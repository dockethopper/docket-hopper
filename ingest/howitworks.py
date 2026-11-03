"""Numbers for the "How Congress works" page (site/src/pages/how-it-works/).

Part 1, the institution: seats by party in each chamber today and at the start of every
Congress since 2013, from the public congress-legislators dataset (no API key). The fetch
result is cached in the database (ingest_log key "members"), so a failed download keeps the
last good copy instead of breaking the build.

Part 2, the path of a bill: for every bill and joint resolution in a completed Congress,
the first date it reached each stage, read from its action history:

    committee   reported by a committee, or discharged from one
    p1 / p2     passed its own chamber / passed the other chamber
    pres        presented to the President
    law         became public or private law

Every claim title on the page is generated here with branches for when the data points
the other way, and checked by claims.verify against the numbers it quotes.
"""

from __future__ import annotations

import collections
import json
import logging
import statistics
import urllib.request
from datetime import date, datetime, timezone

from . import claims, model as M

log = logging.getLogger("ingest.howitworks")

LEGISLATORS = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/legislators-{}.yaml"
TERRITORIES = {"DC", "PR", "GU", "VI", "AS", "MP"}
PRES_PARTY = {"Obama": "D", "Trump": "R", "Biden": "D"}

# Action codes from the Library of Congress action-code list.
REPORTED = {"5000", "14000", "H12200", "H12100"}
DISCHARGED = {"5500", "14500", "H12300"}
PASSED_HOUSE, PASSED_SENATE = "8000", "17000"
PRESENTED = {"E20000", "28000"}
LAW_CODES = {"36000", "E40000"}

LAW = ("became_law", "veto_overridden")
BOTH = ("passed_both", "to_president", "became_law", "vetoed", "veto_overridden", "pocket_vetoed")


def congress_start(c: int) -> date:
    return date(2013 + 2 * (c - 113), 1, 3)


# ---------------------------------------------------------------- Part 2: stages

def stage_dates(rows) -> dict:
    """rows: (bill_id, action_date, action_code, action_type, text) ordered by bill and date.
    Returns {bill_id: {committee, p1, p1_code, p2, pres, law, susp, uc}}."""
    out: dict[str, dict] = {}
    for bid, d, code, typ, text in rows:
        b = out.get(bid)
        if b is None:
            b = out[bid] = {"committee": None, "reported": None, "p1": None, "p1_code": None, "p2": None, "pres": None,
                            "law": None, "susp": False, "uc": False}
        t = (text or "").lower()
        if code in REPORTED or (typ == "Committee" and "ordered to be reported" in t):
            b["reported"] = b["reported"] or d
            b["committee"] = b["committee"] or d
        if code in DISCHARGED:
            b["committee"] = b["committee"] or d
        if code in (PASSED_HOUSE, PASSED_SENATE):
            if b["p1"] is None:
                b["p1"], b["p1_code"] = d, code
            elif b["p2"] is None and code != b["p1_code"]:
                b["p2"] = d
            if code == PASSED_SENATE and "unanimous consent" in t and b["p1_code"] == PASSED_SENATE and b["p2"] is None:
                b["uc"] = True
        if "suspend the rules and pass" in t and b["p2"] is None:
            b["susp"] = True
        if code in PRESENTED:
            b["pres"] = b["pres"] or d
        if code in LAW_CODES or "became public law" in t or "became private law" in t:
            b["law"] = b["law"] or d
    return out


def _days(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def _spread(xs: list[int]) -> dict | None:
    xs = sorted(x for x in xs if x is not None and x >= 0)
    if not xs:
        return None
    n = len(xs)
    return {"n": n, "p25": xs[n // 4], "med": statistics.median(xs), "p75": xs[(3 * n) // 4]}


def path(conn, cur: int) -> dict:
    q = lambda s, *a: conn.execute(s, a).fetchall()
    done_max = cur - 1
    bills = {r[0]: {"id": r[0], "cg": r[1], "st": r[2], "intro": r[3], "oc": r[4], "title": r[5] or "", "area": r[6]}
             for r in q(f"""SELECT bill_id, congress, status, introduced_date, origin_chamber, title, policy_area
                            FROM bills WHERE bill_type IN {M.LEG_SQL}""")}
    rows = q(f"""SELECT a.bill_id, a.action_date, a.action_code, a.action_type, a.text FROM actions a
                 JOIN bills b ON b.bill_id = a.bill_id WHERE b.bill_type IN {M.LEG_SQL}
                 ORDER BY a.bill_id, a.action_date, a.seq""")
    sd = stage_dates(rows)
    empty = {"committee": None, "reported": None, "p1": None, "p2": None, "pres": None, "law": None, "susp": False, "uc": False, "p1_code": None}
    for bid, b in bills.items():
        b.update(sd.get(bid, empty))

    law = lambda b: b["st"] in LAW
    both = lambda b: b["st"] in BOTH

    def stages(xs):
        return {"intro": len(xs), "committee": sum(1 for b in xs if b["committee"]), "p1": sum(1 for b in xs if b["p1"]),
                "p2": sum(1 for b in xs if both(b)), "pres": sum(1 for b in xs if b["pres"] or law(b) or b["st"] in ("vetoed", "pocket_vetoed")),
                "law": sum(1 for b in xs if law(b)), "veto": sum(1 for b in xs if b["st"] in ("vetoed", "veto_overridden", "pocket_vetoed"))}

    def far(b):
        if law(b):
            return "law"
        if both(b):
            return "both"
        if b["p1"]:
            return "one"
        if b["committee"]:
            return "comm"
        return "intro"

    done = [b for b in bills.values() if b["cg"] <= done_max]
    laws = [b for b in done if law(b)]
    first, last = min(b["cg"] for b in done), done_max
    out = {"first": first, "last": last, "years": [congress_start(first).year, congress_start(last).year + 1]}
    out["all"] = stages(done)
    out["far"] = dict(collections.Counter(far(b) for b in done))
    out["by_origin"] = {oc: stages([b for b in done if b["oc"] == oc]) for oc in ("House", "Senate")}
    out["by_congress"] = {c: stages([b for b in bills.values() if b["cg"] == c]) for c in sorted({b["cg"] for b in bills.values()})}
    out["p1_no_committee"] = sum(1 for b in done if b["p1"] and not b["committee"])
    out["gaps"] = {
        "intro_comm": _spread([_days(b["intro"], b["reported"]) for b in laws if b["reported"]]),
        "comm_p1": _spread([_days(b["reported"], b["p1"]) for b in laws if b["reported"]]),
        "intro_p1": _spread([_days(b["intro"], b["p1"]) for b in laws]),
        "p1_p2": _spread([_days(b["p1"], b["p2"]) for b in laws if b["p2"]]),
        "p2_pres": _spread([_days(b["p2"], b["pres"]) for b in laws if b["p2"] and b["pres"]]),
        "pres_law": _spread([_days(b["pres"], b["law"]) for b in laws if b["pres"] and b["law"]]),
        "intro_law": _spread([_days(b["intro"], b["law"]) for b in laws if b["law"]]),
    }

    def month(d: str, c: int) -> int:
        dd, s = date.fromisoformat(d[:10]), congress_start(c)
        return (dd.year - s.year) * 12 + dd.month - 1

    lm = collections.Counter(min(month(b["law"], b["cg"]), 24) for b in laws if b["law"])
    out["law_month"] = [lm.get(i, 0) for i in range(25)]
    lines = []
    for b in laws:
        if b["law"] and b["intro"]:
            s = congress_start(b["cg"])
            lines.append([b["id"], (date.fromisoformat(b["intro"][:10]) - s).days, (date.fromisoformat(b["law"][:10]) - s).days])
    lines.sort(key=lambda r: (r[2], r[1], r[0]))
    out["lines"] = lines

    hp = [b for b in done if b["oc"] == "House" and b["p1_code"] == PASSED_HOUSE]
    sp = [b for b in done if b["oc"] == "Senate" and b["p1_code"] == PASSED_SENATE]
    law_ids = {b["id"] for b in laws}
    conf = {r[0] for r in q("""SELECT DISTINCT bill_id FROM actions WHERE text LIKE '%conference report%' AND text LIKE '%agreed to%'""")}
    rd = {r[0] for r in q("SELECT DISTINCT bill_id FROM actions WHERE action_type = 'ResolvingDifferences'")}
    out["fast"] = {"house_p1": len(hp), "house_susp": sum(b["susp"] for b in hp), "senate_p1": len(sp), "senate_uc": sum(b["uc"] for b in sp),
                   "laws": len(laws), "conference": len(law_ids & conf), "pingpong": len((law_ids & rd) - conf)}
    done_ids = {b["id"] for b in done}
    twins = {r[0] for r in q("SELECT bill_id, related_id FROM related_bills WHERE relationship = 'Identical bill'")
             if r[0] in done_ids and r[0] not in law_ids and r[1] in law_ids}
    out["twins"] = len(twins)
    naming = lambda t: any(k in t.lower() for k in ("to designate the facility", "to name the", "to designate the", "commemorative coin"))
    out["naming"] = sum(1 for b in laws if naming(b["title"]))
    out["post_office"] = sum(1 for b in laws if "postal service" in b["title"].lower() and naming(b["title"]))
    areas = collections.Counter(b["area"] or "Not assigned" for b in laws)
    out["policy"] = areas.most_common(10)
    top = out["policy"][0][0] if out["policy"] else None
    out["naming_in_top"] = sum(1 for b in laws if (b["area"] or "Not assigned") == top and naming(b["title"]))
    return out


# ---------------------------------------------------------------- Part 1: members

def _party(p: str | None) -> str:
    return {"Democrat": "D", "Republican": "R", "Independent": "I"}.get(p or "", "O")


def composition(people: list, day: str, typ: str) -> list:
    """Seated voting members on `day` (YYYY-MM-DD): [party, state, class, caucus] each."""
    seats = []
    for p in people:
        for t in p.get("terms", []):
            if t["type"] != typ or not (str(t["start"]) <= day <= str(t["end"])) or t["state"] in TERRITORIES:
                continue
            party = _party(t.get("party"))
            for pa in t.get("party_affiliations") or []:
                if str(pa["start"]) <= day <= str(pa["end"]):
                    party = _party(pa["party"])
            caucus = t.get("caucus")
            seats.append([party, t["state"], t.get("class"), caucus[0] if caucus else None])
    return sorted(seats, key=lambda s: ("DIRO".index(s[0]), s[1]))


def members(people: list, today: str, congresses: list[int]) -> dict:
    by = {}
    for c in congresses:
        day = date(2013 + 2 * (c - 113), 2, 15).isoformat()      # after January swearing-in and runoffs
        if day > today:
            day = today
        by[c] = {ch: dict(collections.Counter(s[0] for s in composition(people, day, typ))) for ch, typ in (("house", "rep"), ("senate", "sen"))}
    return {"asof": today, "house": composition(people, today, "rep"), "senate": composition(people, today, "sen"), "by_congress": by}


def fetch_people() -> list:
    import yaml  # only needed when fetching

    people = []
    for kind in ("current", "historical"):
        with urllib.request.urlopen(LEGISLATORS.format(kind), timeout=60) as r:
            people += yaml.safe_load(r.read())
    return people


def load_members(conn, today: str, congresses: list[int], fetch=fetch_people) -> dict | None:
    try:
        m = members(fetch(), today, congresses)
        if len(m["house"]) < 400 or len(m["senate"]) < 95:
            raise ValueError(f"implausible seat counts: {len(m['house'])} House, {len(m['senate'])} Senate")
        conn.execute("INSERT OR REPLACE INTO ingest_log(key, value, updated_at) VALUES ('members', ?, ?)",
                     (json.dumps(m), datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return m
    except Exception as e:  # network or data problem: fall back to the last good copy
        log.warning("members: fetch failed (%s); using cached copy", e)
        row = conn.execute("SELECT value FROM ingest_log WHERE key = 'members'").fetchone()
        return json.loads(row[0]) if row else None


# ---------------------------------------------------------------- claims

def control(m: dict | None, path_: dict, pres: dict) -> dict:
    """Which Congresses had one party in both chambers and the White House, and laws in each."""
    rows = []
    for c, st in path_["by_congress"].items():
        h, s = M.HOUSE_MAJORITY.get(c), M.SENATE_MAJORITY.get(c)
        p = PRES_PARTY.get(pres.get(c))
        rows.append({"c": c, "house": h, "senate": s, "pres": pres.get(c), "unified": bool(h and h == s == p),
                     "laws": st["law"], "done": c <= path_["last"],
                     "seats": (m or {}).get("by_congress", {}).get(c) or (m or {}).get("by_congress", {}).get(str(c))})
    return {"rows": rows}


def titles(P: dict, C: dict) -> dict:
    """Claim titles that depend on the data, each with a fallback branch."""
    A, out = P["all"], {}
    gates = [A["committee"] / A["intro"], (A["p1"] - P["p1_no_committee"]) / A["committee"] if A["committee"] else 0,
             A["p2"] / A["p1"] if A["p1"] else 0, A["law"] / A["p2"] if A["p2"] else 0]
    if gates[0] == min(gates) and gates[3] >= 0.9:
        out["gates"] = "Committee is the hardest gate. A bill that clears both chambers almost always becomes law."
    elif gates[0] == min(gates):
        out["gates"] = "Committee is the hardest gate a bill has to clear."
    else:
        out["gates"] = "Every stage of the route turns bills away."
    dec = P["law_month"][23]
    share = dec / A["law"] if A["law"] else 0
    if share >= 0.25:
        out["calendar"] = "More than a quarter of each Congress's laws pass in its final December."
    elif dec > max(v for i, v in enumerate(P["law_month"]) if i != 23):
        out["calendar"] = "Congress passes more laws in its final December than in any other month."
    else:
        out["calendar"] = "Laws pass throughout the two-year term, with no single rush at the end."
    F = P["fast"]
    fast = min(F["house_susp"] / F["house_p1"] if F["house_p1"] else 0, F["senate_uc"] / F["senate_p1"] if F["senate_p1"] else 0)
    out["fast"] = ("Most bills pass through procedural fast lanes meant for measures nobody fights over." if fast > 0.5
                   else "Many bills pass through procedural fast lanes meant for measures nobody fights over.")
    bc = P["by_congress"]
    a, b = bc[P["first"]], bc[P["last"]]
    growth = b["intro"] / a["intro"] - 1
    lawchg = b["law"] / a["law"] - 1 if a["law"] else 0
    g = claims.pct(growth, 0)
    if growth > 0.1 and abs(lawchg) <= 0.2:
        out["trend"] = f"Members file {g} more bills than they did in {P['years'][0]}. Congress enacts about the same number."
    elif growth > 0.1 and lawchg < -0.2:
        out["trend"] = f"Members file {g} more bills than they did in {P['years'][0]}, and Congress enacts fewer."
    elif growth > 0.1:
        out["trend"] = f"Members file {g} more bills than they did in {P['years'][0]}, and Congress enacts more of them."
    else:
        out["trend"] = f"Members file about as many bills as they did in {P['years'][0]}."
    claims.verify(out["trend"], [g, P["years"][0]])
    nm = P["naming"] / A["law"] if A["law"] else 0
    if nm >= 0.2:
        out["naming"] = "Nearly one law in four names a post office, a clinic or a park, or mints a coin." if nm >= 0.22 else \
            "About one law in five names a post office, a clinic or a park, or mints a coin."
    else:
        out["naming"] = "Some laws only name a post office, a clinic or a park, or mint a coin."
    done = [r for r in C["rows"] if r["done"]]
    uni, div = [r["laws"] for r in done if r["unified"]], [r["laws"] for r in done if not r["unified"]]
    if uni and div and min(uni) > max(div):
        out["control"] = "When one party held the House, the Senate and the White House, Congress enacted more laws."
    elif uni and div and statistics.mean(uni) > statistics.mean(div):
        out["control"] = "Congresses under one party's control enacted more laws on average."
    else:
        out["control"] = "Unified control of Congress and the White House has not reliably meant more laws."
    out["unified_laws"], out["divided_laws"] = sorted(uni), sorted(div)
    return out


def build(conn, cur: int, today: str, pres: dict, congresses: list[int], fetch=fetch_people) -> dict:
    P = path(conn, cur)
    m = load_members(conn, today, congresses, fetch)
    C = control(m, P, pres)
    return {"path": P, "members": m, "control": C, "titles": titles(P, C)}
