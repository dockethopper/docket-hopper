"""Billboard ingestion CLI.

    python -m ingest bulk  --congress 113-119        # historic backfill from govinfo zips
    python -m ingest sync                             # nightly: refetch changed bills (current Congress)
    python -m ingest sync --congress 118-119
    python -m ingest textstats --limit 5000           # word counts for "longest/shortest" sort
    python -m ingest stats                            # quick summary of what is in the DB
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
    t.add_argument("--keep-text", action="store_true", help="save full text of enacted/vetoed bills")

    sub.add_parser("stats")

    args = ap.parse_args(argv)
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
        textstats.run(conn, limit=args.limit, workers=args.workers, keep_text_dir=(ROOT / "data" / "text") if args.keep_text else None)
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
