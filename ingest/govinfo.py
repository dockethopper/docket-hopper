"""Download BILLSTATUS data from govinfo bulk data (no API key needed).

Two modes:
  * bulk:  one zip per Congress per bill type, used for the historic backfill.
  * sync:  read the JSON listing for a Congress/type and refetch only the XML
           files whose last-modified stamp changed. Used nightly.

Coverage: BILLSTATUS starts with the 113th Congress (2013).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db
from .parse_billstatus import parse

log = logging.getLogger("ingest.govinfo")

BASE = "https://www.govinfo.gov/bulkdata"
BILL_TYPES = ["hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"]
FIRST_CONGRESS = 113
UA = "billboard-ingest/0.1 (+https://github.com/)"


def current_congress(year: int | None = None) -> int:
    year = year or time.gmtime().tm_year
    # A Congress starts on Jan 3 of odd years: 2025 -> 119th.
    return (year - 1789) // 2 + 1


def _get(url: str, *, accept: str | None = None, retries: int = 4, timeout: int = 120) -> bytes:
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or attempt == retries:
                raise
            log.warning("HTTP %s for %s, retrying in %.0fs", e.code, url, delay)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries:
                raise
            log.warning("%s for %s, retrying in %.0fs", e, url, delay)
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        log.info("using cached %s", dest)
        return dest
    log.info("downloading %s", url)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.rename(dest)
    return dest


def listing(congress: int, bill_type: str) -> dict[str, str]:
    """Return {relative_path: last_modified} for every XML file in the folder."""
    url = f"{BASE}/json/BILLSTATUS/{congress}/{bill_type}"
    data = json.loads(_get(url, accept="application/json"))
    out = {}
    for f in data.get("files", []):
        link = f.get("link", "")
        if f.get("folder") or not link.endswith(".xml"):
            continue
        rel = link.split("/BILLSTATUS/", 1)[-1]
        stamp = f.get("formattedLastModifiedTime") or f.get("lastModifiedTime") or f.get("lastModified") or ""
        out[rel] = str(stamp)
    return out


def bulk(conn, congress: int, bill_type: str, cache_dir: Path, keep_zip: bool = False) -> int:
    """Backfill one Congress/type from its zip, then seed file_state from the listing."""
    url = f"{BASE}/BILLSTATUS/{congress}/{bill_type}/BILLSTATUS-{congress}-{bill_type}.zip"
    zpath = cache_dir / f"BILLSTATUS-{congress}-{bill_type}.zip"
    try:
        _download(url, zpath)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        log.warning("no zip for %s/%s, falling back to per-file sync", congress, bill_type)
        return sync(conn, congress, bill_type)

    n = errors = 0
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if not name.endswith(".xml"):
                continue
            try:
                db.upsert_bill(conn, parse(z.read(name)), source="govinfo-bulk")
                n += 1
            except Exception as e:  # one bad file should not stop the backfill
                errors += 1
                log.error("failed to parse %s: %s", name, e)
            if n % 1000 == 0:
                conn.commit()
    conn.commit()

    # Seed last-modified stamps so the next sync only fetches real changes.
    try:
        stamps = listing(congress, bill_type)
        conn.executemany("INSERT OR REPLACE INTO file_state VALUES (?,?)", stamps.items())
    except Exception as e:
        log.warning("could not seed file_state for %s/%s: %s", congress, bill_type, e)
    db.set_log(conn, f"bulk:{congress}:{bill_type}", f"{n} bills, {errors} errors")
    conn.commit()
    if not keep_zip:
        zpath.unlink(missing_ok=True)
    log.info("bulk %s/%s: %d bills, %d errors", congress, bill_type, n, errors)
    return n


def sync(conn, congress: int, bill_type: str, workers: int = 8) -> int:
    """Fetch only the BILLSTATUS files that changed since we last saw them."""
    stamps = listing(congress, bill_type)
    known = dict(conn.execute("SELECT path, last_modified FROM file_state WHERE path LIKE ?", (f"{congress}/{bill_type}/%",)).fetchall())
    changed = [p for p, s in stamps.items() if known.get(p) != s]
    log.info("sync %s/%s: %d files, %d changed", congress, bill_type, len(stamps), len(changed))
    if not changed:
        return 0

    def fetch(rel: str):
        try:
            return rel, _get(f"{BASE}/BILLSTATUS/{rel}"), None
        except Exception as e:
            return rel, None, e

    n = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rel, body, err in pool.map(fetch, changed):
            if err:
                log.error("fetch failed %s: %s", rel, err)
                continue
            try:
                db.upsert_bill(conn, parse(body), source="govinfo-sync")
                conn.execute("INSERT OR REPLACE INTO file_state VALUES (?,?)", (rel, stamps[rel]))
                n += 1
            except Exception as e:
                log.error("parse failed %s: %s", rel, e)
            if n % 500 == 0:
                conn.commit()
    db.set_log(conn, f"sync:{congress}:{bill_type}", f"{n} updated")
    conn.commit()
    return n
