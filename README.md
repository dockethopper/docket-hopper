# Docket Hopper

A self-updating site about every bill in the U.S. Congress since 2013 (dockethopper.com).
Repository: `dockethopper/docket-hopper`. No AI anywhere in the pipeline: summaries come from the
Congressional Research Service, odds from a small statistical model, coverage from GDELT counts.

## Data (Python, `ingest/`)

Source: [govinfo BILLSTATUS bulk data](https://www.govinfo.gov/bulkdata/BILLSTATUS), 113th Congress (2013) to now. No API key.

```bash
pip install -r requirements.txt
python -m ingest bulk --congress 113-119   # historic backfill, ~110k bills
python -m ingest sync                      # refetch only bills govinfo changed (current Congress)
python -m ingest textstats --minutes 45    # word counts, newest Congress first (backfills over several nights)
python -m ingest gdelt --max-calls 150     # news coverage for active bills, spaced calls (GDELT throttles hard)
python -m ingest export --out site         # JSON for the site; also records passage-odds bands
python -m ingest stats
python -m pytest tests
```

The database lands in `data/billboard.sqlite` (see `ingest/schema.sql`). Each bill gets a derived
`status` from its action history (`ingest/status.py`).

- `ingest/model.py`: passage-odds model, one logistic regression per stage reached; published as four bands.
- `ingest/network.py`: bill neighborhoods and their layouts, computed at build time and cached in `net_layout`.
- `ingest/gdelt.py`: coverage counts with a match check (name plus sponsor or bill number); hand-kept
  search terms for famous bills live in `ingest/media_aliases.json`.
- `ingest/claims.py`: the sentence titles on each page, with a check that fails the export if a sentence
  quotes a number its data does not contain.

## Site (Astro + D3, `site/`)

```bash
cd site && npm ci
npm test                          # bill-title templates
npx astro build                   # needs `python -m ingest export --out site` first
DH_SAMPLE=100 npx astro build     # quick build with 1% of the pre-rendered bill pages
```

Pages: landing, bill directory (one JSON file per Congress), bill pages, network maps, coverage ledger,
outcomes, about, search. Bill pages for the current Congress and every law or veto are pre-rendered;
other bills, and every bill's map, are drawn in the browser by `404.astro` from the same JSON with the
same code (`src/lib/bill.js`, `src/lib/map.js`).

## Nightly

`.github/workflows/nightly-sync.yml` syncs, backfills, exports, publishes the database to the `data`
release, builds the site and deploys it to GitHub Pages. Pages must be set to deploy from
GitHub Actions (Settings → Pages → Source: GitHub Actions). The base path is read from Pages, so the
site works at `dockethopper.github.io/docket-hopper/` now and at `dockethopper.com` once the custom domain is set.

### Safety net

- **Pre-publish checks.** The job records counts before the sync (`python -m ingest health --save`) and
  compares after the export (`--compare`). If the bill count falls, any Congress shrinks, a law stops
  being a law, summaries or links drop sharply, or the newest action is over 45 days old, the job stops
  before publishing. Yesterday's database and site stay live.
- **Backups.** Each successful night uploads `billboard-YYYY-MM-DD.sqlite.gz` to the `snapshots`
  release, keeping the last 7 nights and the last 8 Sundays. To restore, download one, rename it
  `billboard.sqlite.gz`, and upload it to the `data` release with `--clobber`.
- **Alerts.** A failed nightly run, or a failed daily `site-health` check (site down, site not rebuilt
  in two days, or the repo close to GitHub's 60-day idle cut-off for scheduled jobs), opens an issue
  labelled `alert`. Watching the repo gets you the email.
- **Pull requests.** `checks.yml` runs the Python and site tests plus a sample build (`DH_SAMPLE=50`)
  against the published database. Dependabot opens grouped update PRs monthly.

### Public-site extras

- `site/src/pages/privacy/` and `site/src/pages/terms/`: privacy notice and terms/corrections. Corrections go
  through GitHub issues; there is no contact email yet.
- Visit counts: GoatCounter (cookie-free), only in production builds. Set `DH_GOATCOUNTER=""` to turn it off
  or point it at another counter.
- `@astrojs/sitemap` writes `sitemap-index.xml` for every pre-rendered page; `robots.txt` points to it.
  Older bills render in the browser from the 404 fallback, so they are not in the sitemap.
- `site/public/og.png` is the link-preview image (1200×630) for every page.

### Home-page AI summaries

`python -m ingest summaries --site site` writes two short summaries (latest floor action; recently signed
laws) with Claude Opus 5.5, from the records in those lists only. A check rejects a summary that names a
bill or law number, or uses a figure, not in its input; then yesterday's stays up (for up to 7 days) or the
drop-down hides. Unchanged inputs reuse the last summary, so quiet nights make no API call. Every attempt is
logged in the `ai_summary_log` table. Needs the `ANTHROPIC_API_KEY` Actions secret. The drop-downs stay
hidden (visible with `?preview=summaries`) until the `SUMMARIES_LIVE` repository variable is `1`; the About
page then says they are AI-written.
