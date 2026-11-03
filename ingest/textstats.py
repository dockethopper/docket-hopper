"""Fill bills.word_count by downloading each bill's latest text and counting words.

Only the count is stored, not the text, so the database stays small. Enacted
bills can optionally keep their full text under data/text/ for the summarizer.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .govinfo import _get
from .parse_billstatus import strip_html

log = logging.getLogger("ingest.textstats")

# govinfo bill XML carries metadata (Dublin Core, action history) around the body;
# count only the legislative text itself.
_BODY_RE = re.compile(r"<(legis-body|resolution-body|engrossed-amendment-body)\b[\s\S]*?</\1>")


def bill_text(raw: str) -> str:
    bodies = [m.group(0) for m in _BODY_RE.finditer(raw)]
    return strip_html(" ".join(bodies) if bodies else raw) or ""


def run(conn, limit: int | None = None, workers: int = 8, keep_text_dir: Path | None = None, minutes: float | None = None) -> int:
    # Newest Congress first, then older ones over later nights (decision D3).
    q = ("SELECT bill_id, latest_text_url, status FROM bills WHERE word_count IS NULL AND latest_text_url IS NOT NULL "
         "ORDER BY congress DESC, introduced_date DESC")
    deadline = time.monotonic() + minutes * 60 if minutes else None
    rows = conn.execute(q + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    log.info("textstats: %d bills need a word count", len(rows))

    def work(row):
        if deadline and time.monotonic() > deadline:
            return row, None, TimeoutError("time budget spent")
        try:
            text = bill_text(_get(row["latest_text_url"]).decode("utf-8", "replace"))
            return row, text, None
        except Exception as e:
            return row, None, e

    n = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row, text, err in pool.map(work, rows):
            if isinstance(err, TimeoutError):
                continue
            if err:
                log.warning("text fetch failed %s: %s", row["bill_id"], err)
                continue
            conn.execute("UPDATE bills SET word_count = ? WHERE bill_id = ?", (len(text.split()), row["bill_id"]))
            if keep_text_dir and row["status"] in ("became_law", "veto_overridden", "vetoed", "pocket_vetoed"):
                keep_text_dir.mkdir(parents=True, exist_ok=True)
                (keep_text_dir / f"{row['bill_id']}.txt").write_text(text)
            n += 1
            if n % 500 == 0:
                conn.commit()
    conn.commit()
    return n
