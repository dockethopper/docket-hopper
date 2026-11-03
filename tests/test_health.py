from ingest import health

BASE = {"bills": 100, "laws": 5, "vetoes": 2, "summaries": 80, "links": 200, "law_ids": ["119-hr-1", "119-s-2"],
        "by_congress": {"118": 60, "119": 40}, "data_date": "2026-11-01"}


def test_growth_passes():
    after = {**BASE, "bills": 103, "by_congress": {"118": 60, "119": 43}, "law_ids": BASE["law_ids"] + ["119-hr-9"]}
    assert health.compare(BASE, after, "2026-11-04") == []


def test_shrinking_data_blocks_publish():
    after = {**BASE, "bills": 90, "by_congress": {"118": 50, "119": 40}, "law_ids": ["119-hr-1"], "summaries": 70}
    probs = health.compare(BASE, after, "2026-11-04")
    assert any("bill count fell" in p for p in probs)
    assert any("118th Congress" in p for p in probs)
    assert any("119-s-2" in p for p in probs)
    assert any("summaries" in p for p in probs)


def test_stale_data_blocks_publish():
    assert health.compare(BASE, {**BASE, "data_date": "2026-08-01"}, "2026-11-04")
    assert health.stale("2026-10-20", "2026-11-04") == []


def test_snapshot_rotation_keeps_week_of_dailies_and_sunday_weeklies():
    names = [f"billboard-2026-{d}.sqlite.gz" for d in ("10-29", "10-28", "10-25", "09-13", "09-06")] + ["other.txt"]
    assert health.snapshots_to_delete(names, "2026-11-04") == ["billboard-2026-09-06.sqlite.gz", "billboard-2026-10-28.sqlite.gz"]
