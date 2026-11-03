"""Billboard ingestion CLI.

    python -m ingest bulk  --congress 113-119        # historic backfill from govinfo zips
    python -m ingest sync                             # nightly: refetch changed bills (current Congress)
    python -m ingest sync --congress 118-119
    python -m ingest textstats --limit 5000           # word counts for "longest/shortest" sort
    python -m ingest stats                            # quick summary of what is in the DB
    python -m ingest gdelt --max-calls 150            # news coverage for active bills, spaced calls
    python -m ingest export --out site                # JSON for the static site (also records odds history)
    python -m ingest health --save before.json        # record counts before the nightly sync
    python -m ingest health --compare before.json     # block publishing if data shrank or went stale
    python -m ingest snapshots --today 2026-11-04 NAME ...   # print backup names that can be deleted
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import db, govinfo, textstats

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "billboard.sqlite"


def congress_range(spec: str | None) -> list[int]:
    if not spec:
        return [govinfo.current_congress()]
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",")]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="ingest")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bulk")
    b.add_argument("--congress", default=f"{govinfo.FIRST_CONGRESS}-{govinfo.current_congress()}")
    b.add_argument("--types", default=",".join(govinfo.BILL_TYPES))
    b.add_argument("--cache", default=str(ROOT / "data" / "raw"))
    b.add_argument("--keep-zip", action="store_true")

    s = sub.add_parser("sync")
    s.add_argument("--congress")
    s.add_argument("--types", default=",".join(govinfo.BILL_TYPES))
    s.add_argument("--workers", type=int, default=8)

    t = sub.add_parser("textstats")
    t.add_argument("--limit", type=int)
    t.add_argument("--workers", type=int, default=8)
    t.add_argument("--minutes", type=float, help="stop after this long (nightly backfill budget)")
    t.add_argument("--keep-text", action="store_true", help="save full text of enacted/vetoed bills")

    sub.add_parser("stats")

    g = sub.add_parser("gdelt")
    g.add_argument("--max-calls", type=int, default=150)
    g.add_argument("--spacing", type=float, default=20.0, help="seconds between GDELT calls")
    g.add_argument("--minutes", type=float, default=55.0, help="stop after this long")

    e = sub.add_parser("export")
    e.add_argument("--out", default=str(ROOT / "site"))
    e.add_argument("--workers", type=int)

    h = sub.add_parser("health")
    h.add_argument("--save")
    h.add_argument("--compare")

    sn = sub.add_parser("snapshots")
    sn.add_argument("--today", required=True)
    sn.add_argument("names", nargs="*")

    args = ap.parse_args(argv)
    if args.cmd == "snapshots":
        from . import health
        print("\n".join(health.snapshots_to_delete(args.names, args.today)))
        return

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = db.connect(args.db)

    if args.cmd == "bulk":
        for c in congress_range(args.congress):
            for bt in args.types.split(","):
                govinfo.bulk(conn, c, bt, Path(args.cache), keep_zip=args.keep_zip)
    elif args.cmd == "sync":
        for c in congress_range(args.congress):
            for bt in args.types.split(","):
                govinfo.sync(conn, c, bt, workers=args.workers)
    elif args.cmd == "textstats":
        textstats.run(conn, limit=args.limit, workers=args.workers, minutes=args.minutes,
                      keep_text_dir=(ROOT / "data" / "text") if args.keep_text else None)
    elif args.cmd == "gdelt":
        from . import gdelt
        cur = conn.execute("SELECT MAX(congress) FROM bills").fetchone()[0]
        gdelt.run(conn, cur, max_calls=args.max_calls, spacing=args.spacing, minutes=args.minutes)
    elif args.cmd == "export":
        from . import export
        export.run(conn, Path(args.out), workers=args.workers)
    elif args.cmd == "health":
        from . import health
        if args.save:
            c = health.save(conn, Path(args.save))
            print(f"health: saved counts ({c['bills']:,} bills, {c['laws']:,} laws, data through {c['data_date']})")
        if args.compare:
            problems = health.check(conn, Path(args.compare))
            for p in problems:
                print("::error title=Pre-publish check failed::" + p)
            if problems:
                raise SystemExit(1)
            print("health: all pre-publish checks passed")
    elif args.cmd == "stats":
        print("bills:", conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0])
        for r in conn.execute("SELECT congress, COUNT(*) n FROM bills GROUP BY congress ORDER BY congress"):
            print(f"  {r['congress']}th: {r['n']}")
        for r in conn.execute("SELECT status, COUNT(*) n FROM bills GROUP BY status ORDER BY n DESC"):
            print(f"  {r['status']}: {r['n']}")
        print("relationship edges:", conn.execute("SELECT COUNT(*) FROM related_bills").fetchone()[0])
        print("with word count:", conn.execute("SELECT COUNT(*) FROM bills WHERE word_count IS NOT NULL").fetchone()[0])


if __name__ == "__main__":
    main()
