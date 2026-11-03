# Docket Hopper

A self-updating site about every bill in the U.S. Congress since 2013 (dockethopper.com).
Repository name: `billdistill`. No AI anywhere in the pipeline: summaries come from the
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
site works at `zuzwan.github.io/billdistill/` now and at `dockethopper.com` once the custom domain is set.
