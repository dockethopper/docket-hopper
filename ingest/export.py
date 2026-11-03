"""Turn the SQLite database into the JSON the static site is built from.

    python -m ingest export --out site

Writes:
    site/src/data/site.json            numbers, claims and lists for the landing, coverage, outcomes, about pages
    site/src/data/pre/{c}.json         full detail for bills that get a pre-rendered page (current Congress, laws, vetoes)
    site/public/data/dir/{c}.json      one Congress of directory rows (D5)
    site/public/data/search.json       compact cross-Congress title index
    site/public/data/bill/{c}/{t}/{n}.json   detail for every other bill (rendered in the browser)
    site/public/data/net/{c}/{t}/{n}.json    neighborhood map with precomputed layout (N1, N3)
    site/public/data/net/area/{slug}.json    enacted bills of one policy area

It also records today's passage-odds bands (B3), so run it before publishing the database.
"""

from __future__ import annotations

import collections
import json
import logging
import re
import shutil
import statistics
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from . import claims, model as M, network as N
from .status import _PASS_RE, _RECEIVED_RE, _REPORTED_RE, _chamber

log = logging.getLogger("ingest.export")

LEG = M.LEG
LEG_SQL = M.LEG_SQL
LAW = ("became_law", "veto_overridden")
VETO = ("vetoed", "veto_overridden", "pocket_vetoed")
PRES = {113: "Obama", 114: "Obama", 115: "Trump", 116: "Trump", 117: "Biden", 118: "Biden", 119: "Trump"}
LABEL = {"hr": "H.R.", "s": "S.", "hjres": "H.J.Res.", "sjres": "S.J.Res.", "hres": "H.Res.", "sres": "S.Res.",
         "hconres": "H.Con.Res.", "sconres": "S.Con.Res."}
REL_GROUPS = [
    ("Identical or companion", re.compile(r"Identical|Companion", re.I)),
    ("Shared text, or included in a law", re.compile(r"Contained in public law|Public law contains|Text similarities", re.I)),
    ("Procedural (rules, motions)", re.compile(r"Procedural", re.I)),
    ("Related, per CRS or House", re.compile(r"Related", re.I)),
]


def congress_start(c: int) -> date:
    return date(2013 + 2 * (c - 113), 1, 3)


def label(bill_id: str) -> str:
    c, t, n = bill_id.split("-")
    return f"{LABEL.get(t, t)} {n}"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def sponsor_short(name: str | None) -> str:
    return re.sub(r"\s*\[.*$", "", re.sub(r"^(Rep\.|Sen\.|Del\.|Resident Commissioner)\s*", "", name or "")).strip()


def disp_title(r) -> str:
    return r["title"] or r["short_title"] or label(r["bill_id"])


def _wjson(path: Path, obj, pretty=False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=None if pretty else (",", ":"), default=str,
                               indent=1 if pretty else None))


def milestones(actions: list) -> dict:
    """First date for each step of the five-step track, from the action history."""
    out = {"intro": None, "reported": None, "passed_one": None, "passed_both": None, "law": None, "veto": None, "first_chamber": None}
    passed: set[str] = set()
    for a in actions:
        t = a["text"] or ""
        d = a["action_date"]
        if not out["intro"]:
            out["intro"] = d
        if not out["reported"] and _REPORTED_RE.search(t):
            out["reported"] = d
        ch = None
        if _PASS_RE.search(t):
            ch = _chamber(a)
        else:
            m = _RECEIVED_RE.search(t)
            if m:
                ch = "house" if m.group(1).lower() == "senate" else "senate"
        if ch and ch not in passed:
            passed.add(ch)
            if len(passed) == 1:
                out["passed_one"], out["first_chamber"] = d, ch
            elif len(passed) == 2:
                out["passed_both"] = d
        tl = t.lower()
        if not out["law"] and ("became public law" in tl or "became private law" in tl):
            out["law"] = d
        if not out["veto"] and ("vetoed by president" in tl or "pocket vetoed" in tl):
            out["veto"] = d
    return out


def run(conn, out: Path, today: str | None = None, workers: int | None = None) -> dict:
    out = Path(out)
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    q = lambda s, *a: conn.execute(s, a).fetchall()

    cur = q("SELECT MAX(congress) FROM bills")[0][0]
    data_date = q("SELECT MAX(latest_action_date) FROM bills WHERE congress = ?", cur)[0][0][:10]
    day = min(730, (date.fromisoformat(data_date) - congress_start(cur)).days)
    congresses = list(range(113, cur + 1))
    log.info("export: %dth Congress, data through %s (day %d)", cur, data_date, day)

    # ---------------- model + odds history (B3)
    mdl, bip, comp = M.train(conn, cur)
    scores = M.score_active(conn, mdl, bip, comp, cur, today)
    log.info("model: AUC %.3f on %dth; %d active bills scored", mdl.auc, mdl.test_congress, len(scores))
    odds_hist = collections.defaultdict(list)
    for r in q("SELECT bill_id, day, stage, band FROM odds_history ORDER BY bill_id, day"):
        odds_hist[r[0]].append([r[1], r[2], r[3]])

    # ---------------- per-Congress totals (L4: bills and joint resolutions only)
    cong = {r["congress"]: dict(r) for r in q(f"""SELECT congress, COUNT(*) introduced,
        SUM(status IN ('became_law','veto_overridden')) enacted,
        SUM(status IN ('vetoed','veto_overridden','pocket_vetoed')) vetoed,
        SUM(status IN ('vetoed','pocket_vetoed')) vetoed_standing,
        SUM(status IN ('reported','passed_one','passed_both','to_president','vetoed','pocket_vetoed','became_law','veto_overridden','failed')) reported_plus,
        SUM(status IN ('passed_one','passed_both','to_president','vetoed','pocket_vetoed','became_law','veto_overridden')) passed_one_plus,
        SUM(status IN ('passed_both','to_president','vetoed','pocket_vetoed','became_law','veto_overridden')) passed_both_plus
        FROM bills WHERE bill_type IN {LEG_SQL} GROUP BY 1""")}
    totals = dict(q("SELECT COUNT(*) bills, SUM(summary IS NOT NULL AND summary != '') with_summary FROM bills")[0])
    totals["links"] = q("SELECT COUNT(*) FROM related_bills")[0][0]
    totals["leg"] = sum(c["introduced"] for c in cong.values())
    totals["laws"] = sum(c["enacted"] for c in cong.values())
    totals["with_words"] = q("SELECT COUNT(*) FROM bills WHERE word_count IS NOT NULL")[0][0]

    # law dates
    law_date = dict(q("""SELECT bill_id, MIN(action_date) FROM actions
        WHERE text LIKE 'Became Public Law%' OR text LIKE 'Became Private Law%' GROUP BY 1"""))
    laws = q(f"SELECT bill_id, congress, introduced_date, latest_action_date FROM bills WHERE bill_type IN {LEG_SQL} AND status IN ('became_law','veto_overridden')")
    same_point = {}
    dd = collections.defaultdict(list)
    for r in laws:
        ld = (law_date.get(r["bill_id"]) or r["latest_action_date"] or "")[:10]
        if not ld:
            continue
        if (date.fromisoformat(ld) - congress_start(r["congress"])).days <= day:
            same_point[r["congress"]] = same_point.get(r["congress"], 0) + 1
        if r["introduced_date"]:
            dd[r["congress"]].append((date.fromisoformat(ld) - date.fromisoformat(r["introduced_date"][:10])).days)
    same_point = {c: same_point.get(c, 0) for c in congresses}
    days_to_law = {c: {"median": statistics.median(v), "p25": float(np.percentile(v, 25)), "p75": float(np.percentile(v, 75)), "n": len(v)}
                   for c, v in dd.items() if v}

    land = claims.landing(cur, same_point[cur], {c: same_point[c] for c in congresses if c != cur})
    claims.verify(land["text"], [cur, same_point[cur]])
    prior_med = statistics.median([same_point[c] for c in congresses if c != cur]) if len(congresses) > 1 else 0

    # ---------------- landing lists
    moving = [dict(r) for r in q(f"""SELECT bill_id, title, short_title, status, latest_action_date, latest_action_text, sponsor_party
        FROM bills WHERE congress = ? AND bill_type IN {LEG_SQL} AND status IN ('passed_one','passed_both','to_president')
        ORDER BY latest_action_date DESC, bill_id LIMIT 6""", cur)]
    recent_laws = [dict(r) for r in q("""SELECT bill_id, title, short_title, law_number, law_type, latest_action_date, policy_area
        FROM bills WHERE status IN ('became_law','veto_overridden') AND congress = ?
        ORDER BY latest_action_date DESC, bill_id LIMIT 6""", cur)]
    for r in moving + recent_laws:
        r["title"] = disp_title(r)
        r.pop("short_title", None)

    # ---------------- outcomes (completed Congresses)
    rows, _, _ = M.load_rows(conn)
    mm = collections.Counter()
    cb = collections.Counter()
    for r in rows:
        if r["congress"] >= cur:
            continue
        lawy = r["status"] in LAW
        k = "majority" if r["sponsor_party"] == M.majority(r["congress"], r["bill_type"]) else "minority"
        mm[(k, "n")] += 1
        mm[(k, "law")] += lawy
        cc = r["cosponsor_count"] or 0
        bk = "0" if cc == 0 else "1–4" if cc < 5 else "5–19" if cc < 20 else "20–49" if cc < 50 else "50+"
        bp = "bipartisan" if (bip.get(r["bill_id"]) or 0) > 0 else "one-party"
        cb[(bk, bp, "n")] += 1
        cb[(bk, bp, "law")] += lawy
    cos_bucket = [{"bucket": k, "kind": bp, "n": cb[(k, bp, "n")], "law": cb[(k, bp, "law")]}
                  for k in ["0", "1–4", "5–19", "20–49", "50+"] for bp in ("one-party", "bipartisan") if cb[(k, bp, "n")]]
    nb = sum(r["n"] for r in cos_bucket if r["kind"] == "bipartisan")
    lb = sum(r["law"] for r in cos_bucket if r["kind"] == "bipartisan")
    no = sum(r["n"] for r in cos_bucket if r["kind"] == "one-party")
    lo = sum(r["law"] for r in cos_bucket if r["kind"] == "one-party")
    oc = claims.outcomes(lb / nb if nb else 0, lo / no if no else 0)
    claims.verify(oc["text"], [round(oc["ratio"])])
    vetoes = [dict(r) for r in q("""SELECT bill_id, congress, title, short_title, status, sponsor_party, latest_action_date
        FROM bills WHERE status IN ('vetoed','veto_overridden','pocket_vetoed') ORDER BY congress, latest_action_date""")]
    for v in vetoes:
        v["title"] = disp_title(v)
        v.pop("short_title", None)

    # ---------------- bulk lookups for bill detail
    log.info("loading child tables")
    cos_party = collections.defaultdict(dict)
    for r in q("SELECT bill_id, party, COUNT(*) FROM cosponsors WHERE withdrawn_date IS NULL GROUP BY 1, 2"):
        cos_party[r[0]][r[1] or "?"] = r[2]
    committees = collections.defaultdict(list)
    for r in q("SELECT bill_id, chamber, name FROM committees ORDER BY bill_id, chamber, name"):
        committees[r[0]].append(f"{r[1] or ''} {r[2] or ''}".strip())
    subjects = collections.defaultdict(list)
    for r in q("SELECT bill_id, subject FROM subjects ORDER BY bill_id, subject"):
        subjects[r[0]].append(r[1])
    versions = collections.defaultdict(list)
    for r in q("SELECT bill_id, version, MIN(date) d FROM text_versions GROUP BY 1, 2 ORDER BY 1, d"):
        versions[r[0]].append([r[1], (r[2] or "")[:10]])
    sponsor_rec = {r[0]: [r[1], r[2]] for r in q(f"""SELECT sponsor_bioguide, COUNT(*), SUM(status IN ('became_law','veto_overridden'))
        FROM bills WHERE bill_type IN {LEG_SQL} AND sponsor_bioguide IS NOT NULL GROUP BY 1""")}
    area_rec = {r[0]: [r[1], r[2]] for r in q(f"""SELECT policy_area, COUNT(*), SUM(status IN ('became_law','veto_overridden'))
        FROM bills WHERE bill_type IN {LEG_SQL} AND congress < ? AND policy_area IS NOT NULL GROUP BY 1""", cur)}
    info = {r["bill_id"]: r for r in q("""SELECT bill_id, congress, bill_type, title, short_title, status, sponsor_party,
        sponsor_state, policy_area, cosponsor_count FROM bills""")}
    related = collections.defaultdict(dict)   # bill -> {other: relationship}
    for r in q("SELECT bill_id, related_id, relationship FROM related_bills"):
        for a, b in ((r[0], r[1]), (r[1], r[0])):
            prev = related[a].get(b)
            gi = _rel_group(r[2])
            if prev is None or gi < _rel_group(prev):
                related[a][b] = r[2]

    # ---------------- coverage (media)
    media_cut = q("SELECT date(?, '-90 days')", data_date if data_date > today else today)[0][0]
    media_state = {r[0]: (r[1], r[2]) for r in q("SELECT bill_id, generic, status FROM media_state")}
    media_series = collections.defaultdict(dict)
    for r in q("SELECT bill_id, day, articles FROM media_daily WHERE day >= ?", media_cut):
        media_series[r[0]][r[1]] = r[2]
    media_arts = collections.defaultdict(list)
    for r in q("SELECT bill_id, title, url, domain, seen_date FROM media_articles ORDER BY bill_id, seen_date DESC"):
        if len(media_arts[r[0]]) < 5:
            media_arts[r[0]].append({"title": r[1], "url": r[2], "domain": r[3], "date": r[4]})
    end = date.fromisoformat(media_cut)
    days90 = [(end.toordinal() + i) for i in range(1, 91)]
    days90 = [date.fromordinal(d).isoformat() for d in days90]

    def press(bid):
        st = media_state.get(bid)
        if not st:
            return None
        if st[0]:
            return -1  # name too generic (M3)
        if st[1] in ("ok", "empty"):
            return sum(media_series[bid].values())
        return None

    # ---------------- network neighborhoods + layouts (N1, N3)
    log.info("network layouts")
    adj = N.adjacency(q("SELECT bill_id, related_id, relationship FROM related_bills"))
    cache = {r[0]: (r[1], r[2]) for r in q("SELECT key, sig, xy FROM net_layout")}
    jobs, hoods = [], {}
    for bid in sorted(adj):
        if bid not in info:
            continue
        nodes, edges = N.neighborhood(adj, bid)
        ids = list(nodes)
        rad = [round(N.node_radius(info[k]["cosponsor_count"] if k in info else 0, k == bid), 1) for k in ids]
        hoods[bid] = (nodes, edges)
        jobs.append((bid, ids, edges, rad))
    # policy-area maps: enacted bills only
    area_nodes = collections.defaultdict(list)
    for bid, r in info.items():
        if r["status"] in LAW and r["policy_area"] and r["bill_type"] in LEG:
            area_nodes[r["policy_area"]].append(bid)
    area_hoods = {}
    for area, ids in area_nodes.items():
        ids = sorted(ids, key=lambda b: (-int(b.split("-")[0]), b))
        s = set(ids)
        edges = sorted({(min(a, b), max(a, b), k) for a in ids for b, k in adj.get(a, {}).items() if b in s and a != b})
        key = "area:" + slug(area)
        area_hoods[area] = ({i: 1 for i in ids}, edges)
        jobs.append((key, ids, edges, [round(N.node_radius(info[k]["cosponsor_count"], False), 1) for k in ids]))
    xy = N.layout_many(jobs, cache, workers)
    conn.execute("DELETE FROM net_layout")
    conn.executemany("INSERT INTO net_layout VALUES (?,?,?)", [(k, cache[k][0], cache[k][1]) for k in xy if k in cache])
    conn.commit()
    log.info("layouts done: %d", len(xy))

    pub = out / "public" / "data"
    for sub in ("bill", "net", "dir"):
        shutil.rmtree(pub / sub, ignore_errors=True)
    src = out / "src" / "data"
    shutil.rmtree(src / "pre", ignore_errors=True)

    def node_row(k, d):
        r = info.get(k)
        if not r:
            return [k, d, None, None, None, None, 0]
        return [k, d, disp_title(r)[:110], r["status"], r["sponsor_party"], r["policy_area"], r["cosponsor_count"] or 0]

    def write_net(path: Path, key: str, nodes: dict, edges, center: str | None):
        ids = list(nodes)
        pos = N.decode_xy(xy.get(key, ""))
        index = {k: i for i, k in enumerate(ids)}
        _wjson(path, {"center": center, "cols": ["id", "d", "title", "status", "party", "area", "cos", "x", "y"],
                      "nodes": [node_row(k, nodes[k]) + (pos[i] if i < len(pos) else [450, 280]) for i, k in enumerate(ids)],
                      "edges": [[index[a], index[b], 1 if k == "identical" else 0] for a, b, k in edges]})

    for bid, (nodes, edges) in hoods.items():
        c, t, n = bid.split("-")
        write_net(pub / "net" / c / t / f"{n}.json", bid, nodes, edges, bid)
    areas_out = []
    for area, (nodes, edges) in sorted(area_hoods.items()):
        write_net(pub / "net" / "area" / f"{slug(area)}.json", "area:" + slug(area), nodes, edges, None)
        areas_out.append({"area": area, "slug": slug(area), "n": len(nodes), "links": len(edges)})

    # featured map: the current Congress's law with the biggest neighborhood
    def hood_size(b):
        return len(hoods[b][0]) - 1 if b in hoods else 0
    cur_laws = [b for b, r in info.items() if r["congress"] == cur and r["status"] in LAW]
    featured = max(cur_laws or list(hoods), key=lambda b: (hood_size(b), b), default=None)
    feat = None
    if featured:
        fn = [k for k in hoods.get(featured, ({}, []))[0] if k != featured]
        ac = collections.Counter(info[k]["policy_area"] for k in fn if k in info and info[k]["policy_area"])
        top = ac.most_common(1)[0] if ac else (None, 0)
        fr = info[featured]
        name = disp_title(fr) if len(disp_title(fr)) <= 70 else label(featured)
        feat = {"id": featured, "name": name, "n": len(fn), "top_area": top[0], "top_n": top[1],
                "claim": claims.network(name, len(fn), top[0], top[1])}
        claims.verify(feat["claim"], [len(fn), top[1], *re.findall(r"\d+", name)])

    # ---------------- bill detail + directory + search
    log.info("bill detail")
    pre_ids = set()
    pre = collections.defaultdict(list)
    dir_rows = collections.defaultdict(list)
    search = []
    area_list = sorted({r["policy_area"] for r in info.values() if r["policy_area"]})
    area_idx = {a: i for i, a in enumerate(area_list)}

    cur_actions: list = []
    cur_bid = None
    action_iter = conn.execute("SELECT bill_id, action_date, chamber, text FROM actions ORDER BY bill_id, action_date, seq")
    ms_by_bill = {}
    for r in action_iter:
        if r[0] != cur_bid:
            if cur_bid:
                ms_by_bill[cur_bid] = milestones(cur_actions)
            cur_bid, cur_actions = r[0], []
        cur_actions.append({"action_date": (r[1] or "")[:10], "chamber": r[2], "text": r[3]})
    if cur_bid:
        ms_by_bill[cur_bid] = milestones(cur_actions)

    for b in conn.execute("SELECT * FROM bills ORDER BY congress, bill_type, number"):
        bid = b["bill_id"]
        c, t, n = bid.split("-")
        title = disp_title(b)
        cp = cos_party.get(bid, {})
        bp = bip.get(bid) or 0
        sc = scores.get(bid)
        pr = press(bid)
        is_res = t not in LEG
        rel = related.get(bid, {})
        groups = [{"name": g[0], "n": 0, "items": []} for g in REL_GROUPS]
        for oid, relname in sorted(rel.items(), key=lambda kv: (-int(kv[0].split("-")[0]), kv[0])):
            gi = _rel_group(relname)
            g = groups[gi]
            g["n"] += 1
            if len(g["items"]) < 40:
                o = info.get(oid)
                g["items"].append([oid, disp_title(o)[:140] if o else None, o["status"] if o else None, o["sponsor_party"] if o else None])
        ms = ms_by_bill.get(bid, {})
        det = {
            "id": bid, "c": int(c), "t": t, "n": int(n), "title": title,
            "intro": b["introduced_date"], "status": b["status"], "law": b["law_number"], "law_type": b["law_type"],
            "area": b["policy_area"], "origin": b["origin_chamber"],
            "sponsor": {"name": sponsor_short(b["sponsor_name"]), "party": b["sponsor_party"], "state": b["sponsor_state"],
                        "id": b["sponsor_bioguide"], "rec": sponsor_rec.get(b["sponsor_bioguide"])},
            "cos": {"n": b["cosponsor_count"] or 0, "D": cp.get("D", 0), "R": cp.get("R", 0), "I": cp.get("I", 0) + cp.get("ID", 0), "bip": bp},
            "latest": [b["latest_action_date"], b["latest_action_text"]],
            "ms": ms, "words": b["word_count"], "text_url": b["latest_text_url"],
            "summary": b["summary"], "committees": committees.get(bid, []), "subjects": subjects.get(bid, [])[:14],
            "n_subjects": len(subjects.get(bid, [])), "versions": versions.get(bid, []),
            "area_rec": area_rec.get(b["policy_area"]), "rel": [g for g in groups if g["n"]],
            "net": hood_size(bid),
            "odds": {"now": [sc[0], sc[2]] if sc else None, "hist": odds_hist.get(bid, [])},
            "media": None, "cur": cur,
        }
        if pr is not None:
            s = media_series.get(bid, {})
            det["media"] = {"generic": pr == -1, "total": None if pr == -1 else pr, "series": [s.get(d, 0) for d in days90] if pr != -1 else [],
                            "start": days90[0], "arts": media_arts.get(bid, [])}
        prerender = int(c) == cur or b["status"] in LAW + VETO
        if prerender:
            pre[c].append(det)
            pre_ids.add(bid)
        else:
            _wjson(pub / "bill" / c / t / f"{n}.json", det)
        dir_rows[c].append([f"{t}-{n}", title[:200], sponsor_short(b["sponsor_name"]), b["sponsor_party"], b["sponsor_state"],
                            (b["introduced_date"] or "")[:10], b["status"], b["cosponsor_count"] or 0, bp, b["word_count"],
                            area_idx.get(b["policy_area"]), pr, sc[2] if sc else None, (b["latest_action_date"] or "")[:10], int(is_res)])
        search.append([bid, title[:110], sponsor_short(b["sponsor_name"]).split(",")[0], b["status"]])

    for c, items in pre.items():
        _wjson(src / "pre" / f"{c}.json", items)
    dir_meta = []
    for c, items in dir_rows.items():
        ci = cong.get(int(c), {})
        _wjson(pub / "dir" / f"{c}.json", {
            "congress": int(c), "cols": ["id", "title", "sponsor", "party", "state", "intro", "status", "cos", "bip", "words", "area", "press", "band", "latest", "res"],
            "areas": area_list, "rows": items})
        text = claims.directory(int(c), ci.get("introduced", 0), ci.get("enacted", 0), int(c) == cur)
        claims.verify(text, [int(c), ci.get("introduced", 0), (ci.get("enacted", 0) / ci["introduced"]) if ci.get("introduced") else 0.0])
        dir_meta.append({"congress": int(c), "claim": text, "n": len(items), "leg": ci.get("introduced", 0)})
    _wjson(pub / "search.json", {"cols": ["id", "title", "sponsor", "status"], "rows": search})

    # ---------------- coverage ledger (M1)
    ledger = []
    for bid, s in media_series.items():
        st = media_state.get(bid)
        if not st or st[0] or bid not in info:
            continue
        tot = sum(s.values())
        if tot <= 0:
            continue
        wk = [sum(s.get(d, 0) for d in days90[i:i + 7]) for i in range(0, 91 - 7, 7)]
        r = info[bid]
        ledger.append({"id": bid, "title": disp_title(r), "total": tot, "weeks": wk, "status": r["status"],
                       "cos": r["cosponsor_count"] or 0, "bip": bip.get(bid) or 0, "generic": False})
    ledger.sort(key=lambda x: -x["total"])
    ledger = ledger[:40]
    top3 = ledger[:3]
    cov_claim = claims.coverage(sum(1 for x in top3 if x["status"] in LAW) if len(top3) == 3 else None, len(ledger))
    generic_rows = []
    for r in q(f"""SELECT b.bill_id, b.title, b.short_title, b.status, b.cosponsor_count FROM bills b JOIN media_state m USING(bill_id)
        WHERE m.generic = 1 AND b.congress = ? ORDER BY (b.status != 'introduced') DESC, b.cosponsor_count DESC LIMIT 8""", cur):
        generic_rows.append({"id": r["bill_id"], "title": disp_title(r), "total": None, "weeks": [], "status": r["status"],
                             "cos": r["cosponsor_count"] or 0, "bip": bip.get(r["bill_id"]) or 0, "generic": True})
    cov_stats = dict(q("""SELECT COUNT(*) tracked, SUM(status='ok') with_coverage, MIN(fetched_at) first FROM media_state WHERE query IS NOT NULL""")[0])
    cov_stats["generic"] = q("SELECT COUNT(*) FROM media_state WHERE generic = 1")[0][0]

    site = {
        "built": today, "data_date": data_date, "cur": cur, "day": day, "congresses": congresses,
        "start": {c: congress_start(c).isoformat() for c in congresses},
        "pres": PRES, "totals": totals, "congress": [cong[c] for c in congresses if c in cong],
        "same_point": same_point, "prior_median": prior_med, "landing": land,
        "moving": moving, "recent_laws": recent_laws,
        "dir": dir_meta, "areas": areas_out,
        "outcomes": {"claim": oc["text"], "ratio": oc["ratio"], "rate_bip": lb / nb if nb else 0, "rate_one": lo / no if no else 0,
                     "cos_bucket": cos_bucket, "maj_min": {k: {"n": mm[(k, "n")], "law": mm[(k, "law")]} for k in ("majority", "minority")},
                     "vetoes": vetoes, "days_to_law": days_to_law},
        "model": {"auc": mdl.auc, "base_rate": mdl.base_rate, "calibration": mdl.calibration, "test_congress": mdl.test_congress,
                  "train_through": mdl.train_through, "n_train": mdl.n_train, "features": M.FEATURES,
                  "weights": [dict(zip(M.FEATURES, map(float, w))) for w in mdl.weights],
                  "bands": [[lo_, hi_, nm] for lo_, hi_, nm in M.BANDS], "active_scored": len(scores),
                  "band_counts": collections.Counter(s[2] for s in scores.values())},
        "network": feat,
        "coverage": {"claim": cov_claim, "rows": ledger, "generic": generic_rows, "stats": cov_stats},
        "prerendered": len(pre_ids),
    }
    _wjson(src / "site.json", site, pretty=True)
    log.info("export done: %d pre-rendered bills, %d bill files, %d maps", len(pre_ids), totals["bills"] - len(pre_ids), len(hoods))
    return site


def _rel_group(relationship: str) -> int:
    for i, (_, rx) in enumerate(REL_GROUPS):
        if rx.search(relationship or ""):
            return i
    return 3
