from pathlib import Path

from ingest import db
from ingest.parse_billstatus import parse
from ingest.status import derive_status

FIX = Path(__file__).parent / "fixtures"


def test_new_layout_vetoed():
    p = parse((FIX / "new_layout_vetoed.xml").read_bytes())
    b = p["bill"]
    assert b["bill_id"] == "118-hjres-9001"
    assert b["status"] == "vetoed"
    assert b["cosponsor_count"] == 1  # withdrawn cosponsor excluded
    assert b["summary"] == "This joint resolution nullifies a rule."
    assert b["short_title"] == "Example Rule Disapproval Act"
    assert b["latest_text_url"].endswith("BILLS-118hjres9001enr.htm")
    assert [a["action_date"] for a in p["actions"]] == sorted(a["action_date"] for a in p["actions"])
    assert p["related"] == [{"related_id": "118-sjres-77", "relationship": "Identical bill", "identified_by": "CRS"}]
    assert p["subjects"] == ["Federal lands", "Mining"]


def test_old_layout_law():
    p = parse((FIX / "old_layout_law.xml").read_bytes())
    b = p["bill"]
    assert b["bill_id"] == "114-hr-4242"
    assert b["status"] == "became_law"
    assert b["law_number"] == "114-999"
    assert b["summary"] == "Funds roads."
    assert p["subjects"] == ["Highways"]
    assert p["committees"][0]["system_code"] == "hspw00"


def test_status_rules():
    a = lambda *ts: [{"text": t} for t in ts]
    assert derive_status("hr", a("Introduced in House")) == "introduced"
    assert derive_status("hr", a("Reported (Amended) by the Committee on X.")) == "reported"
    assert derive_status("hr", a("Passed/agreed to in House: On passage Passed")) == "passed_one"
    assert derive_status("hr", a("Passed/agreed to in House: ok", "Received in the Senate.")) == "passed_one"
    assert derive_status("s", a("Passed/agreed to in Senate: ok", "Passed/agreed to in House: ok")) == "passed_both"
    assert derive_status("hres", a("Passed/agreed to in House: Resolution agreed to")) == "agreed_to"
    assert derive_status("hr", a("Pocket Vetoed by President.")) == "pocket_vetoed"
    assert derive_status("hr", a("Vetoed by President.", "Passed Senate over veto by Yea-Nay Vote."), has_law=True) == "veto_overridden"


def test_upsert_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    p = parse((FIX / "new_layout_vetoed.xml").read_bytes())
    db.upsert_bill(conn, p, "test")
    conn.execute("UPDATE bills SET word_count = 123")
    db.upsert_bill(conn, p, "test")
    assert conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 5
    assert conn.execute("SELECT word_count FROM bills").fetchone()[0] == 123  # preserved, same text
