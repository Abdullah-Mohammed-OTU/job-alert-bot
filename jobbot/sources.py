"""Fetchers for the public job-board APIs used by most tech companies.

Each fetcher takes a requests-like session and a company dict from config.toml
and yields Job objects. None of these endpoints need an API key.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from urllib.parse import urlparse

TIMEOUT = 20


@dataclass(frozen=True)
class Job:
    uid: str        # globally unique: "<source>:<board>:<job id>"
    company: str
    title: str
    location: str
    url: str
    posted: str     # human-readable; may be empty
    source: str


def _ms_to_date(ms) -> str:
    if not ms:
        return ""
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def _iso_to_date(value) -> str:
    return value[:10] if value else ""


def _join(*parts) -> str:
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return " / ".join(out)


def fetch_greenhouse(session, company):
    slug = company["slug"]
    r = session.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        yield Job(
            uid=f"greenhouse:{slug}:{j['id']}",
            company=company["name"],
            title=j["title"].strip(),
            location=(j.get("location") or {}).get("name", "") or "",
            url=j["absolute_url"],
            posted=_iso_to_date(j.get("first_published") or j.get("updated_at")),
            source="greenhouse",
        )


def fetch_lever(session, company):
    slug = company["slug"]
    r = session.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json():
        cats = j.get("categories") or {}
        remote = "Remote" if (j.get("workplaceType") or "").lower() == "remote" else ""
        yield Job(
            uid=f"lever:{slug}:{j['id']}",
            company=company["name"],
            title=j["text"].strip(),
            location=_join(cats.get("location"), *(cats.get("allLocations") or []), remote),
            url=j["hostedUrl"],
            posted=_ms_to_date(j.get("createdAt")),
            source="lever",
        )


def fetch_ashby(session, company):
    slug = company["slug"]
    r = session.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        secondary = [s.get("location") for s in (j.get("secondaryLocations") or [])]
        yield Job(
            uid=f"ashby:{slug}:{j['id']}",
            company=company["name"],
            title=j["title"].strip(),
            location=_join(j.get("location"), *secondary, "Remote" if j.get("isRemote") else ""),
            url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j['id']}",
            posted=_iso_to_date(j.get("publishedAt")),
            source="ashby",
        )


WORKDAY_DEFAULT_SEARCHES = [
    "new grad", "graduate", "entry level", "junior", "early career", "associate",
    "software", "developer", "data", "IT", "analyst", "security", "technology",
]


def fetch_workday(session, company, page_size=20, max_pages=5):
    """Workday has no public list endpoint, but every careers site calls this
    JSON API from the browser. `url` is the careers page, e.g.
    https://<tenant>.wd3.myworkdayjobs.com/<site>  (drop any /en-US segment)."""
    parsed = urlparse(company["url"])
    host = parsed.netloc
    tenant = host.split(".")[0]
    site = [p for p in parsed.path.split("/") if p and p.lower() not in ("en-us", "en-ca")][0]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    searches = company.get("search", WORKDAY_DEFAULT_SEARCHES)
    if isinstance(searches, str):
        searches = [searches]
    seen = set()
    for search in searches:
        yield from _workday_search(session, host, site, api, company, search, page_size, max_pages, seen)


def _workday_search(session, host, site, api, company, search, page_size, max_pages, seen):
    tenant = host.split(".")[0]
    offset = 0
    for _ in range(max_pages):
        r = session.post(
            api,
            json={"appliedFacets": {}, "limit": page_size, "offset": offset, "searchText": search},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        postings = data.get("jobPostings", [])
        for j in postings:
            path = j["externalPath"]
            if path in seen:
                continue
            seen.add(path)
            yield Job(
                uid=f"workday:{tenant}/{site}:{path}",
                company=company["name"],
                title=j["title"].strip(),
                location=j.get("locationsText", "") or "",
                url=f"https://{host}/{site}{path}",
                posted=j.get("postedOn", "") or "",
                source="workday",
            )
        offset += page_size
        if not postings or offset >= data.get("total", 0):
            break


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
}


def board_key(company) -> str:
    """Stable id for a board, used to know whether it has been seeded yet."""
    return f"{company['source']}:{company.get('slug') or company.get('url')}"
