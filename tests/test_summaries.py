import json
import sqlite3

from ingest import summaries as S

INP = {"bills": [{"number": "H.R. 1744", "title": "Religious Freedom Act", "cosponsors": 11, "introduced": "2025-02-27", "actions_in_period": ["2026-10-26: Presented to President."]},
                 {"number": "S. 56", "public_law": "P.L. 119-135", "cosponsors": 3}],
       "context": {"laws_so_far_this_congress": 139, "median_laws_by_same_day_since_2013": 199.5}}
GOOD = ("Congress sent H.R. 1744 to the president on Oct. 26, 2026, after 11 cosponsors signed on. "
        "S. 56 became P.L. 119-135. " + "Quiet week otherwise. " * 12 +
        "\n\nThe 119th Congress has passed 139 laws so far, against a median of 199.5 by this point since 2013.")


def test_good_summary_passes():
    assert S.check(GOOD, INP) == []


def test_invented_bill_law_or_number_is_rejected():
    bad = GOOD + "\n\nH.R. 99 also moved, and P.L. 119-200 took effect. It costs 450 million dollars."
    probs = S.check(bad, INP)
    assert any("H.R. 99" in p for p in probs)
    assert any("119-200" in p for p in probs)
    assert any("450" in p for p in probs)


def test_us_abbreviation_is_not_a_bill_and_lists_are_rejected():
    assert S.check(GOOD.replace("Quiet week", "U.S. 2026 news was quiet", 1), INP) == []
    assert any("list" in p for p in S.check("- " + GOOD, INP))


def test_links_map_labels_and_laws_to_bills():
    assert S.links(GOOD, INP, 119) == {"H.R. 1744": "119-hr-1744", "S. 56": "119-s-56", "P.L. 119-135": "119-s-56"}


def _db(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    conn.row_factory = sqlite3.Row
    return conn


def test_run_keeps_last_good_summary_when_a_new_one_fails(tmp_path, monkeypatch):
    site = tmp_path / "site"
    (site / "src" / "data").mkdir(parents=True)
    (site / "src" / "data" / "site.json").write_text(json.dumps(
        {"cur": 119, "data_date": "2026-10-29", "day": 664, "same_point": {"118": 106, "119": 139}, "prior_median": 106,
         "congress": [{"congress": 119, "introduced": 10, "passed_one_plus": 2}]}))
    conn = _db(tmp_path)
    inp = {"floor": dict(INP), "laws": None}
    monkeypatch.setattr(S, "floor_input", lambda *a: inp["floor"])
    monkeypatch.setattr(S, "laws_input", lambda *a: inp["laws"])

    out = S.run(conn, site, live=False, call=lambda sec, i: (GOOD, 100, 50))
    assert out["sections"]["floor"]["current"] and "laws" not in out["sections"]
    assert out["sections"]["floor"]["links"]["H.R. 1744"] == "119-hr-1744"

    calls = []
    S.run(conn, site, live=False, call=lambda sec, i: calls.append(sec) or (GOOD, 1, 1))
    assert calls == []  # same inputs: no new call

    inp["floor"] = {**INP, "extra": "new action"}
    out = S.run(conn, site, live=True, call=lambda sec, i: (GOOD + " H.R. 4242 too.", 1, 1))
    assert out["live"] and out["sections"]["floor"]["current"] is False  # rejected; yesterday's stays
    log = conn.execute("SELECT status FROM ai_summary_log ORDER BY rowid").fetchall()
    assert [r[0] for r in log] == ["ok", "rejected"]
