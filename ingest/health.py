"""Pre-publish checks for the nightly job, plus the backup-rotation rule.

The nightly job records counts before it touches the database, then compares after the
export. If anything looks like data loss, the job stops before publishing, so yesterday's
database release and yesterday's site stay live.

    python -m ingest health --save before.json        # before sync
    python -m ingest health --compare before.json     # after export; exit 1 on a problem
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

# Tolerances. Bills are never deleted from Congress.gov, so the totals should only grow.
# A small drop in CRS summaries or related-bill links can be a real correction on govinfo.
SUMMARY_DROP = 0.01
LINK_DROP = 0.02
STALE_DAYS = 45   # longest normal gap with no recorded action in the current Congress (August recess)


def counts(conn) -> dict:
    one = lambda s: conn.execute(s).fetchone()[0]
    return {
        "bills": one("SELECT COUNT(*) FROM bills"),
        "laws": one("SELECT COUNT(*) FROM bills WHERE status IN ('became_law','veto_overridden')"),
        "vetoes": one("SELECT COUNT(*) FROM bills WHERE status IN ('vetoed','pocket_vetoed','veto_overridden')"),
        "summaries": one("SELECT COUNT(*) FROM bills WHERE summary IS NOT NULL AND summary != ''"),
        "links": one("SELECT COUNT(*) FROM related_bills"),
        "law_ids": [r[0] for r in conn.execute("SELECT bill_id FROM bills WHERE status IN ('became_law','veto_overridden') ORDER BY 1")],
        "by_congress": {str(r[0]): r[1] for r in conn.execute("SELECT congress, COUNT(*) FROM bills GROUP BY 1")},
        "data_date": (one("SELECT MAX(latest_action_date) FROM bills") or "")[:10],
    }


def compare(before: dict, after: dict, today: str | None = None) -> list[str]:
    """Problems that should block publishing. Empty list means safe to publish."""
    p = []
    if after["bills"] < before["bills"]:
        p.append(f"bill count fell from {before['bills']:,} to {after['bills']:,}")
    for c, n in before["by_congress"].items():
        if after["by_congress"].get(c, 0) < n:
            p.append(f"{c}th Congress fell from {n:,} to {after['by_congress'].get(c, 0):,} bills")
    gone = sorted(set(before["law_ids"]) - set(after["law_ids"]))
    if gone:
        p.append(f"{len(gone)} enacted bill(s) no longer marked as law: {', '.join(gone[:10])}")
    if after["vetoes"] < before["vetoes"]:
        p.append(f"veto count fell from {before['vetoes']} to {after['vetoes']}")
    if after["summaries"] < before["summaries"] * (1 - SUMMARY_DROP):
        p.append(f"CRS summaries fell from {before['summaries']:,} to {after['summaries']:,}")
    if after["links"] < before["links"] * (1 - LINK_DROP):
        p.append(f"related-bill links fell from {before['links']:,} to {after['links']:,}")
    p += stale(after["data_date"], today)
    return p


def stale(data_date: str, today: str | None = None) -> list[str]:
    today_d = date.fromisoformat(today) if today else date.today()
    if not data_date:
        return ["database has no action dates"]
    age = (today_d - date.fromisoformat(data_date)).days
    if age > STALE_DAYS:
        return [f"newest recorded action is {age} days old ({data_date}); govinfo sync may be failing"]
    return []


def snapshots_to_delete(names: list[str], today: str, daily: int = 7, weekly: int = 8) -> list[str]:
    """Backups are named billboard-YYYY-MM-DD.sqlite.gz. Keep the last `daily` days and
    the Sunday copies from the last `weekly` weeks; everything else can go."""
    t = date.fromisoformat(today)
    keep_from_daily = t - timedelta(days=daily - 1)
    keep_from_weekly = t - timedelta(weeks=weekly)
    out = []
    for n in names:
        if not (n.startswith("billboard-") and n.endswith(".sqlite.gz")):
            continue
        try:
            d = date.fromisoformat(n[len("billboard-"):-len(".sqlite.gz")])
        except ValueError:
            continue
        if d >= keep_from_daily or (d.weekday() == 6 and d > keep_from_weekly):
            continue
        out.append(n)
    return sorted(out)


def save(conn, path: Path) -> dict:
    c = counts(conn)
    Path(path).write_text(json.dumps(c))
    return c


def check(conn, path: Path, today: str | None = None) -> list[str]:
    return compare(json.loads(Path(path).read_text()), counts(conn), today)
