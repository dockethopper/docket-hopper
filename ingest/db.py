"""SQLite helpers: open the database and upsert one parsed bill."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")

BILL_COLS = [
    "bill_id", "congress", "bill_type", "number", "title", "short_title", "origin_chamber",
    "introduced_date", "update_date", "policy_area", "sponsor_bioguide", "sponsor_name",
    "sponsor_party", "sponsor_state", "cosponsor_count", "action_count", "latest_action_date",
    "latest_action_text", "status", "law_type", "law_number", "summary", "latest_text_url",
]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    return conn


def upsert_bill(conn: sqlite3.Connection, parsed: dict, source: str) -> None:
    b = parsed["bill"]
    bid = b["bill_id"]
    # Keep word_count across re-ingests unless the text changed.
    prev = conn.execute("SELECT latest_text_url, word_count FROM bills WHERE bill_id = ?", (bid,)).fetchone()
    word_count = prev["word_count"] if prev and prev["latest_text_url"] == b["latest_text_url"] else None

    for t in ("actions", "cosponsors", "subjects", "related_bills", "committees", "text_versions"):
        conn.execute(f"DELETE FROM {t} WHERE bill_id = ?", (bid,))

    cols = BILL_COLS + ["word_count", "source", "ingested_at"]
    vals = [b[c] for c in BILL_COLS] + [word_count, source, now()]
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "bill_id")
    conn.execute(
        f"INSERT INTO bills ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
        f"ON CONFLICT(bill_id) DO UPDATE SET {updates}",
        vals,
    )

    conn.executemany(
        "INSERT INTO actions VALUES (?,?,?,?,?,?,?)",
        [(bid, a["seq"], a["action_date"], a["action_code"], a["action_type"], a["chamber"], a["text"]) for a in parsed["actions"]],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO cosponsors VALUES (?,?,?,?,?,?,?,?)",
        [(bid, c["bioguide_id"], c["name"], c["party"], c["state"], c["sponsorship_date"], c["is_original"], c["withdrawn_date"]) for c in parsed["cosponsors"]],
    )
    conn.executemany("INSERT OR IGNORE INTO subjects VALUES (?,?)", [(bid, s) for s in parsed["subjects"]])
    conn.executemany(
        "INSERT OR IGNORE INTO related_bills VALUES (?,?,?,?)",
        [(bid, r["related_id"], r["relationship"], r["identified_by"]) for r in parsed["related"]],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO committees VALUES (?,?,?,?)",
        [(bid, c["system_code"], c["name"], c["chamber"]) for c in parsed["committees"]],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO text_versions VALUES (?,?,?,?)",
        [(bid, t["version"], t["date"], t["url"] or "") for t in parsed["text_versions"]],
    )


def set_log(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO ingest_log VALUES (?,?,?)", (key, value, now()))


def get_log(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM ingest_log WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
