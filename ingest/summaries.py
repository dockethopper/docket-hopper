"""Two short AI-written summaries for the home page (drew, 2026-11-03).

    python -m ingest summaries --site site

One summary sits above "Latest floor and chamber action", the other above "Recently signed
into law". Each is written by Claude Opus 5.5 from records we hand it, nothing else, and is
checked before it can appear:

- Every bill or law number it names must be in its input.
- Every figure it uses must appear in its input.
- It must be a sensible length.

If a check fails, or the API is down, or there is no key, the page keeps the last summary that
passed (with its date) for up to a week, then hides the drop-down. Inputs are hashed, so a
quiet night with nothing new costs nothing. Every attempt is logged in `ai_summary_log` for review.

Summaries show on the site only when DH_SUMMARIES_LIVE=1 (the SUMMARIES_LIVE repository
variable). Until then they are built into the page hidden, and appear with ?preview=summaries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

from .db import now

log = logging.getLogger("ingest.summaries")

MODEL = "claude-opus-5-5"
MAX_TOKENS = 8000      # Opus 5.5 always thinks first; thinking counts toward this cap
KEEP_DAYS = 7          # how long a stale summary may stay up when tonight's fails
LEG = ("hr", "s", "hjres", "sjres")
LABEL = {"hr": "H.R.", "s": "S.", "hjres": "H.J.Res.", "sjres": "S.J.Res.", "hres": "H.Res.", "sres": "S.Res.",
         "hconres": "H.Con.Res.", "sconres": "S.Con.Res."}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_summaries (
    section TEXT PRIMARY KEY, input_hash TEXT, text TEXT, model TEXT,
    generated_at TEXT, data_date TEXT, links TEXT
);
CREATE TABLE IF NOT EXISTS ai_summary_log (
    section TEXT, at TEXT, input_hash TEXT, model TEXT, status TEXT, problems TEXT, text TEXT,
    input_tokens INTEGER, output_tokens INTEGER
);
"""

# Floor and chamber actions worth summarizing; signing and enactment belong to the laws summary.
FLOOR_RX = re.compile(r"passed|failed|agreed to|presented to president|cloture|veto|motion to suspend|"
                      r"roll no\.|record vote|resolving differences|conference report", re.I)
NOT_FLOOR_RX = re.compile(r"became (public|private) law|signed by president", re.I)

SYSTEM = """You write short, neutral summaries for Docket Hopper, a public website that tracks bills in the U.S. Congress.

Rules, all required:
1. Use only the records in the user's message. Do not add facts, background, history, motives, or predictions from your own knowledge, even if you are confident they are true. If the records do not say something, leave it out.
2. Be neutral. Never characterize a party, member, or bill as good or bad, and never guess at motives or political effects. Mention party only as a label, written like (R-TX) or (D-CA), exactly as given.
3. Refer to every bill by its number exactly as given (for example H.R. 1234 or S. 56), and to laws by their public law number as given (for example P.L. 119-35). Do not mention any bill or law that is not in the records.
4. Use only figures that appear in the records. Do not compute new numbers, percentages, or totals. Dates may be written as month and day.
5. Broader context comes only from the "context" figures provided, such as how many laws this Congress has passed compared with earlier Congresses at the same point.
6. Write plain prose in short paragraphs separated by blank lines. No headings, lists, bold, links, or quotation of whole titles longer than about twelve words; shorten a long title to its subject.
7. Write for a general reader. Explain what a bill does in a few plain words, using its CRS summary.
"""

PROMPTS = {
    "floor": ("Write a summary of recent floor and chamber action in Congress, 3 to 5 paragraphs, about 250 to 400 words. "
              "Start with the overall picture for the period (how much moved and of what kind), then cover the most significant "
              "bills, grouping related ones, then close with the context figures. Each bill's status_now says where it stands today; "
              "if a bill has since been signed into law, say so in a few words instead of describing it as pending. Records:"),
    "laws": ("Write a summary of the bills recently signed into law, 2 to 4 paragraphs, about 150 to 300 words. "
             "Start with the overall picture (how many and what areas), then describe the most significant laws, then close "
             "with the context figures. Records:"),
}


def label(bill_id: str) -> str:
    _, t, n = bill_id.split("-")
    return f"{LABEL.get(t, t)} {n}"


def _bill(conn, bid: str) -> dict:
    b = conn.execute("""SELECT bill_id, title, short_title, sponsor_name, sponsor_party, sponsor_state, cosponsor_count,
        policy_area, status, introduced_date, summary, law_number, law_type FROM bills WHERE bill_id = ?""", (bid,)).fetchone()
    parties = dict(conn.execute("SELECT party, COUNT(*) FROM cosponsors WHERE bill_id = ? AND withdrawn_date IS NULL GROUP BY 1", (bid,)).fetchall())
    summary = re.sub(r"<[^>]+>", " ", b["summary"] or "")
    summary = re.sub(r"\s+", " ", summary).strip()
    out = {
        "number": label(bid), "title": b["title"], "policy_area": b["policy_area"],
        "sponsor": f"{b['sponsor_name'] or ''}".strip(), "sponsor_party": b["sponsor_party"], "sponsor_state": b["sponsor_state"],
        "cosponsors": b["cosponsor_count"] or 0, "cosponsors_by_party": parties, "introduced": (b["introduced_date"] or "")[:10],
        "crs_summary": summary[:700] + ("…" if len(summary) > 700 else ""),
    }
    if b["law_number"]:
        out["public_law"] = ("P.L. " if b["law_type"] != "Private Law" else "Pvt.L. ") + b["law_number"]
    return out


STATUS_NOW = {
    "became_law": "has since been signed into law", "veto_overridden": "became law over a veto",
    "vetoed": "vetoed", "to_president": "awaiting the President's decision", "failed": "failed",
}


def floor_input(conn, cur: int, data_date: str, ctx: dict) -> dict | None:
    """Bills with floor or chamber action in the 14 days to data_date (wider if that is too quiet)."""
    def rows(days):
        return conn.execute(f"""SELECT a.bill_id, a.action_date, a.chamber, a.text FROM actions a JOIN bills b USING(bill_id)
            WHERE b.congress = ? AND b.bill_type IN {LEG} AND a.action_date >= date(?, ?)
            ORDER BY a.action_date DESC, a.bill_id, a.seq""", (cur, data_date, f"-{days} days")).fetchall()
    days = 14
    acts = [r for r in rows(days) if FLOOR_RX.search(r["text"] or "") and not NOT_FLOOR_RX.search(r["text"] or "")]
    if len({r["bill_id"] for r in acts}) < 3:
        days = 30
        acts = [r for r in rows(days) if FLOOR_RX.search(r["text"] or "") and not NOT_FLOOR_RX.search(r["text"] or "")]
    by = {}
    for r in acts:
        line = f"{r['action_date'][:10]}: {r['text'].strip()}"
        lst = by.setdefault(r["bill_id"], [])
        if line not in lst:
            lst.append(line)
    if not by:
        return None
    bills = []
    for bid in list(by)[:40]:
        d = _bill(conn, bid)
        d.pop("public_law", None)
        d["status_now"] = STATUS_NOW.get(conn.execute("SELECT status FROM bills WHERE bill_id = ?", (bid,)).fetchone()[0], "pending")
        d["actions_in_period"] = by[bid][:6]
        bills.append(d)
    start = (date.fromisoformat(data_date) - timedelta(days=days)).isoformat()
    return {"period": {"from": start, "to": data_date}, "bills_with_action": len(bills), "bills": bills, "context": ctx}


def laws_input(conn, cur: int, data_date: str, ctx: dict) -> dict | None:
    ids = [r[0] for r in conn.execute("""SELECT b.bill_id FROM bills b JOIN actions a USING(bill_id)
        WHERE b.congress = ? AND b.status IN ('became_law','veto_overridden')
          AND (a.text LIKE 'Became Public Law%' OR a.text LIKE 'Became Private Law%') AND a.action_date >= date(?, '-30 days')
        GROUP BY b.bill_id ORDER BY MIN(a.action_date) DESC, b.bill_id""", (cur, data_date)).fetchall()]
    if not ids:
        return None
    laws = []
    for bid in ids[:30]:
        d = _bill(conn, bid)
        d["became_law"] = conn.execute("""SELECT MIN(action_date) FROM actions WHERE bill_id = ?
            AND (text LIKE 'Became Public Law%' OR text LIKE 'Became Private Law%')""", (bid,)).fetchone()[0][:10]
        laws.append(d)
    start = (date.fromisoformat(data_date) - timedelta(days=30)).isoformat()
    return {"period": {"from": start, "to": data_date}, "laws_signed": len(laws), "laws": laws, "context": ctx}


def context(site: dict) -> dict:
    cur = site["cur"]
    sp = {int(k): v for k, v in site["same_point"].items()}
    ordn = lambda n: f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    cong = {c["congress"]: c for c in site["congress"]}
    return {
        "congress": f"{ordn(cur)} Congress", "day_of_term": site["day"],
        "laws_so_far_this_congress": sp.get(cur, 0),
        "laws_by_same_day_in_earlier_congresses": {ordn(c): n for c, n in sorted(sp.items()) if c != cur},
        "median_laws_by_same_day_since_2013": site["prior_median"],
        "bills_introduced_this_congress": cong.get(cur, {}).get("introduced"),
        "bills_passed_at_least_one_chamber_this_congress": cong.get(cur, {}).get("passed_one_plus"),
    }


# ---------------------------------------------------------------- checks
LABEL_RX = re.compile(r"(?<![\w.])(H\.\s?R\.|S\.|H\.\s?J\.\s?Res\.|S\.\s?J\.\s?Res\.|H\.\s?Res\.|S\.\s?Res\.|H\.\s?Con\.\s?Res\.|S\.\s?Con\.\s?Res\.)\s?(\d+)")
LAW_RX = re.compile(r"(?<![\w.])(?:P\.\s?L\.|Pub(?:lic)?\.?\s+L(?:aw)?\.?|Pvt\.\s?L\.)\s?(\d{2,3}[-–]\d+)")
NUM_RX = re.compile(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?")


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", "", s)


def check(text: str, inp: dict) -> list[str]:
    raw = json.dumps(inp, ensure_ascii=False)
    problems = []
    words = len(text.split())
    if words < 60 or words > 650:
        problems.append(f"length {words} words")
    allowed = {_norm_label(m.group(0)) for m in LABEL_RX.finditer(raw)}
    for m in LABEL_RX.finditer(text):
        if _norm_label(m.group(0)) not in allowed:
            problems.append(f"names {m.group(0)}, which is not in its records")
    laws = {m.group(1).replace("–", "-") for m in LAW_RX.finditer(raw)}
    for m in LAW_RX.finditer(text):
        if m.group(1).replace("–", "-") not in laws:
            problems.append(f"names law {m.group(1)}, which is not in its records")
    tokens = set()
    for m in re.finditer(r"\d+(?:,\d{3})*(?:\.\d+)?", raw):  # any digits in the records, even inside keys
        t = m.group(0).replace(",", "")
        tokens.add(t)
        tokens.add(t.lstrip("0") or "0")
        if "." in t:
            tokens.add(t.rstrip("0").rstrip("."))
    for m in NUM_RX.finditer(LAW_RX.sub(" ", text)):
        t = m.group(0).replace(",", "").rstrip(".")
        if t not in tokens and (t.lstrip("0") or "0") not in tokens:
            problems.append(f"uses the figure {m.group(0)}, which is not in its records")
    if re.search(r"^\s*(#|[-*•]\s)", text, re.M):
        problems.append("uses headings or a list")
    return sorted(set(problems))


def bill_id_of(number: str, congress: int) -> str:
    """"H.R. 1744" -> "119-hr-1744"."""
    pre, n = number.rsplit(" ", 1)
    t = {v: k for k, v in LABEL.items()}[pre]
    return f"{congress}-{t}-{n}"


def links(text: str, inp: dict, congress: int) -> dict:
    """Label as written in the summary -> bill id, for linking on the page."""
    by_label = {}
    for b in inp.get("bills", []) + inp.get("laws", []):
        by_label[_norm_label(b["number"])] = b["number"]
        if b.get("public_law"):
            by_label[_norm_label(b["public_law"])] = b["number"]
    out = {}
    for m in list(LABEL_RX.finditer(text)) + list(LAW_RX.finditer(text)):
        k = _norm_label(m.group(0))
        if k in by_label:
            out[m.group(0)] = bill_id_of(by_label[k], congress)
    return out


# ---------------------------------------------------------------- generation
class Stopped(RuntimeError):
    """The model stopped before finishing (for example at max_tokens); carries the tokens billed."""

    def __init__(self, msg: str, tin: int = 0, tout: int = 0):
        super().__init__(msg)
        self.tin, self.tout = tin, tout


def _call(section: str, inp: dict) -> tuple[str, int, int]:
    import anthropic  # only needed when a key is present

    client = anthropic.Anthropic(max_retries=3, timeout=180.0)
    resp = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM,
        messages=[{"role": "user", "content": PROMPTS[section] + "\n\n" + json.dumps(inp, ensure_ascii=False, indent=1)}],
    )
    if resp.stop_reason != "end_turn":
        raise Stopped(f"stopped early: {resp.stop_reason}", resp.usage.input_tokens, resp.usage.output_tokens)
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text, resp.usage.input_tokens, resp.usage.output_tokens


def run(conn, site_dir: Path, live: bool | None = None, call=_call) -> dict:
    conn.executescript(SCHEMA)
    site_dir = Path(site_dir)
    site = json.loads((site_dir / "src" / "data" / "site.json").read_text())
    cur, data_date = site["cur"], site["data_date"]
    ctx = context(site)
    live = (os.environ.get("DH_SUMMARIES_LIVE", "") == "1") if live is None else live
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY")) or call is not _call
    out = {"live": live, "model": MODEL, "sections": {}}

    for section, build in (("floor", floor_input), ("laws", laws_input)):
        inp = build(conn, cur, data_date, ctx)
        prev = conn.execute("SELECT * FROM ai_summaries WHERE section = ?", (section,)).fetchone()
        if inp is None:
            log.info("summaries: %s has nothing in its period; hidden", section)
            continue
        h = hashlib.sha256(json.dumps([MODEL, SYSTEM, PROMPTS[section], inp], sort_keys=True).encode()).hexdigest()[:16]
        if prev and prev["input_hash"] == h:
            log.info("summaries: %s inputs unchanged; reusing %s", section, prev["generated_at"])
        elif has_key:
            try:
                text, tin, tout = call(section, inp)
                problems = check(text, inp)
            except Exception as e:  # API down, refusal, bad key, cut off: keep the last good one
                text, problems = "", [f"call failed: {type(e).__name__}: {e}"[:300]]
                tin, tout = getattr(e, "tin", 0), getattr(e, "tout", 0)
            status = "ok" if not problems else "rejected"
            conn.execute("INSERT INTO ai_summary_log VALUES (?,?,?,?,?,?,?,?,?)",
                         (section, now(), h, MODEL, status, json.dumps(problems), text, tin, tout))
            log.info("summaries: %s %s (%d in, %d out tokens)%s", section, status, tin, tout, f": {problems}" if problems else "")
            if not problems:
                conn.execute("INSERT OR REPLACE INTO ai_summaries VALUES (?,?,?,?,?,?,?)",
                             (section, h, text, MODEL, now(), data_date, json.dumps(links(text, inp, cur))))
            conn.commit()
        else:
            log.info("summaries: no ANTHROPIC_API_KEY; %s keeps its last summary", section)
        row = conn.execute("SELECT * FROM ai_summaries WHERE section = ?", (section,)).fetchone()
        if not row:
            continue
        age = (date.fromisoformat(data_date) - date.fromisoformat(row["data_date"])).days
        if age > KEEP_DAYS:
            log.info("summaries: %s last good summary is %d days old; hidden", section, age)
            continue
        out["sections"][section] = {
            "paras": [p.strip() for p in re.split(r"\n\s*\n", row["text"]) if p.strip()],
            "links": json.loads(row["links"] or "{}"), "generated": row["generated_at"][:10], "data_date": row["data_date"],
            "current": row["input_hash"] == h,
        }
    conn.commit()
    path = site_dir / "src" / "data" / "summaries.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    log.info("summaries: wrote %s (%s; live=%s)", path, ", ".join(out["sections"]) or "none", live)
    return out
