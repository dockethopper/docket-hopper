"""Parse a govinfo BILLSTATUS XML document into plain dicts.

govinfo changed the BILLSTATUS layout in 2022 (e.g. <billType> -> <type>,
<billNumber> -> <number>, <summaries><billSummaries> -> <summaries><summary>).
Files for older Congresses were regenerated, but we accept both layouts so a
partial re-download never breaks the pipeline.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any

from .status import derive_status

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _text(el: ET.Element | None, *paths: str) -> str | None:
    """Return stripped text of the first path that exists under el."""
    if el is None:
        return None
    for p in paths:
        found = el.find(p)
        if found is not None and found.text and found.text.strip():
            return found.text.strip()
    return None


def _items(el: ET.Element | None, *paths: str) -> list[ET.Element]:
    """Return <item> children of the first container path that exists."""
    if el is None:
        return []
    # Old and new layouts nest some lists differently (committees/billCommittees),
    # so take the first path that actually has items.
    for p in paths:
        found = el.find(p)
        if found is not None and (items := found.findall("item")):
            return items
    return []


def strip_html(s: str | None) -> str | None:
    if not s:
        return s
    s = html.unescape(_TAG_RE.sub(" ", s))
    return _WS_RE.sub(" ", s).strip()


def make_bill_id(congress: int | str, bill_type: str, number: int | str) -> str:
    return f"{int(congress)}-{bill_type.lower()}-{int(number)}"


def _date(s: str | None) -> str | None:
    # Dates arrive as "2023-02-02" or "2023-02-02T10:11:12Z"; keep ISO, trim to date
    # for *_date fields, callers keep full timestamps where they want them.
    return s[:10] if s else None


def parse(xml_bytes: bytes) -> dict[str, Any]:
    root = ET.fromstring(xml_bytes)
    bill = root.find("bill")
    if bill is None:
        raise ValueError("BILLSTATUS document has no <bill> element")

    congress = int(_text(bill, "congress"))
    bill_type = (_text(bill, "type", "billType") or "").lower()
    number = int(_text(bill, "number", "billNumber"))
    bill_id = make_bill_id(congress, bill_type, number)

    # Sponsor (first only; joint sponsorship is not used in Congress).
    sponsor = (_items(bill, "sponsors") or [None])[0]

    # Actions, oldest first so seq is chronological.
    actions = []
    for it in _items(bill, "actions"):
        actions.append(
            {
                "action_date": _date(_text(it, "actionDate")),
                "action_code": _text(it, "actionCode"),
                "action_type": _text(it, "type"),
                "chamber": _text(it, "sourceSystem/name"),
                "text": _text(it, "text"),
            }
        )
    actions.sort(key=lambda a: a["action_date"] or "")
    for i, a in enumerate(actions):
        a["seq"] = i

    cosponsors = []
    for it in _items(bill, "cosponsors"):
        bio = _text(it, "bioguideId")
        if not bio:
            continue
        cosponsors.append(
            {
                "bioguide_id": bio,
                "name": _text(it, "fullName"),
                "party": _text(it, "party"),
                "state": _text(it, "state"),
                "sponsorship_date": _date(_text(it, "sponsorshipDate")),
                "is_original": 1 if (_text(it, "isOriginalCosponsor") or "").lower() == "true" else 0,
                "withdrawn_date": _date(_text(it, "sponsorshipWithdrawnDate")),
            }
        )

    subjects = sorted(
        {
            n
            for it in _items(bill, "subjects/legislativeSubjects", "subjects/billSubjects/legislativeSubjects")
            if (n := _text(it, "name"))
        }
    )

    related = []
    for it in _items(bill, "relatedBills"):
        r_type = _text(it, "type")
        r_num = _text(it, "number")
        r_cong = _text(it, "congress")
        if not (r_type and r_num and r_cong):
            continue
        rid = make_bill_id(r_cong, r_type, r_num)
        details = _items(it, "relationshipDetails") or [None]
        for d in details:
            related.append(
                {
                    "related_id": rid,
                    "relationship": _text(d, "type") or "Related bill",
                    "identified_by": _text(d, "identifiedBy"),
                }
            )

    committees = []
    for it in _items(bill, "committees", "committees/billCommittees"):
        code = _text(it, "systemCode")
        if code:
            committees.append({"system_code": code, "name": _text(it, "name"), "chamber": _text(it, "chamber")})

    # Summaries: keep the most recent one.
    summaries = bill.find("summaries")
    summary_items: list[ET.Element] = []
    if summaries is not None:
        summary_items = summaries.findall("summary") or _items(summaries, "billSummaries")
    summary_items.sort(key=lambda s: _text(s, "updateDate", "lastSummaryUpdateDate") or "")
    summary = strip_html(_text(summary_items[-1], "text")) if summary_items else None

    text_versions = []
    for it in _items(bill, "textVersions"):
        version = _text(it, "type") or "Unknown"
        date = _date(_text(it, "date"))
        urls = [u for f in _items(it, "formats") if (u := _text(f, "url"))]
        for u in urls or [None]:
            text_versions.append({"version": version, "date": date, "url": u})

    # Prefer an HTML rendering of the newest version for word counts later.
    latest_text_url = None
    dated = sorted((t for t in text_versions if t["url"]), key=lambda t: t["date"] or "")
    if dated:
        newest_date = dated[-1]["date"]
        newest = [t for t in dated if t["date"] == newest_date]
        htm = [t for t in newest if t["url"].endswith(".htm")]
        latest_text_url = (htm or newest)[0]["url"]

    law = (_items(bill, "laws") or [None])[0]

    short_title = None
    for it in _items(bill, "titles"):
        t_type = (_text(it, "titleType") or "").lower()
        if t_type.startswith("short title"):
            short_title = _text(it, "title")
            break

    latest_action = bill.find("latestAction")

    record = {
        "bill_id": bill_id,
        "congress": congress,
        "bill_type": bill_type,
        "number": number,
        "title": _text(bill, "title"),
        "short_title": short_title,
        "origin_chamber": _text(bill, "originChamber"),
        "introduced_date": _date(_text(bill, "introducedDate")),
        "update_date": _text(bill, "updateDate"),
        "policy_area": _text(bill, "policyArea/name"),
        "sponsor_bioguide": _text(sponsor, "bioguideId"),
        "sponsor_name": _text(sponsor, "fullName"),
        "sponsor_party": _text(sponsor, "party"),
        "sponsor_state": _text(sponsor, "state"),
        "cosponsor_count": len([c for c in cosponsors if not c["withdrawn_date"]]),
        "action_count": len(actions),
        "latest_action_date": _date(_text(latest_action, "actionDate")) or (actions[-1]["action_date"] if actions else None),
        "latest_action_text": _text(latest_action, "text") or (actions[-1]["text"] if actions else None),
        "law_type": _text(law, "type"),
        "law_number": _text(law, "number"),
        "summary": summary,
        "latest_text_url": latest_text_url,
    }
    record["status"] = derive_status(bill_type, actions, has_law=law is not None)

    return {
        "bill": record,
        "actions": actions,
        "cosponsors": cosponsors,
        "subjects": subjects,
        "related": related,
        "committees": committees,
        "text_versions": text_versions,
    }
