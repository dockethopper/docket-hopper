"""Derive a single outcome status for a bill from its action history.

Statuses, furthest-along first:
    became_law       enacted (signed, or became law without signature)
    veto_overridden  vetoed, then Congress overrode the veto
    vetoed           vetoed by the President, not overridden
    pocket_vetoed    pocket vetoed
    to_president     presented to the President, no outcome yet
    passed_both      passed both chambers (also: concurrent/joint res. agreed to)
    agreed_to        simple resolution adopted by its chamber
    passed_one       passed one chamber
    failed           failed a floor vote
    reported         reported out of committee
    introduced       nothing beyond introduction/referral
"""

from __future__ import annotations

import re

_PASS_RE = re.compile(
    r"(passed/agreed to in (house|senate)|passed (house|senate)|"
    r"resolution agreed to in (house|senate)|"
    r"(house|senate) (passed|agreed to)|"
    r"on passage passed|on agreeing to the resolution agreed to)",
    re.I,
)
_RECEIVED_RE = re.compile(r"received in the (house|senate)", re.I)
_REPORTED_RE = re.compile(r"(reported by|reported \(amended\)|reported with an amendment|reported without amendment|ordered to be reported)", re.I)
_FAILED_RE = re.compile(r"(failed of passage|failed by (yea-nay|recorded|voice)|motion to proceed .*not agreed to)", re.I)

SIMPLE_RES = {"hres", "sres"}


def _chamber(action: dict) -> str | None:
    text = (action.get("text") or "").lower()
    m = re.search(r"\b(house|senate)\b", text)
    if m:
        return m.group(1)
    ch = (action.get("chamber") or "").lower()
    if "house" in ch:
        return "house"
    if "senate" in ch:
        return "senate"
    return None


def derive_status(bill_type: str, actions: list[dict], has_law: bool = False) -> str:
    texts = [(a.get("text") or "") for a in actions]
    joined = "\n".join(texts).lower()

    vetoed = "vetoed by president" in joined or "veto message received" in joined
    overridden = bool(re.search(r"(passed|agreed to).{0,60}over (the )?(president'?s )?veto", joined)) or \
        "two-thirds of the members present having voted in the affirmative" in joined and vetoed

    if has_law or "became public law" in joined or "became private law" in joined:
        return "veto_overridden" if vetoed else "became_law"
    if "pocket vetoed" in joined:
        return "pocket_vetoed"
    if vetoed:
        return "veto_overridden" if overridden else "vetoed"
    if "presented to president" in joined:
        return "to_president"

    passed: set[str] = set()
    for a in actions:
        t = a.get("text") or ""
        if _PASS_RE.search(t):
            ch = _chamber(a)
            if ch:
                passed.add(ch)
        # "Received in the Senate" means the House already passed it, and vice versa.
        m = _RECEIVED_RE.search(t)
        if m:
            passed.add("house" if m.group(1).lower() == "senate" else "senate")

    if bill_type in SIMPLE_RES and passed:
        return "agreed_to"
    if len(passed) >= 2:
        return "passed_both"
    if passed:
        return "passed_one"
    if any(_FAILED_RE.search(t) for t in texts):
        return "failed"
    if any(_REPORTED_RE.search(t) for t in texts):
        return "reported"
    return "introduced"
