"""Enumerate complete, repeatable metadata snapshots of UARB virtual lists."""

import asyncio
import hashlib
import json
from time import monotonic

from .collection import CATEGORIES, DocumentPriority
from .retrieval import _ready_viewport, open_category
from .selection import RequestFailure


async def _sweep(page, category: str, count: int, timeout_ms: int) -> list[dict]:
    grid = page.locator(".v-grid").filter(has=page.get_by_role("grid"))
    scroller = grid.locator(".v-grid-scroller-vertical")
    await scroller.evaluate("e => {e.scrollTop=0}")
    seen = {}
    extension = {"Exhibits": ".fm_object_314 .text", "Key Documents": ".fm_object_284 .text",
                 "Other Documents": ".fm_object_308 .text"}.get(category, ".no_extension_field")
    for _ in range(count + 2):
        await _ready_viewport(grid, count, timeout_ms)
        rows = await grid.locator("tbody.v-grid-body > tr.v-grid-row-has-data").evaluate_all("""(rows, extension) => rows.map(r => {
            const texts = selector => [...r.querySelectorAll(selector)].map(e=>e.innerText.trim());
            const transform = getComputedStyle(r).transform;
            const y = transform==='none' ? r.offsetTop : new DOMMatrixReadOnly(transform).m42;
            return {position:y/r.offsetHeight,
                identifiers:texts('.fm_object_201 .text, .fm_object_268 .text, .fm_object_300 .text'),
                titles:texts('.fm_object_202 .text'), dates:texts('.fm_object_203 .text'),
                security:texts('.fm_object_204 .text'),
                extensions:texts(extension), raw_text:r.innerText.replace(/\\s+/g,' ').trim()};
        })""", extension)
        for row in rows:
            if not row["identifiers"] or not any(row["identifiers"]):
                continue
            position = row.pop("position")
            if position is None or abs(position - round(position)) > .01 or not 0 <= position < count:
                raise ValueError("List row has an invalid snapshot position")
            index = round(position)
            if index in seen and seen[index] != row:
                differences = {key: [seen[index][key], row[key]] for key in row if seen[index][key] != row[key]}
                raise ValueError(f"Document list changed while cataloging position {index}: {differences}")
            seen[index] = row
        state = await scroller.evaluate("e=>({top:e.scrollTop,height:e.clientHeight,max:e.scrollHeight})")
        if state["height"] == 0 or state["top"] + state["height"] >= state["max"] - 1:
            if set(seen) != set(range(count)):
                raise ValueError(f"Incomplete list coverage: {len(seen)} of {count} positions")
            return [{"listing_position": i, **seen[i]} for i in range(count)]
        moved = await scroller.evaluate("e=>{const old=e.scrollTop; e.scrollTop+=Math.max(1,Math.floor(e.clientHeight*.75)); return e.scrollTop>old}")
        if not moved:
            raise ValueError("Catalog traversal made no progress")
    raise ValueError("Catalog traversal exceeded its bounded step limit")


def _candidate(category: str, row: dict, index: int) -> dict:
    def optional_single(key):
        values = row[key]
        if len(values) > 1:
            raise ValueError(f"Ambiguous catalog field: {key}")
        return (values[0] or None) if values else None
    reference = optional_single("identifiers")
    security = optional_single("security")
    date_category = category in {"Transcripts", "Recordings"}
    titles = row["titles"]
    title = None if category == "Transcripts" else optional_single("titles")
    witnesses = [t for t in titles if t] if category == "Transcripts" else []
    return {
        "candidate_id": f"c{index:06d}", "category": category,
        "document_number": None if date_category else reference,
        "hearing_date": reference if date_category else None,
        "title": title, "witnesses": witnesses,
        "date": reference if date_category else optional_single("dates"),
        "date_label": "Hearing date" if date_category else "Date Filed" if category == "Exhibits" else "Date",
        "extension": optional_single("extensions"), "security": security,
        "listing_position": row["listing_position"], "raw_text": row["raw_text"],
        "eligible": False, "ineligible_reason": None, "download_reference": {},
    }


def finalize_catalog(matter_number: str, counts: dict, documents: list[dict]) -> dict:
    for category in CATEGORIES:
        entries = [d for d in documents if d["category"] == category]
        if len(entries) != counts[category]:
            raise ValueError("Catalog/category counts disagree")
        numbers = [d["document_number"] for d in entries if d["document_number"] is not None]
        if len(numbers) != len(set(numbers)):
            raise ValueError("Conflicting document identifiers within a category")
        for candidate in entries:
            dated = category in {"Transcripts", "Recordings"}
            title = candidate["title"]
            if category == "Transcripts":
                peers = [d for d in entries if d["hearing_date"] == candidate["hearing_date"]]
                title = next((w for w in candidate["witnesses"]
                              if sum(w in p["witnesses"] for p in peers) == 1), None)
            reference = {"hearing_date": candidate["hearing_date"]} if dated else {"document_number": candidate["document_number"]}
            if title:
                reference["title"] = title
            candidate["download_reference"] = reference
            reason = None
            if not candidate["security"] or candidate["security"].casefold() != "public":
                reason = "not_public"
            elif not title:
                reason = "missing_or_ambiguous_title"
            elif dated and category == "Recordings" and sum(
                d["hearing_date"] == candidate["hearing_date"] and d["title"] == title for d in entries
            ) != 1:
                reason = "ambiguous_hearing_reference"
            else:
                try:
                    DocumentPriority(**{"category": category, "document_number": None,
                                        "priority": 1, **reference})
                except (TypeError, ValueError):
                    reason = "invalid_download_reference"
            candidate.update(eligible=reason is None, ineligible_reason=reason)
    # The same native reference can be listed under Key and Other Documents.
    by_number = {}
    for candidate in documents:
        number = candidate["document_number"]
        if number:
            content = tuple(candidate[k] for k in ("title", "date", "security"))
            if number in by_number and by_number[number] != content:
                raise ValueError("Conflicting metadata for a cross-category document reference")
            by_number[number] = content
    fingerprint = hashlib.sha256(json.dumps({"matter_number": matter_number,
        "category_counts": counts, "documents": documents}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"schema_version": 1, "matter_number": matter_number, "category_counts": counts,
            "complete": True, "fingerprint": fingerprint, "documents": documents,
            "eligible_count": sum(d["eligible"] for d in documents)}


async def build_catalog(page, report: dict, timeout_ms: int) -> dict:
    started = monotonic()
    counts = report["category_counts"]
    documents = []
    try:
        # Enumeration is bounded independently of the size of a server-supplied count.
        async with asyncio.timeout(300):
            for category in CATEGORIES:
                count = counts[category]
                report["catalog_category"] = category
                if count == 0:
                    continue
                await open_category(page, category, count, timeout_ms)
                first = await _sweep(page, category, count, timeout_ms)
                second = await _sweep(page, category, count, timeout_ms)
                if first != second:
                    raise ValueError("Document list changed between catalog verification passes")
                offset = len(documents)
                documents.extend(_candidate(category, row, offset + i + 1)
                                 for i, row in enumerate(first))
            # Some document layouts leave another tab's count blank. Re-query
            # the verified matter-detail layout, where all five counts are exposed.
            from .probe import _search_matter, _extract_matter_details
            await page.get_by_role("button", name="Search", exact=True).click()
            refreshed = {}
            detail = await _search_matter(page, report["verified_matter_number"], refreshed)
            await _extract_matter_details(detail, refreshed, timeout_ms)
            if refreshed["category_counts"] != counts:
                raise ValueError("Matter category counts changed during catalog enumeration")
            result = finalize_catalog(report["verified_matter_number"], counts, documents)
            result["counts_reverified"] = True
            result["enumeration_seconds"] = round(monotonic() - started, 3)
            return result
    except Exception as exc:
        raise RequestFailure("catalog_incomplete", f"Cannot establish a complete catalog: {exc}") from exc


def model_catalog(catalog: dict) -> list[dict]:
    """All eligible candidates, without repeated matter data or browser evidence."""
    keys = ("candidate_id", "category", "document_number", "hearing_date", "title",
            "witnesses", "date", "date_label", "extension")
    return [{k: d[k] for k in keys if d.get(k) is not None and d.get(k) != []}
            for d in catalog["documents"] if d["eligible"]]
