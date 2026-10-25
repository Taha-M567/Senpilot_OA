"""Check labelled fixtures locally; --live opts in to paid model ranking calls."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from senpilot_agent.catalog import _candidate, finalize_catalog
from senpilot_agent.collection import CATEGORIES
from senpilot_agent.llm import LLMConfig, rank_documents
from senpilot_agent.selection import EmailRequest, extract_matter_number, validate_selection


FIXTURES = Path(__file__).resolve().parents[1] / "tests/fixtures/selection-evals.json"


def case_catalog(fixtures, case):
    counts = dict.fromkeys(CATEGORIES, 0)
    documents = []
    for source in fixtures["documents"]:
        ref = source["reference"]
        if ref in case.get("omit_refs", []) or ("only_refs" in case and ref not in case["only_refs"]):
            continue
        category = source["category"]
        documents.append(_candidate(category, {
            "identifiers": [ref], "titles": source.get("witnesses", [source.get("title", "")]),
            "dates": [source.get("date") or ""], "security": ["Public"], "extensions": [],
            "listing_position": counts[category], "raw_text": json.dumps(source),
        }, len(documents) + 1))
        counts[category] += 1
    return finalize_catalog(fixtures["matter_number"], counts, documents)


def judge(case, selection, catalog):
    request = validate_selection(selection, catalog)
    expected = case["expected"]
    errors = []
    if selection["status"] != expected["status"]:
        errors.append(f"Expected status {expected['status']}, observed {selection['status']}")
    refs = [d.reference for d in request.documents] if request else []
    if not expected.get("min_count", 0) <= len(refs) <= expected.get("max_count", 10):
        errors.append("Selection quantity outside labelled bounds")
    if not set(expected.get("required_refs", [])).issubset(refs):
        errors.append("Missing required document")
    if any(not set(group).intersection(refs) for group in expected.get("required_any", [])):
        errors.append("Missing required purpose/match group")
    if "allowed_refs" in expected and not set(refs).issubset(expected["allowed_refs"]):
        errors.append("Selection outside allowed matches")
    if set(expected.get("forbidden_refs", [])).intersection(refs):
        errors.append("Selected an excluded document")
    tiers = expected.get("tiers", [])
    for earlier, later in zip(tiers, tiers[1:]):
        a = [refs.index(ref) for ref in earlier if ref in refs]
        b = [refs.index(ref) for ref in later if ref in refs]
        if a and b and max(a) >= min(b):
            errors.append("Requested preference tier order was reversed")
    return {"name": case["name"], "passed": not errors, "errors": errors, "references": refs}


async def evaluate(fixtures, cases, root):
    config = LLMConfig.from_env()
    results = []
    for case in cases:
        directory = root / case["name"]
        directory.mkdir(parents=True)
        catalog = case_catalog(fixtures, case)
        request = EmailRequest("", case["body"])
        report = {}
        try:
            selection = await rank_documents(request, catalog, config, directory, report)
            result = judge(case, selection, catalog)
        except Exception as exc:
            result = {"name": case["name"], "passed": False, "errors": [type(exc).__name__ + ": " + str(exc)]}
        (directory / "evaluation.json").write_text(json.dumps({"case": case, "result": result, "report": report}, indent=2), encoding="utf-8")
        results.append(result)
        print(f"{case['name']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
    (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return all(r["passed"] for r in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Make paid model requests for each selected case")
    parser.add_argument("--case", help="Run just one named case")
    args = parser.parse_args()
    fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
    cases = [case for case in fixtures["cases"] if args.case is None or case["name"] == args.case]
    if not cases:
        parser.error("Unknown evaluation case")
    for case in cases:
        assert extract_matter_number("", case["body"]) == fixtures["matter_number"]
        assert case_catalog(fixtures, case)["complete"]
    if not args.live:
        print(f"Validated {len(cases)} labelled fixtures. No model calls; selection quality remains unmeasured. Use --live to evaluate.")
        return 0
    root = Path("artifacts") / ("selection-evals-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    print(f"Evidence: {root.resolve()}")
    return 0 if asyncio.run(evaluate(fixtures, cases, root)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
