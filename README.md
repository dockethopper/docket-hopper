# Billboard

A self-updating site about bills in the U.S. Congress.

## Stage 1: data

Source: [govinfo BILLSTATUS bulk data](https://www.govinfo.gov/bulkdata/BILLSTATUS), which covers the
113th Congress (2013) to now. No API key needed.

```bash
python -m ingest bulk --congress 113-119   # historic backfill, ~100k bills
python -m ingest textstats                 # word counts (for longest/shortest sorting)
python -m ingest sync                      # refetch only bills govinfo changed (current Congress)
python -m ingest stats
python -m pytest tests
```

The database lands in `data/billboard.sqlite` (see `ingest/schema.sql`). Each bill gets a derived
`status` (became_law, vetoed, veto_overridden, pocket_vetoed, passed_both, passed_one, reported,
introduced, ...) from its action history; see `ingest/status.py`.

`related_bills` holds the edges for the network map. `media_daily` is reserved for news coverage
(GDELT), added in a later stage.

## Auto-update

`.github/workflows/nightly-sync.yml` runs daily, pulls the database from the `data` release, syncs
changes, and publishes it back.

## Roadmap

1. Data ingestion (this stage)
2. Site: landing, bill directory, bill page, network map, media balance sheet, outcomes
3. News coverage (GDELT) and AI summaries / effect predictions for active bills
4. Design pass using the deck design guide (Source Serif 4, IBM Plex Sans/Condensed/Mono)
