"""Page titles that state a claim, generated from the numbers each night (decision L2).

Every template has branches for when the data points the other way, and `verify`
re-checks each generated sentence against the numbers it was built from. The export
fails (and the site is not deployed) if a sentence would say something false.
"""

from __future__ import annotations

import re

WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def word(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def pct(x: float, d: int = 1) -> str:
    s = f"{x * 100:.{d}f}"
    return (s[:-2] if s.endswith(".0") else s) + "%"


def landing(cur: int, laws_now: int, prior: dict[int, int]) -> dict:
    """prior: {congress: laws by the same day of its term} for the earlier Congresses."""
    k = len(prior)
    more = sum(v > laws_now for v in prior.values())     # earlier Congresses that had more by now
    fewer = sum(v < laws_now for v in prior.values())
    head = f"The {ordinal(cur)} Congress has enacted {laws_now} {'law' if laws_now == 1 else 'laws'} so far"
    if k == 0:
        tail, kind = "", "none"
    elif more == k:
        tail, kind = f", fewer than at the same point in any of the last {word(k)} Congresses", "lowest"
    elif fewer == k:
        tail, kind = f", more than at the same point in any of the last {word(k)} Congresses", "highest"
    elif more > k / 2:
        tail, kind = f", fewer than at the same point in {word(more)} of the last {word(k)} Congresses", "below"
    elif fewer > k / 2:
        tail, kind = f", more than at the same point in {word(fewer)} of the last {word(k)} Congresses", "above"
    else:
        tail, kind = f", in line with the last {word(k)} Congresses at the same point", "middle"
    lead = {"lowest": "This is the slowest pace since 2013.", "highest": "This is the fastest pace since 2013.",
            "below": "The pace is below the norm, not a record low.", "above": "The pace is above the norm, not a record high.",
            "middle": "The pace is about normal.", "none": ""}[kind]
    return {"text": head + tail, "kind": kind, "more": more, "fewer": fewer, "k": k, "laws": laws_now, "lead": lead}


def directory(congress: int, introduced: int, enacted: int, current: bool) -> str:
    share = pct(enacted / introduced) if introduced else "0%"
    verb = "have become" if current else "became"
    return f"{introduced:,} bills in the {ordinal(congress)} Congress; {share} {verb} law"


def outcomes(rate_bip: float, rate_one: float) -> dict:
    ratio = rate_bip / rate_one if rate_one else 0
    if ratio >= 1.5:
        t = f"Bills with even one cosponsor from the other party become law {round(ratio)} times as often as one-party bills"
        if round(ratio) == 1:
            t = "Bills with even one cosponsor from the other party become law more often than one-party bills"
    elif ratio > 1:
        t = "Bills with a cosponsor from the other party become law somewhat more often than one-party bills"
    else:
        t = "Bills with a cosponsor from the other party do not become law more often than one-party bills"
    return {"text": t, "ratio": ratio}


def network(name: str, n: int, top_area: str | None, top_n: int) -> str:
    if n == 0:
        return f"The {name} has no recorded links to other bills"
    base = f"The {name} connects to {n:,} other {'bill' if n == 1 else 'bills'}"
    if top_area and top_n >= 2 and top_n / n >= 0.25:
        return base + f", {top_n:,} of them in {top_area.lower()} policy"
    return base


def coverage(top3_laws: int | None, n_rows: int) -> str:
    if top3_laws is None or n_rows < 3:
        return "Coverage beside progress for bills in the current Congress"
    if top3_laws <= 1:
        return f"Coverage and progress rarely line up: {word(3 - top3_laws)} of the 3 most-covered bills have not become law"
    return f"The most-covered bills are mostly laws: {word(top3_laws)} of the top 3 were enacted"


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def verify(sentence: str, allowed: list) -> None:
    """Every number in the sentence must be one of the numbers it was built from."""
    ok = set()
    for a in allowed:
        if isinstance(a, float):
            ok.add(pct(a))
            ok.add(pct(a).rstrip("%"))
        ok.add(f"{a:,}" if isinstance(a, int) else str(a))
        ok.add(str(a))
    for m in _NUM.finditer(sentence):
        tok = m.group(0)
        if tok in ok or tok + "%" in ok or tok.rstrip("thsndr") in ok:
            continue
        raise AssertionError(f"claim {sentence!r} quotes {tok}, which is not in its data {allowed}")
