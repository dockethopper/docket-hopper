"""News coverage from the GDELT DOC API (free, no key), collected slowly every night.

Constraints this module is built around (decisions M2, M3, D4):
- GDELT throttles hard: a few quick calls from one address get HTTP 429 and the block
  lasts 10+ minutes. Calls are spaced (default 20 s) and a second 429 ends the run.
- The DOC API only looks back about three months, so coverage history starts at launch.
- A bill's name alone is a noisy search ("SAVE Act"). An article counts only if it also
  names the sponsor or the bill number. Names that are still too generic get a flag
  instead of a number.
- We store counts and headline links only, never article text.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import now

log = logging.getLogger("ingest.gdelt")
API = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "DocketHopper/1.0 (+https://github.com/dockethopper/docket-hopper)"
ALIASES = Path(__file__).with_name("media_aliases.json")
LABEL = {"hr": "H.R.", "s": "S.", "hjres": "H.J.Res.", "sjres": "S.J.Res."}

# Single words that make a bill name useless as a search term even with the sponsor check.
_COMMON = {"save", "safe", "help", "care", "fair", "free", "hope", "home", "jobs", "work", "clear", "clean", "act",
           "access", "protect", "secure", "strong", "smart", "ready", "relief", "reform", "rights", "united", "clergy",
           "freedom", "liberty", "justice", "promise", "patriot", "build", "invest", "restore", "recover", "honor"}


class Throttled(Exception):
    pass


def searchable_name(title: str | None) -> str | None:
    """The bill's popular name if it has one we can search for, else None."""
    if not title:
        return None
    t = title.strip().rstrip(".")
    if t.lower().startswith(("to ", "a bill", "providing for", "relating to", "expressing", "making ", "authorizing")):
        return None
    if len(t) > 90 or not re.search(r"\bAct\b|\bResolution\b", t):
        return None
    return t


def core_name(name: str) -> str:
    return re.sub(r"\s+(Act|Resolution)(\s+of\s+\d{4})?$", "", name).strip()


def is_generic(name: str) -> bool:
    core = core_name(name)
    words = core.split()
    if len(words) == 0:
        return True
    if len(words) == 1:
        w = words[0].strip("’'").lower()
        return w in _COMMON or (words[0].isupper() and len(words[0]) <= 6) or len(w) <= 4
    return False


def sponsor_surname(sponsor_name: str | None) -> str | None:
    # "Rep. Hill, J. French [R-AR-2]" -> "Hill"
    if not sponsor_name:
        return None
    m = re.match(r"^(?:Rep\.|Sen\.|Del\.|Resident Commissioner)?\s*([^,\[]+)", sponsor_name)
    s = m.group(1).strip() if m else ""
    return s if len(s) >= 4 and " " not in s else None


def build_query(row, aliases: dict) -> tuple[str | None, bool]:
    """Returns (query, generic). query None means the bill has no searchable name."""
    a = aliases.get(row["bill_id"])
    if a:
        return a["query"], False
    name = searchable_name(row["title"])
    if not name:
        return None, False
    if is_generic(name):
        return None, True
    t = row["bill_type"]
    label = f'"{LABEL.get(t, t)} {row["number"]}"'
    sur = sponsor_surname(row["sponsor_name"])
    check = f"({sur} OR {label})" if sur else label
    return f'"{name}" {check} sourcecountry:US', False


def _call(params: dict, timeout: int = 60) -> dict:
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Throttled() from e
        raise
    body = body.strip()
    if not body.startswith("{"):
        # GDELT reports query problems ("phrase too short", "too many keywords") as plain text.
        raise ValueError(body[:200])
    return json.loads(body)


def pick_bills(conn, congress: int, limit: int) -> list:
    """Active bills first (recent action), then never-fetched by cosponsors, then stalest."""
    return conn.execute(f"""
        SELECT b.bill_id, b.bill_type, b.number, b.title, b.sponsor_name, b.latest_action_date, m.fetched_at
        FROM bills b LEFT JOIN media_state m USING(bill_id)
        WHERE b.congress = ? AND b.bill_type IN ('hr','s','hjres','sjres')
          AND (m.fetched_at IS NULL OR replace(replace(m.fetched_at,'T',' '),'Z','') < datetime('now','-20 hours'))
          AND (m.query IS NOT NULL OR m.bill_id IS NULL)
        ORDER BY (b.latest_action_date >= date('now','-30 days')) DESC,
                 (m.fetched_at IS NULL) DESC,
                 CASE WHEN m.fetched_at IS NULL THEN -b.cosponsor_count ELSE 0 END,
                 m.fetched_at, b.latest_action_date DESC
        LIMIT ?""", (congress, limit * 4)).fetchall()


def run(conn, congress: int, max_calls: int = 150, spacing: float = 20.0, minutes: float = 55.0) -> dict:
    aliases = json.loads(ALIASES.read_text()) if ALIASES.exists() else {}
    deadline = time.monotonic() + minutes * 60
    stats = {"calls": 0, "bills": 0, "skipped": 0, "throttled": 0, "errors": 0}
    last_call = 0.0
    strikes = 0

    def call(params):
        nonlocal last_call, strikes
        wait = spacing - (time.monotonic() - last_call)
        if wait > 0:
            time.sleep(wait)
        last_call = time.monotonic()
        stats["calls"] += 1
        try:
            out = _call(params)
            strikes = 0
            return out
        except Throttled:
            stats["throttled"] += 1
            strikes += 1
            if strikes >= 2:
                raise
            log.warning("GDELT 429; backing off 3 minutes")
            time.sleep(180)
            last_call = time.monotonic()
            stats["calls"] += 1
            return _call(params)

    for row in pick_bills(conn, congress, max_calls):
        if stats["calls"] >= max_calls or time.monotonic() > deadline:
            break
        q, generic = build_query(row, aliases)
        if not q:
            # No searchable name, or too generic: remember that so we never spend a call on it.
            conn.execute("INSERT OR REPLACE INTO media_state VALUES (?,?,?,?,?)", (row["bill_id"], None, int(generic), now(), "skipped"))
            stats["skipped"] += 1
            continue
        end = datetime.now(timezone.utc)
        if row["fetched_at"]:
            start = datetime.strptime(row["fetched_at"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc) - timedelta(days=2)
        else:
            start = end - timedelta(days=90)
        tl_params = {"mode": "timelinevolraw", "format": "json",
                     "startdatetime": start.strftime("%Y%m%d%H%M%S"), "enddatetime": end.strftime("%Y%m%d%H%M%S")}
        try:
            try:
                tl = call({"query": q, **tl_params})
            except ValueError:
                # GDELT can reject a short quoted phrase such as "S. 5"; keep the sponsor check only.
                alt = re.sub(r' \((\S+) OR "[^"]+"\)', r" \1", q)
                if alt == q:
                    raise
                q = alt
                tl = call({"query": q, **tl_params})
            data = (tl.get("timeline") or [{}])[0].get("data") or []
            days = [(p["date"][:4] + "-" + p["date"][4:6] + "-" + p["date"][6:8], int(p.get("value") or 0)) for p in data]
            conn.executemany("INSERT OR REPLACE INTO media_daily (bill_id, day, articles) VALUES (?,?,?)",
                             [(row["bill_id"], d, n) for d, n in days])
            total = sum(n for _, n in days)
            if total > 0 and stats["calls"] < max_calls:
                arts = call({"query": q, "mode": "artlist", "format": "json", "maxrecords": 8, "sort": "datedesc", "timespan": "3months"})
                for a in (arts.get("articles") or [])[:8]:
                    sd = a.get("seendate") or ""
                    conn.execute("INSERT OR REPLACE INTO media_articles VALUES (?,?,?,?,?)",
                                 (row["bill_id"], a.get("url"), a.get("title"), a.get("domain"), f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}" if sd else None))
            conn.execute("INSERT OR REPLACE INTO media_state VALUES (?,?,?,?,?)", (row["bill_id"], q, 0, now(), "ok" if total else "empty"))
            stats["bills"] += 1
        except Throttled:
            log.warning("GDELT still throttling after back-off; stopping for tonight")
            break
        except Exception as e:  # bad query text, network blip: note it and move on
            stats["errors"] += 1
            log.warning("GDELT failed for %s: %s", row["bill_id"], e)
            conn.execute("INSERT OR REPLACE INTO media_state VALUES (?,?,?,?,?)", (row["bill_id"], q, 0, now(), "error"))
        conn.commit()
    conn.commit()
    log.info("gdelt: %s", stats)
    return stats
