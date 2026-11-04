import sqlite3

from ingest import gdelt


def test_backs_off_and_keeps_going_after_repeated_429(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(gdelt.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(gdelt.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    replies = iter([gdelt.Throttled(), gdelt.Throttled(), {"timeline": [{"data": [{"date": "20261101T000000Z", "value": 0}]}]}])

    def fake(params, timeout=60):
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(gdelt, "_call", fake)
    monkeypatch.setattr(gdelt, "pick_bills", lambda conn, c, n: [
        {"bill_id": "119-hr-1", "bill_type": "hr", "number": 1, "title": "Rural Broadband Expansion Act",
         "sponsor_name": "Rep. Hill, J. French [R-AR-2]", "latest_action_date": "2026-11-01", "fetched_at": None}])
    conn = sqlite3.connect(":memory:")
    conn.executescript(open(gdelt.Path(gdelt.__file__).with_name("schema.sql")).read())
    conn.execute("PRAGMA foreign_keys = OFF")
    stats = gdelt.run(conn, 119, max_calls=10, spacing=0, minutes=55)
    assert stats["throttled"] == 2 and stats["bills"] == 1
    assert clock[0] == 180 + 360  # 3 then 6 minutes
