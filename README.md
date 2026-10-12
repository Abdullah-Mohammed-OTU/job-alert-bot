# New-Grad Job Alert Bot

Polls company job boards directly and posts new entry-level tech roles to a Discord channel, covering software, data/AI, IT, security, QA and product/BA/design. Alerts usually arrive within 15 minutes of them going live. That's typically hours before they show up on LinkedIn or in the aggregator repos.

- **Sources:** Greenhouse, Lever, Ashby and Workday public job APIs. No scraping, no API keys.
- **Categories:** every alert is tagged and colour-coded in Discord:

  | Category | Examples |
  |---|---|
  | Software | Software Engineer, Web Developer |
  | Data / AI | Data Analyst, Data Scientist, BI Developer, ML Engineer |
  | IT / Infra | IT Support, Service Desk Tier 1, Sysadmin, Cloud, SRE |
  | Security | Cybersecurity Analyst, SOC Analyst |
  | QA / Test | QA Analyst, SDET |
  | Product / BA / Design | Associate PM, Business Systems Analyst, Technology Analyst, UX |

  You can switch categories on or off in `config.toml`.
- **Filters:** drops senior, staff, lead, manager, II/III, Tier 2, intern and co-op roles. Also drops non-tech jobs that share keywords, such as mechanical engineer, security guard and pharmacy technician. Locations are Canada, US or remote; anything clearly overseas is dropped.
- **No spam:** the first time it sees a board it silently records what's already posted, so you only hear about postings that appear after that. Titles with an explicit new-grad signal (new grad, junior, associate, "Engineer I", 2026/2027) get a 🎓 and are sorted first.
- **Reliable:** one dead board never stops the others. Failed Discord deliveries retry on the next run. Rate limits are handled. Each run is capped so a bad filter can't flood the channel.

## 1. Create the Discord webhook

1. In your Discord server, make a channel (e.g. `#job-alerts`).
2. Open **Edit Channel → Integrations → Webhooks → New Webhook**.
3. Click **Copy Webhook URL**. Treat it like a password; anyone with it can post to the channel.

## 2. Try it locally

```bash
pip install -r requirements.txt
python bot.py --check      # are all the boards in config.toml reachable?
python bot.py --dry-run    # what would match right now? (tune filters here)

export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python bot.py              # first run: seeds silently
python bot.py              # later runs: posts anything new
python -m pytest -q        # 80 tests, no network needed
```

On Windows PowerShell, use `$env:DISCORD_WEBHOOK_URL="..."` instead of `export`.

## 3. Deploy

### Option A: GitHub Actions (free, no server)

1. Push this folder to a new GitHub repo.
2. Go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `DISCORD_WEBHOOK_URL` and paste the webhook URL.
3. Go to **Actions → Poll job boards → Run workflow** once to seed.

It then runs every 15 minutes and commits `state/seen.json` back to the repo so it remembers what it has posted.

- **Public repo:** Actions minutes are unlimited.
- **Private repo:** you get 2,000 free minutes a month. Every 15 minutes is roughly 2,900 runs, which is too many, so change the cron to every 30 minutes.
- Nothing secret lives in the repo; the webhook URL is only in the secret.

### Option B: Docker on any always-on machine

```bash
cp .env.example .env       # paste your webhook URL
docker compose up -d --build
docker compose logs -f
```

It polls every ~10 minutes, with a little random jitter. `config.toml` is mounted into the container, so after editing companies you only need `docker compose restart`.

## 4. Add the companies you actually want

`config.toml` ships with 30 Canadian employers whose boards were confirmed on Oct 12, 2026: 18 on Workday, 8 on Ashby and 4 on Greenhouse. To add more, find a company's board, open any job posting from it and look at the URL:

| URL looks like | source | what to put in config |
|---|---|---|
| `job-boards.greenhouse.io/acme/...` or `boards.greenhouse.io/acme` | `greenhouse` | `slug = "acme"` |
| `jobs.lever.co/acme/...` | `lever` | `slug = "acme"` |
| `jobs.ashbyhq.com/acme/...` | `ashby` | `slug = "acme"` |
| `acme.wd3.myworkdayjobs.com/en-US/AcmeCareers/...` | `workday` | `url = "https://acme.wd3.myworkdayjobs.com/AcmeCareers"` |

Workday is what most Canadian banks, insurers, telecoms and large enterprises use. These are where a lot of new-grad IT, data and analyst roles are posted. The bot runs a set of tech and new-grad searches on each Workday board automatically.

Some companies run their own careers site (Shopify, Amazon, Google, Microsoft). These need a custom fetcher. Add a function to `jobbot/sources.py` and register it in `FETCHERS`.

Run `python bot.py --check` after editing.

## Tuning

All of this is set in `config.toml`:

- `categories = [...]`: which kinds of roles to alert on.
- `include_us = false`: Canada plus roles that say "remote" only.
- `require_junior_keyword = true`: only alert on explicit new-grad / junior / associate / "Analyst I" / Tier 1 titles. Recommended once you add a lot of companies. Less noise, but you'll miss roles titled just "Data Analyst".
- `mention = "<@your-user-id>"`: get a push notification ping on each batch.
- Override `exclude_pattern` or any category under `[settings.category_patterns]` with your own regex. The defaults are in `jobbot/filters.py`.

## Layout

```
bot.py               entry point / CLI
jobbot/sources.py    one fetcher per ATS -> Job objects
jobbot/filters.py    tech categories, title + location rules
jobbot/state.py      seen-postings JSON (dedupe, pruned after 45 days)
jobbot/discord.py    webhook embeds, batching, 429 handling
config.toml          settings + company list
tests/               pytest suite with recorded-style API payloads
```
