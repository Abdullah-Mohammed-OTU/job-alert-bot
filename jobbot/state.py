"""Remembers which postings were already announced, in a small JSON file.

JSON rather than SQLite so the GitHub Actions deploy can commit it back to the
repo between runs.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

PRUNE_AFTER_DAYS = 45


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        data = {}
        if self.path.exists():
            data = json.loads(self.path.read_text() or "{}")
        self.seen: dict[str, str] = data.get("seen", {})          # uid -> last seen date
        self.seeded: set[str] = set(data.get("seeded_boards", []))

    @staticmethod
    def today() -> str:
        return dt.date.today().isoformat()

    def is_new(self, uid: str) -> bool:
        return uid not in self.seen

    def mark(self, uid: str) -> None:
        self.seen[uid] = self.today()

    def touch(self, uids) -> None:
        """Refresh last-seen for postings that are still live."""
        t = self.today()
        for u in uids:
            if u in self.seen:
                self.seen[u] = t

    def prune(self) -> None:
        cutoff = (dt.date.today() - dt.timedelta(days=PRUNE_AFTER_DAYS)).isoformat()
        self.seen = {u: d for u, d in self.seen.items() if d >= cutoff}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"seeded_boards": sorted(self.seeded), "seen": dict(sorted(self.seen.items()))},
            indent=1,
        ))
        os.replace(tmp, self.path)
