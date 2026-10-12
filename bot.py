#!/usr/bin/env python3
"""New-grad job alert bot.

Polls company job boards (Greenhouse, Lever, Ashby, Workday), keeps postings
that look like entry-level tech roles (software, data/AI, IT, security, QA,
product/BA/design) in Canada/US/remote, and posts the
ones it hasn't seen before to a Discord channel.

    python bot.py              # one pass (what cron / GitHub Actions runs)
    python bot.py --loop 10    # run forever, every ~10 minutes (Docker / a server)
    python bot.py --dry-run    # print matches, don't post or save
    python bot.py --check      # verify every board in config.toml responds
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from jobbot.discord import Discord
from jobbot.filters import Filter
from jobbot.sources import FETCHERS, board_key
from jobbot.state import State

ROOT = Path(__file__).resolve().parent


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    for c in cfg.get("companies", []):
        if c.get("source") not in FETCHERS:
            raise SystemExit(f"config: {c.get('name')!r} has unknown source {c.get('source')!r}")
        if c["source"] == "workday" and "url" not in c:
            raise SystemExit(f"config: workday company {c['name']!r} needs a url")
        if c["source"] != "workday" and "slug" not in c:
            raise SystemExit(f"config: {c['name']!r} needs a slug")
    return cfg


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "new-grad-job-alert-bot/1.0 (personal use)"
    return s


def fetch_all(session, companies, workers=8):
    """Returns {board_key: list[Job] | Exception}. One broken board never stops the rest."""
    def one(c):
        try:
            return board_key(c), list(FETCHERS[c["source"]](session, c))
        except Exception as e:  # noqa: BLE001
            return board_key(c), e

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(one, companies))


def run_once(cfg, session, state: State, discord: Discord | None, dry_run=False) -> list:
    settings = cfg.get("settings", {})
    flt = Filter.from_settings(settings)
    companies = cfg.get("companies", [])
    max_alerts = int(settings.get("max_alerts_per_run", 40))

    results = fetch_all(session, companies)

    if dry_run or discord is None:
        # Ignore state entirely: show everything that passes the filters right
        # now, so you can tune config.toml before going live.
        hits = []
        for c in companies:
            res = results[board_key(c)]
            if isinstance(res, Exception):
                print(f"[warn] {c['name']} ({c['source']}): {res}", file=sys.stderr)
                continue
            hits += [j for j in res if flt.matches(j)]
        hits.sort(key=lambda j: (not flt.is_junior(j.title), j.company))
        for j in hits:
            mark = "🎓" if flt.is_junior(j.title) else "  "
            print(f"{mark} {flt.category(j.title):<22} {j.company:<18} {j.title}  [{j.location}]  {j.url}")
        print(f"\n{len(hits)} posting(s) currently pass the filters. Dry run: nothing posted or saved.")
        return hits

    new_matches, seeded_now, errors = [], [], []

    for c in companies:
        key = board_key(c)
        res = results[key]
        if isinstance(res, Exception):
            errors.append(f"{c['name']} ({c['source']}): {res}")
            continue
        state.touch(j.uid for j in res)
        first_time = key not in state.seeded
        for job in res:
            if not state.is_new(job.uid):
                continue
            if first_time:
                # Brand-new board: remember everything that's already up so we
                # only alert on postings that appear from now on.
                state.mark(job.uid)
            elif flt.matches(job):
                new_matches.append(job)
            else:
                state.mark(job.uid)  # seen, not interesting
        if first_time:
            state.seeded.add(key)
            seeded_now.append(c["name"])

    if seeded_now:
        print(f"Seeded {len(seeded_now)} new board(s) silently: {', '.join(seeded_now)}")
    for e in errors:
        print(f"[warn] {e}", file=sys.stderr)

    to_send, overflow = new_matches[:max_alerts], new_matches[max_alerts:]
    flagged = [(j, flt.is_junior(j.title), flt.category(j.title)) for j in to_send]
    # Explicit new-grad titles first, then grouped by category.
    flagged.sort(key=lambda p: (not p[1], p[2]))

    delivered = discord.send_jobs(flagged) if flagged else []
    for j in delivered:
        state.mark(j.uid)
    if overflow:
        names = sorted({j.company for j in overflow})
        if discord.send_text(f"…and {len(overflow)} more matches (capped this run) at: {', '.join(names)}"):
            for j in overflow:
                state.mark(j.uid)
    state.prune()
    state.save()
    print(f"{len(new_matches)} new match(es), {len(delivered)} posted, {len(errors)} board error(s).")
    return new_matches


def check(cfg, session) -> int:
    results = fetch_all(session, cfg.get("companies", []))
    bad = 0
    for c in cfg.get("companies", []):
        res = results[board_key(c)]
        if isinstance(res, Exception):
            bad += 1
            print(f"FAIL  {c['name']:<20} {c['source']:<10} {res}")
        else:
            print(f"ok    {c['name']:<20} {c['source']:<10} {len(res)} open postings")
    print(f"\n{len(results) - bad}/{len(results)} boards reachable.")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.environ.get("JOBBOT_CONFIG", ROOT / "config.toml"), type=Path)
    ap.add_argument("--state", default=os.environ.get("JOBBOT_STATE", ROOT / "state" / "seen.json"), type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--loop", type=float, metavar="MINUTES", help="keep running, polling every N minutes")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    session = make_session()
    if args.check:
        return check(cfg, session)

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook and not args.dry_run:
        print("DISCORD_WEBHOOK_URL is not set. Set it, or use --dry-run.", file=sys.stderr)
        return 2
    discord = None if args.dry_run else Discord(session, webhook, cfg.get("settings", {}).get("mention", ""))

    while True:
        state = State(args.state)
        try:
            run_once(cfg, session, state, discord, dry_run=args.dry_run)
        except Exception as e:  # noqa: BLE001  keep the loop alive
            print(f"[error] run failed: {e}", file=sys.stderr)
            if not args.loop:
                raise
        if not args.loop:
            return 0
        # Jitter so requests don't land on the same second every time.
        time.sleep(args.loop * 60 * random.uniform(0.85, 1.15))


if __name__ == "__main__":
    sys.exit(main())
