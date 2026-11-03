"""Passage-odds model (no AI): plain logistic regression on public bill-status fields.

There is one model per stage a bill has reached, each trained only on bills that
reached that stage, so a bill that has passed the House is compared with other bills
that passed a chamber (no leakage from a bill's own future). Odds are published as
four bands, never as a percentage (decision D2): the model ranks well but its raw
probabilities run high.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import numpy as np

LEG = ("hr", "s", "hjres", "sjres")
LEG_SQL = "('hr','s','hjres','sjres')"
LAW = ("became_law", "veto_overridden")

# Party in the majority, by Congress. Add a line when a new Congress starts; an
# unknown Congress falls back to the latest known one.
HOUSE_MAJORITY = {113: "R", 114: "R", 115: "R", 116: "D", 117: "D", 118: "R", 119: "R"}
SENATE_MAJORITY = {113: "D", 114: "R", 115: "R", 116: "R", 117: "D", 118: "D", 119: "R"}

FEATURES = [
    "intercept", "sponsor in majority", "log cosponsors", "any bipartisan cosponsor",
    "log cross-party cosponsors", "has identical companion", "joint resolution", "Senate bill",
]

# Odds bands (decision D2). Lower bound inclusive.
BANDS = [(0.0, 0.01, "Long shot"), (0.01, 0.04, "Typical"), (0.04, 0.12, "Better than most"), (0.12, 1.01, "Strong")]
STAGE_NAMES = ["at introduction", "after committee", "after passing one chamber", "after passing both chambers"]

# Which model stage a status has reached. None = no live score (enacted, vetoed, resolution adopted).
_STAGE = {"introduced": 0, "reported": 1, "failed": 1, "passed_one": 2, "passed_both": 3, "to_president": 3}
# Statuses that count as having reached at least stage k, for training.
_REACHED = [
    None,
    {"reported", "failed", "passed_one", "passed_both", "to_president", "vetoed", "pocket_vetoed", "became_law", "veto_overridden"},
    {"passed_one", "passed_both", "to_president", "vetoed", "pocket_vetoed", "became_law", "veto_overridden"},
    {"passed_both", "to_president", "vetoed", "pocket_vetoed", "became_law", "veto_overridden"},
]


def band_of(p: float) -> int:
    for i, (lo, hi, _) in enumerate(BANDS):
        if lo <= p < hi:
            return i
    return len(BANDS) - 1


def live_stage(status: str | None) -> int | None:
    return _STAGE.get(status or "introduced")


def majority(congress: int, bill_type: str) -> str:
    m = HOUSE_MAJORITY if bill_type in ("hr", "hjres") else SENATE_MAJORITY
    return m.get(congress) or m[max(m)]


@dataclass
class Model:
    weights: list[np.ndarray]                      # one weight vector per stage
    auc: float = 0.0
    base_rate: float = 0.0
    calibration: list[dict] = field(default_factory=list)
    test_congress: int = 0
    train_through: int = 0
    n_train: int = 0

    def prob(self, x: np.ndarray, stage: int) -> float:
        return float(1 / (1 + np.exp(-x @ self.weights[stage])))


def load_rows(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], dict, dict]:
    bip = dict(conn.execute(f"""
        SELECT b.bill_id, SUM(co.party != b.sponsor_party AND co.party IN ('D','R'))
        FROM bills b JOIN cosponsors co USING(bill_id)
        WHERE b.bill_type IN {LEG_SQL} AND co.withdrawn_date IS NULL GROUP BY 1""").fetchall())
    comp = dict(conn.execute("SELECT bill_id, COUNT(*) FROM related_bills WHERE relationship='Identical bill' GROUP BY 1").fetchall())
    rows = conn.execute(f"""SELECT bill_id, congress, bill_type, sponsor_party, cosponsor_count, status, policy_area, introduced_date
        FROM bills WHERE bill_type IN {LEG_SQL}""").fetchall()
    return rows, bip, comp


def features(r, bip: dict, comp: dict) -> list[float]:
    cc = r["cosponsor_count"] or 0
    xb = bip.get(r["bill_id"]) or 0
    return [1.0, float(r["sponsor_party"] == majority(r["congress"], r["bill_type"])), float(np.log1p(cc)), float(xb > 0),
            float(np.log1p(xb)), float(comp.get(r["bill_id"], 0) > 0), float(r["bill_type"] in ("hjres", "sjres")),
            float(r["bill_type"] in ("s", "sjres"))]


def _fit(X: np.ndarray, y: np.ndarray, iters: int = 3000) -> np.ndarray:
    w = np.zeros(X.shape[1])
    if len(y) == 0:
        return w
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        w -= (X.T @ (p - y) / len(y) + 1e-4 * w)
    return w


def _fit_stages(rows, X, y) -> list[np.ndarray]:
    ws = []
    for k in range(4):
        m = np.ones(len(rows), bool) if k == 0 else np.array([r["status"] in _REACHED[k] for r in rows])
        ws.append(_fit(X[m], y[m]))
    return ws


def train(conn: sqlite3.Connection, current_congress: int) -> tuple[Model, dict, dict]:
    """Fit the live model on every completed Congress, and a track-record copy that
    leaves out the most recent completed Congress so we can publish how it did there."""
    rows, bip, comp = load_rows(conn)
    last_done = current_congress - 1
    X_all = np.array([features(r, bip, comp) for r in rows], float)
    y_all = np.array([r["status"] in LAW for r in rows], float)
    cong = np.array([r["congress"] for r in rows])

    # Track record: stage-0 model trained through last_done-1, scored on last_done at introduction.
    tr, te = cong < last_done, cong == last_done
    w0 = _fit(X_all[tr], y_all[tr])
    pt = 1 / (1 + np.exp(-X_all[te] @ w0))
    yt = y_all[te]
    auc, cal = 0.0, []
    if len(yt) and 0 < yt.sum() < len(yt):
        order = np.argsort(pt)
        ranks = np.empty(len(pt))
        ranks[order] = np.arange(len(pt))
        auc = float((ranks[yt == 1].mean() - (yt.sum() - 1) / 2) / (len(yt) - yt.sum()))
        bins = [0, 0.01, 0.03, 0.1, 0.3, 1.01]
        for lo, hi in zip(bins, bins[1:]):
            m = (pt >= lo) & (pt < hi)
            if m.sum():
                cal.append({"lo": lo, "hi": hi, "n": int(m.sum()), "pred": float(pt[m].mean()), "actual": float(yt[m].mean())})

    live_mask = cong <= last_done
    live_rows = [r for r, m in zip(rows, live_mask) if m]
    ws = _fit_stages(live_rows, X_all[live_mask], y_all[live_mask])
    model = Model(weights=ws, auc=auc, base_rate=float(yt.mean()) if len(yt) else 0.0, calibration=cal,
                  test_congress=last_done, train_through=last_done, n_train=int(live_mask.sum()))
    return model, bip, comp


def score_active(conn: sqlite3.Connection, model: Model, bip: dict, comp: dict, current_congress: int, day: str) -> dict[str, tuple[int, float, int]]:
    """Score every active bill of the current Congress and record changes in odds_history.
    Returns {bill_id: (stage, p, band)} for the current scores."""
    out = {}
    rows = conn.execute(f"""SELECT bill_id, congress, bill_type, sponsor_party, cosponsor_count, status FROM bills
        WHERE congress = ? AND bill_type IN {LEG_SQL}""", (current_congress,)).fetchall()
    last = {r[0]: (r[1], r[2]) for r in conn.execute("""
        SELECT h.bill_id, h.stage, h.band FROM odds_history h
        JOIN (SELECT bill_id, MAX(day) d FROM odds_history GROUP BY 1) m ON m.bill_id = h.bill_id AND m.d = h.day""")}
    new = []
    for r in rows:
        st = live_stage(r["status"])
        if st is None:
            continue
        p = model.prob(np.array(features(r, bip, comp)), st)
        b = band_of(p)
        out[r["bill_id"]] = (st, p, b)
        if last.get(r["bill_id"]) != (st, b):
            new.append((r["bill_id"], day, st, p, b))
    conn.executemany("INSERT OR REPLACE INTO odds_history VALUES (?,?,?,?,?)", new)
    conn.commit()
    return out
