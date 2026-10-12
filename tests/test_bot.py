import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
from jobbot import discord as discord_mod  # noqa: E402
from jobbot.filters import Filter  # noqa: E402
from jobbot.sources import Job, fetch_ashby, fetch_greenhouse, fetch_lever, fetch_workday  # noqa: E402
from jobbot.state import State  # noqa: E402

# --- fake HTTP --------------------------------------------------------------

class Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status
        self.text = json.dumps(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Routes GETs by URL substring; records POSTs (Discord + Workday)."""
    def __init__(self, routes=None, post_routes=None, webhook_status=None):
        self.routes = routes or {}
        self.post_routes = post_routes or {}
        self.webhook_status = list(webhook_status or [])
        self.webhook_posts = []

    def get(self, url, timeout=None):
        for k, v in self.routes.items():
            if k in url:
                return v if isinstance(v, Resp) else Resp(v)
        return Resp({"error": "not found"}, 404)

    def post(self, url, json=None, timeout=None):
        if "discord" in url:
            self.webhook_posts.append(json)
            if self.webhook_status:
                code = self.webhook_status.pop(0)
                return Resp({"retry_after": 0.01}, code)
            return Resp({}, 204)
        for k, pages in self.post_routes.items():
            if k in url:
                return Resp(pages[json["offset"] // json["limit"]])
        return Resp({}, 404)


GREENHOUSE = {"jobs": [
    {"id": 1, "title": "Software Engineer, New Grad (2026)", "absolute_url": "https://gh/1",
     "location": {"name": "Toronto, ON"}, "first_published": "2026-10-12T09:00:00Z"},
    {"id": 2, "title": "Senior Software Engineer", "absolute_url": "https://gh/2",
     "location": {"name": "Toronto, ON"}},
    {"id": 3, "title": "Software Engineer", "absolute_url": "https://gh/3",
     "location": {"name": "London, UK"}},
    {"id": 4, "title": "Account Executive", "absolute_url": "https://gh/4",
     "location": {"name": "Remote"}},
]}
LEVER = [
    {"id": "abc", "text": "Software Engineer I", "hostedUrl": "https://lever/abc",
     "categories": {"location": "Remote, US", "allLocations": ["Remote, US"]},
     "workplaceType": "remote", "createdAt": 1791900000000},
    {"id": "def", "text": "Software Engineering Intern", "hostedUrl": "https://lever/def",
     "categories": {"location": "Waterloo, ON"}},
]
ASHBY = {"jobs": [
    {"id": "x1", "title": "Associate Software Developer", "location": "Bengaluru",
     "secondaryLocations": [{"location": "Toronto"}], "jobUrl": "https://ashby/x1",
     "publishedAt": "2026-10-11T00:00:00Z", "isListed": True},
    {"id": "x2", "title": "Software Engineer", "location": "Bengaluru, India", "jobUrl": "https://ashby/x2"},
    {"id": "x3", "title": "Junior Developer", "location": "NYC", "isListed": False},
]}
WORKDAY_PAGES = [
    {"total": 21, "jobPostings": [
        {"title": "Developer I - Digital", "externalPath": "/job/Toronto/Dev-I_R1",
         "locationsText": "Toronto, ON", "postedOn": "Posted Today"},
        {"title": "Lead Developer", "externalPath": "/job/Toronto/Lead_R2", "locationsText": "Toronto, ON"},
    ]},
    {"total": 21, "jobPostings": [
        {"title": "Graduate Software Developer", "externalPath": "/job/Ottawa/Grad_R3",
         "locationsText": "2 Locations", "postedOn": "Posted 2 Days Ago"},
    ]},
]

COMPANIES = [
    {"name": "Acme", "source": "greenhouse", "slug": "acme"},
    {"name": "Lev", "source": "lever", "slug": "lev"},
    {"name": "Ash", "source": "ashby", "slug": "ash"},
    {"name": "Bank", "source": "workday", "url": "https://bank.wd3.myworkdayjobs.com/en-US/Careers", "search": "dev"},
]


def session_all(**kw):
    return FakeSession(
        routes={"greenhouse.io/v1/boards/acme": GREENHOUSE, "lever.co/v0/postings/lev": LEVER,
                "ashbyhq.com/posting-api/job-board/ash": ASHBY},
        post_routes={"/wday/cxs/bank/Careers/jobs": WORKDAY_PAGES},
        **kw,
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(discord_mod.time, "sleep", lambda s: None)


# --- parsers ----------------------------------------------------------------

def test_greenhouse_parse():
    jobs = list(fetch_greenhouse(session_all(), COMPANIES[0]))
    assert [j.uid for j in jobs][:2] == ["greenhouse:acme:1", "greenhouse:acme:2"]
    assert jobs[0].location == "Toronto, ON" and jobs[0].posted == "2026-10-12"


def test_lever_parse_dedupes_locations_and_adds_remote():
    j = list(fetch_lever(session_all(), COMPANIES[1]))[0]
    assert j.location == "Remote, US / Remote"
    assert j.posted.startswith("2026-")


def test_ashby_parse_secondary_and_unlisted():
    jobs = list(fetch_ashby(session_all(), COMPANIES[2]))
    assert [j.uid for j in jobs] == ["ashby:ash:x1", "ashby:ash:x2"]
    assert jobs[0].location == "Bengaluru / Toronto"


def test_workday_paginates_and_builds_urls():
    jobs = list(fetch_workday(session_all(), COMPANIES[3]))
    assert len(jobs) == 3
    assert jobs[0].url == "https://bank.wd3.myworkdayjobs.com/Careers/job/Toronto/Dev-I_R1"


# --- filters ----------------------------------------------------------------

F = Filter.from_settings({})


@pytest.mark.parametrize("title,ok", [
    ("Software Engineer, New Grad", True),
    ("Software Engineer", True),
    ("Software Engineer I", True),
    ("Software Engineer II", False),
    ("Senior Software Engineer", False),
    ("Sr. Backend Developer", False),
    ("Staff Engineer", False),
    ("Software Engineering Intern", False),
    ("Co-op Software Developer", False),
    ("Internal Tools Engineer", True),
    ("Sales Engineer", False),
    ("Account Executive", False),
    ("Software Engineer, 2027 New Grad", True),
    ("Engineering Manager", False),
])
def test_titles(title, ok):
    assert F.title_ok(title) is ok


@pytest.mark.parametrize("loc,ok", [
    ("Toronto, ON", True), ("Remote - Canada", True), ("Toronto / London", True),
    ("London, UK", False), ("Bengaluru, India", False), ("Remote - EMEA", False),
    ("New York, NY", True), ("Remote", True), ("", True),
    ("Based on site in Dublin", False),
])
def test_locations_default(loc, ok):
    assert F.location_ok(loc) is ok


@pytest.mark.parametrize("title,cat", [
    # software
    ("Software Engineer, New Grad", "Software"),
    ("Junior Web Developer", "Software"),
    ("Technology Development Program 2027", "Software"),
    # data / AI
    ("Data Analyst", "Data / AI"),
    ("Junior Data Scientist", "Data / AI"),
    ("Data Engineer I", "Data / AI"),
    ("Machine Learning Engineer, New Grad", "Data / AI"),
    ("Business Intelligence Developer", "Data / AI"),
    ("Reporting Analyst", "Data / AI"),
    # IT / infra
    ("IT Support Specialist", "IT / Infra"),
    ("Service Desk Analyst - Tier 1", "IT / Infra"),
    ("Systems Administrator", "IT / Infra"),
    ("Junior Cloud Engineer", "IT / Infra"),
    ("Network Technician", "IT / Infra"),
    ("Site Reliability Engineer, New Grad", "IT / Infra"),
    ("I&IT Technical Specialist", "IT / Infra"),
    # security
    ("Cybersecurity Analyst I", "Security"),
    ("Security Engineer, Early Career", "Security"),
    ("SOC Analyst", "Security"),
    # QA
    ("QA Analyst", "QA / Test"),
    ("Software Developer in Test", "QA / Test"),
    ("Test Automation Engineer", "QA / Test"),
    # product / BA / design
    ("Associate Product Manager", "Product / BA / Design"),
    ("Business Systems Analyst", "Product / BA / Design"),
    ("Technology Analyst, Graduate Program", "Product / BA / Design"),
    ("UX Designer, New Grad", "Product / BA / Design"),
    # not tech, or not entry level
    ("Financial Analyst", None),
    ("Product Manager", None),
    ("IT Manager", None),
    ("Security Guard", None),
    ("Pharmacy Technician", None),
    ("Mechanical Engineer, New Grad", None),
    ("Process Engineer", None),
    ("Customer Success Manager", None),
    ("Level 2 Service Desk Analyst", None),
    ("Senior Data Scientist", None),
    ("Data Science Intern", None),
    ("Marketing Coordinator", None),
    ("Registered Nurse", None),
])
def test_categories(title, cat):
    assert F.category(title) == cat


def test_category_toggle():
    f = Filter.from_settings({"categories": ["data", "security"]})
    assert f.category("Data Analyst") == "Data / AI"
    assert f.category("IT Support Specialist") is None
    # Security Engineer stays Security even with software off
    assert f.category("Security Engineer") == "Security"
    with pytest.raises(SystemExit):
        Filter.from_settings({"categories": ["sofware"]})


def test_embed_has_category_field_and_color():
    job = Job("u", "C", "Data Analyst", "Toronto", "https://x", "", "greenhouse")
    e = discord_mod.build_embed(job, False, "Data / AI")
    assert e["fields"][0] == {"name": "Category", "value": "Data / AI", "inline": True}
    assert e["color"] == discord_mod.COLORS["Data / AI"]


def test_workday_multiple_searches_dedupe():
    s = session_all()
    c = dict(COMPANIES[3]); c.pop("search")
    jobs = list(fetch_workday(s, c))
    assert len(jobs) == len({j.uid for j in jobs}) == 3


def test_canada_only_mode():
    f = Filter.from_settings({"include_us": False})
    assert f.location_ok("Toronto, ON") and f.location_ok("Remote")
    assert not f.location_ok("Seattle, WA")


def test_require_junior_keyword():
    f = Filter.from_settings({"require_junior_keyword": True})
    assert not f.title_ok("Software Engineer")
    assert f.title_ok("Junior Software Developer")


# --- end to end -------------------------------------------------------------

def cfg():
    return {"settings": {}, "companies": COMPANIES}


def test_first_run_seeds_silently_then_alerts_only_new(tmp_path):
    state_path = tmp_path / "seen.json"
    s = session_all()
    d = discord_mod.Discord(s, "https://discord.com/api/webhooks/x", "")

    assert bot.run_once(cfg(), s, State(state_path), d) == []
    assert s.webhook_posts == []

    # A new posting appears on one board.
    GREENHOUSE["jobs"].append({"id": 99, "title": "Junior Software Developer", "absolute_url": "https://gh/99",
                               "location": {"name": "Remote - Canada"}})
    GREENHOUSE["jobs"].append({"id": 100, "title": "Director of Engineering", "absolute_url": "https://gh/100",
                               "location": {"name": "Toronto"}})
    try:
        new = bot.run_once(cfg(), s, State(state_path), d)
        assert [j.uid for j in new] == ["greenhouse:acme:99"]
        assert len(s.webhook_posts) == 1
        embed = s.webhook_posts[0]["embeds"][0]
        assert embed["title"].startswith("🎓") and embed["url"] == "https://gh/99"

        # Third run: nothing new, nothing sent.
        assert bot.run_once(cfg(), s, State(state_path), d) == []
        assert len(s.webhook_posts) == 1
    finally:
        del GREENHOUSE["jobs"][-2:]


def test_new_board_added_later_is_seeded_not_spammed(tmp_path):
    state_path = tmp_path / "seen.json"
    s = session_all()
    d = discord_mod.Discord(s, "https://discord.com/x", "")
    bot.run_once({"settings": {}, "companies": COMPANIES[:1]}, s, State(state_path), d)
    bot.run_once(cfg(), s, State(state_path), d)
    assert s.webhook_posts == []


def test_failed_delivery_is_retried_next_run(tmp_path):
    state_path = tmp_path / "seen.json"
    st = State(state_path)
    st.seeded = {"greenhouse:acme"}
    st.save()
    s = session_all(webhook_status=[500])
    d = discord_mod.Discord(s, "https://discord.com/x", "")
    one = {"settings": {}, "companies": COMPANIES[:1]}

    first = bot.run_once(one, s, State(state_path), d)
    assert first and not s.webhook_posts[1:]          # one failed attempt
    second = bot.run_once(one, s, State(state_path), d)
    assert [j.uid for j in second] == [j.uid for j in first]  # still unseen -> resent
    assert len(s.webhook_posts) == 2


def test_rate_limit_is_retried():
    s = FakeSession(webhook_status=[429])
    d = discord_mod.Discord(s, "https://discord.com/x")
    job = Job("u", "C", "Software Engineer", "Toronto", "https://x", "", "greenhouse")
    assert d.send_jobs([(job, False, "Software")]) == [job]
    assert len(s.webhook_posts) == 2


def test_batches_of_ten_and_cap(tmp_path):
    jobs = {"jobs": [{"id": i, "title": f"New Grad Software Engineer {chr(65 + i % 26)}{i}",
                      "absolute_url": f"https://gh/{i}", "location": {"name": "Toronto"}} for i in range(55)]}
    st = State(tmp_path / "s.json")
    st.seeded = {"greenhouse:big"}
    s = FakeSession(routes={"boards/big": jobs})
    d = discord_mod.Discord(s, "https://discord.com/x")
    c = {"settings": {"max_alerts_per_run": 25}, "companies": [{"name": "Big", "source": "greenhouse", "slug": "big"}]}
    bot.run_once(c, s, st, d)
    sizes = [len(p.get("embeds", [])) for p in s.webhook_posts]
    assert sizes == [10, 10, 5, 0]                       # 25 alerts + 1 overflow note
    assert "30 more" in s.webhook_posts[-1]["content"]
    assert len(State(tmp_path / "s.json").seen) == 55


def test_broken_board_does_not_stop_others(tmp_path, capsys):
    st = State(tmp_path / "s.json")
    s = session_all()
    companies = [{"name": "Gone", "source": "greenhouse", "slug": "doesnotexist"}] + COMPANIES
    bot.run_once({"settings": {}, "companies": companies}, s, st, discord_mod.Discord(s, "https://discord.com/x"))
    assert "Gone" in capsys.readouterr().err
    assert "greenhouse:acme" in st.seeded and "greenhouse:doesnotexist" not in st.seeded


def test_dry_run_lists_current_matches(tmp_path):
    hits = bot.run_once(cfg(), session_all(), State(tmp_path / "s.json"), None, dry_run=True)
    titles = sorted(j.title for j in hits)
    assert titles == sorted([
        "Software Engineer, New Grad (2026)", "Software Engineer I", "Associate Software Developer",
        "Developer I - Digital", "Graduate Software Developer",
    ])
    assert not (tmp_path / "s.json").exists()


def test_config_file_loads():
    c = bot.load_config(Path(bot.ROOT) / "config.toml")
    assert len(c["companies"]) == 30
