"""Inspect matters, download supplied priorities, and create delivery ZIPs."""

import argparse
import asyncio
from pathlib import Path
import re
import sys

from .probe import inspect_site
from .collection import load_collection_request


def matter_argument(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"M[0-9]{5}", value):
        raise argparse.ArgumentTypeError("--matter must be M followed by exactly five ASCII digits")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect UARB, read matter details, or download supplied document priorities into a ZIP.",
    )
    request = parser.add_mutually_exclusive_group()
    request.add_argument("--matter", type=matter_argument, help="Matter number, for example M12205")
    request.add_argument("--collection-request", type=Path,
                         help="JSON with matter, document IDs/priorities, and limit; download and ZIP")
    parser.add_argument("--download-one", action="store_true",
                        help="Save and validate the first Other Documents file (requires --matter)")
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    parser.add_argument("--headed", action="store_true", help="Show the browser for debugging")
    parser.add_argument("--channel", choices=["chrome", "msedge"], help="Use installed Chrome or Edge")
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--inspect-seconds", type=int, default=0,
                        help="Debugging pause after inspection or search completes (maximum 60 seconds)")
    args = parser.parse_args()
    if args.download_one and args.matter is None:
        parser.error("--download-one requires --matter")
    if not 1000 <= args.timeout_ms <= 120_000:
        parser.error("--timeout-ms must be between 1000 and 120000")
    if not 0 <= args.inspect_seconds <= 60:
        parser.error("--inspect-seconds must be between 0 and 60")
    collection = None
    if args.collection_request is not None:
        try:
            collection = load_collection_request(args.collection_request)
        except (OSError, ValueError, TypeError) as exc:
            parser.error(f"Invalid collection request: {exc}")
    directory, report = asyncio.run(inspect_site(
        args.output, headed=args.headed, channel=args.channel,
        timeout_ms=args.timeout_ms, inspect_seconds=args.inspect_seconds,
        matter_number=args.matter,
        download_one=args.download_one,
        collection_request=collection,
    ))
    print(f"Status: {report['status']}; stage: {report['stage']}")
    print(f"Evidence: {directory.resolve() / 'report.json'}")
    if report.get("verified_matter_number"):
        print(f"Verified matter: {report['verified_matter_number']}")
    if report.get("metadata_counts_complete"):
        for name, value in report["matter_metadata"].items():
            print(f"{name.replace('_', ' ').capitalize()}: {value if value is not None else '(not provided)'}")
        for category, count in report["category_counts"].items():
            print(f"{category}: {count}")
        print(f"Total documents: {report['total_document_count']}")
        if args.collection_request is not None:
            print(f"Downloaded and validated: {report['downloaded_document_count']} document(s)")
        elif report.get("phase1_milestone_complete"):
            print(f"Downloaded and validated: {report['downloaded_document_count']} Other Documents file(s)")
            for document in report["downloaded_documents"]:
                print(f"Document: {directory.resolve() / document['path']}")
        else:
            print("Metadata and category counts captured; download remains unverified.")
    else:
        print("Metadata, category counts, and download remain unverified.")
    if report.get("archive"):
        print(f"Delivery ZIP: {directory.resolve() / report['archive']['path']}")
    if report.get("document_outcomes"):
        for outcome in report["document_outcomes"]:
            if outcome["status"] != "downloaded":
                reference = outcome['document'].get('document_number') or outcome['document'].get('hearing_date')
                print(f"Document {reference}: {outcome['status']}")
    if report["status"] in {"failed", "collection_partial"}:
        encoding = sys.stdout.encoding or "utf-8"
        message = report.get("error", {}).get("message", "Collection is partial; see document_outcomes in report.json")
        print(message.encode(encoding, errors="backslashreplace").decode(encoding))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
