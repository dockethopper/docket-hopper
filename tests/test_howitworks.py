"""How Congress works page: stage reading, seat counts, cached member data, claim branches."""

import sqlite3

import pytest

from ingest import howitworks as H


def test_stage_dates_reads_each_stage_once():
    rows = [
        ("118-hr-1", "2023-01-09", "Intro-H", "IntroReferral", "Introduced in House"),
        ("118-hr-1", "2023-02-01", "H12200", "Committee", "Reported (Amended) by the Committee on Ways and Means."),
        ("118-hr-1", "2023-03-01", "H30300", "Floor", "Mr. X moved to suspend the rules and pass the bill, as amended."),
        ("118-hr-1", "2023-03-01", "8000", "Floor", "Passed/agreed to in House: On motion to suspend the rules and pass the bill Agreed to by voice vote."),
        ("118-hr-1", "2023-06-01", "17000", "Floor", "Passed/agreed to in Senate: Passed Senate without amendment by Unanimous Consent."),
        ("118-hr-1", "2023-06-05", "E20000", "President", "Presented to President."),
        ("118-hr-1", "2023-06-10", "36000", "BecameLaw", "Became Public Law No: 118-1."),
        ("118-s-2", "2023-01-09", "10000", "IntroReferral", "Introduced in Senate"),
        ("118-s-2", "2023-01-10", "17000", "Floor", "Passed/agreed to in Senate: Passed Senate without amendment by Unanimous Consent."),
    ]
    sd = H.stage_dates(rows)
    a = sd["118-hr-1"]
    assert (a["committee"], a["p1"], a["p2"], a["pres"], a["law"]) == ("2023-02-01", "2023-03-01", "2023-06-01", "2023-06-05", "2023-06-10")
    assert a["susp"] and not a["uc"]          # the Senate's UC passage is the second chamber, not the origin's fast lane
    b = sd["118-s-2"]
    assert b["committee"] is None and b["p1"] == "2023-01-10" and b["uc"]


PEOPLE = [
    {"terms": [{"type": "rep", "start": "2023-01-03", "end": "2025-01-03", "state": "CA", "party": "Democrat"},
               {"type": "rep", "start": "2025-01-03", "end": "2027-01-03", "state": "CA", "party": "Republican"}]},
    {"terms": [{"type": "rep", "start": "2025-01-03", "end": "2027-01-03", "state": "DC", "party": "Democrat"}]},      # delegate: no vote
    {"terms": [{"type": "sen", "start": "2019-01-03", "end": "2031-01-03", "state": "VT", "party": "Independent", "class": 1, "caucus": "Democrat"}]},
    {"terms": [{"type": "sen", "start": "2021-01-03", "end": "2027-01-03", "state": "TX", "party": "Republican", "class": 2,
                "party_affiliations": [{"start": "2021-01-03", "end": "2024-01-01", "party": "Democrat"}, {"start": "2024-01-01", "end": "2027-01-03", "party": "Republican"}]}]},
]


def test_composition_skips_delegates_and_follows_party_switches():
    assert H.composition(PEOPLE, "2026-06-01", "rep") == [["R", "CA", None, None]]
    assert H.composition(PEOPLE, "2024-06-01", "rep") == [["D", "CA", None, None]]
    assert H.composition(PEOPLE, "2022-06-01", "sen") == [["D", "TX", 2, None], ["I", "VT", 1, "D"]]
    m = H.members(PEOPLE, "2026-06-01", [118, 119])
    assert m["by_congress"][119] == {"house": {"R": 1}, "senate": {"I": 1, "R": 1}}


def test_member_fetch_falls_back_to_cached_copy():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingest_log (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL)")
    assert H.load_members(conn, "2026-06-01", [119], fetch=lambda: PEOPLE) is None      # implausibly few seats: rejected, nothing cached

    big = [{"terms": [{"type": "rep", "start": "2025-01-03", "end": "2027-01-03", "state": "CA", "party": "Democrat"}]}] * 430 + \
          [{"terms": [{"type": "sen", "start": "2025-01-03", "end": "2031-01-03", "state": "OH", "party": "Republican", "class": 1}]}] * 100
    good = H.load_members(conn, "2026-06-01", [119], fetch=lambda: big)
    assert len(good["house"]) == 430

    def down():
        raise OSError("network down")
    assert H.load_members(conn, "2026-06-01", [119], fetch=down)["house"] == good["house"]


def _path(**over):
    p = {"all": {"intro": 1000, "committee": 120, "p1": 80, "p2": 30, "pres": 29, "law": 28, "veto": 1},
         "p1_no_committee": 10, "law_month": [1] * 23 + [8, 2], "fast": {"house_p1": 50, "house_susp": 40, "senate_p1": 20, "senate_uc": 15},
         "by_congress": {113: {"intro": 100, "law": 10}, 118: {"intro": 180, "law": 10}}, "first": 113, "last": 118, "years": [2013, 2025],
         "naming": 7}
    p.update(over)
    return p


def test_titles_follow_the_data():
    C = {"rows": [{"unified": True, "laws": 12, "done": True}, {"unified": False, "laws": 9, "done": True}]}
    t = H.titles(_path(), C)
    assert t["gates"].startswith("Committee is the hardest gate. A bill")
    assert t["calendar"] == "More than a quarter of each Congress's laws pass in its final December."
    assert t["trend"] == "Members file 80% more bills than they did in 2013. Congress enacts about the same number."
    assert t["naming"].startswith("Nearly one law in four")
    assert t["control"].startswith("When one party held")
    # the other branches
    t2 = H.titles(_path(law_month=[1] * 25, by_congress={113: {"intro": 100, "law": 10}, 118: {"intro": 100, "law": 10}}, naming=1),
                  {"rows": [{"unified": True, "laws": 5, "done": True}, {"unified": False, "laws": 9, "done": True}]})
    assert "no single rush" in t2["calendar"]
    assert t2["trend"].startswith("Members file about as many")
    assert t2["naming"].startswith("Some laws")
    assert t2["control"].startswith("Unified control")


@pytest.mark.parametrize("dec,expect", [(1, "no single rush"), (2, "more laws in its final December")])
def test_calendar_middle_branch(dec, expect):
    lm = [1] * 23 + [dec, 0]
    assert expect in H.titles(_path(law_month=lm, all={**_path()["all"], "law": 100}), {"rows": []})["calendar"]
