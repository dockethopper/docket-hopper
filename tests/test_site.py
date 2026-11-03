"""Stage 2: claims (L2), model bands, network layout, coverage match check."""

import numpy as np
import pytest

from ingest import claims, gdelt, model as M, network as N


def test_landing_claim_branches():
    prior = {113: 185, 114: 244, 115: 273, 116: 182, 117: 214, 118: 106}
    c = claims.landing(119, 139, prior)
    assert c["text"] == "The 119th Congress has enacted 139 laws so far, fewer than at the same point in five of the last six Congresses"
    assert c["kind"] == "below"
    assert claims.landing(119, 100, prior)["kind"] == "lowest"
    assert "more than at the same point in any" in claims.landing(119, 300, prior)["text"]
    assert claims.landing(119, 200, prior)["kind"] == "middle"
    for laws in range(0, 320, 7):
        claims.verify(claims.landing(119, laws, prior)["text"], [119, laws])


def test_verify_rejects_a_number_not_in_the_data():
    with pytest.raises(AssertionError):
        claims.verify("The 119th Congress has enacted 140 laws so far", [119, 139])


def test_other_claims():
    assert claims.directory(119, 17060, 139, True) == "17,060 bills in the 119th Congress; 0.8% have become law"
    assert claims.directory(117, 100, 10, False).endswith("10% became law")
    assert claims.outcomes(0.05, 0.01)["text"].startswith("Bills with even one cosponsor from the other party become law 5 times")
    assert "do not" in claims.outcomes(0.01, 0.02)["text"]
    assert claims.network("X Act", 10, "Health", 2) == "The X Act connects to 10 other bills"
    assert claims.network("X Act", 10, "Health", 6).endswith("6 of them in health policy")
    assert claims.coverage(None, 0).startswith("Coverage beside progress")
    assert "two of the 3" in claims.coverage(1, 10)


def test_bands():
    assert [M.band_of(p) for p in (0.001, 0.02, 0.05, 0.5, 1.0)] == [0, 1, 2, 3, 3]
    assert M.live_stage("passed_one") == 2 and M.live_stage("became_law") is None


def test_layout_is_deterministic_and_in_bounds():
    adj = N.adjacency([("a", "b", "Identical bill"), ("b", "c", "Related bill"), ("a", "d", "Related bill"), ("d", "e", "Related bill")])
    nodes, edges = N.neighborhood(adj, "a")
    assert nodes == {"a": 0, "b": 1, "d": 1, "c": 2, "e": 2}
    ids = list(nodes)
    p1 = N.layout(ids, edges, [5] * len(ids), seed_key="a")
    p2 = N.layout(ids, edges, [5] * len(ids), seed_key="a")
    assert np.allclose(p1, p2)
    assert (p1[:, 0] >= 0).all() and (p1[:, 0] <= N.W).all() and (p1[:, 1] >= 0).all() and (p1[:, 1] <= N.H).all()
    cache = {}
    out = N.layout_many([("a", ids, edges, [5] * 5)], cache, workers=1)
    assert N.decode_xy(out["a"]) and "a" in cache


def test_coverage_match_check():
    row = {"bill_id": "119-s-5", "bill_type": "s", "number": 5, "title": "Laken Riley Act", "sponsor_name": "Sen. Britt, Katie Boyd [R-AL]"}
    q, generic = gdelt.build_query(row, {})
    assert q == '"Laken Riley Act" (Britt OR "S. 5") sourcecountry:US' and not generic
    assert gdelt.build_query({**row, "title": "SAVE Act"}, {}) == (None, True)
    assert gdelt.build_query({**row, "title": "To amend title 38, United States Code"}, {}) == (None, False)
    assert gdelt.build_query(row, {"119-s-5": {"query": "x"}}) == ("x", False)
