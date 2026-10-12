"""Posts matches to a Discord channel through an incoming webhook."""
from __future__ import annotations

import time

EMBEDS_PER_MESSAGE = 10  # Discord's limit
COLORS = {
    "Software": 0x5865F2, "Data / AI": 0x57F287, "IT / Infra": 0xFEE75C, "Security": 0xED4245,
    "QA / Test": 0xEB459E, "Product / BA / Design": 0xF0B232,
}


def build_embed(job, junior: bool, category: str = "") -> dict:
    fields = []
    if category:
        fields.append({"name": "Category", "value": category, "inline": True})
    fields.append({"name": "Location", "value": (job.location or "Not listed")[:1024], "inline": True})
    if job.posted:
        fields.append({"name": "Posted", "value": job.posted, "inline": True})
    return {
        "title": (("🎓 " if junior else "") + job.title)[:256],
        "url": job.url,
        "description": f"**{job.company}**",
        "color": COLORS.get(category, 0x99AAB5),
        "fields": fields,
        "footer": {"text": job.source},
    }


def chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


class Discord:
    def __init__(self, session, webhook_url: str | None, mention: str = ""):
        self.session = session
        self.url = webhook_url
        self.mention = mention

    def _post(self, payload: dict, tries: int = 4) -> bool:
        for _ in range(tries):
            r = self.session.post(self.url, json=payload, timeout=20)
            if r.status_code == 429:  # rate limited: Discord says how long to wait
                time.sleep(float(r.json().get("retry_after", 2)) + 0.25)
                continue
            if r.status_code < 300:
                return True
            print(f"[discord] HTTP {r.status_code}: {r.text[:200]}")
            return False
        return False

    def send_jobs(self, items) -> list:
        """items: (job, junior, category) tuples. Sends in batches of 10 and
        returns the jobs that were delivered, so failures retry next run."""
        delivered = []
        for i, batch in enumerate(chunks(items, EMBEDS_PER_MESSAGE)):
            payload = {"embeds": [build_embed(*it) for it in batch]}
            if i == 0 and self.mention:
                payload["content"] = self.mention
                payload["allowed_mentions"] = {"parse": ["users", "roles", "everyone"]}
            if self._post(payload):
                delivered.extend(it[0] for it in batch)
            time.sleep(1)
        return delivered

    def send_text(self, text: str) -> bool:
        return self._post({"content": text[:2000]})
