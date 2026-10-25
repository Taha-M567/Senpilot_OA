"""Standalone Phase 1 inspection and matter-details entry point."""

import argparse
import asyncio
from pathlib import Path
import re
import sys

from .probe import inspect_site


def matter_argument(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"M[0-9]{5}", value):
        raise argparse.ArgumentTypeError("--matter must be M followed by exactly five ASCII digits")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect UARB or collect verified matter metadata and counts, saving evidence.",
    )
    parser.add_argument("--matter", type=matter_argument, help="Matter number, for example M12205")
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
    directory, report = asyncio.run(inspect_site(
        args.output, headed=args.headed, channel=args.channel,
        timeout_ms=args.timeout_ms, inspect_seconds=args.inspect_seconds,
        matter_number=args.matter,
        download_one=args.download_one,
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
        if report.get("phase1_milestone_complete"):
            print(f"Downloaded and validated: {report['downloaded_document_count']} Other Documents file(s)")
            for document in report["downloaded_documents"]:
                print(f"Document: {directory.resolve() / document['path']}")
        else:
            print("Metadata and category counts captured; download remains unverified.")
    else:
        print("Metadata, category counts, and download remain unverified.")
    if report["status"] == "failed":
        encoding = sys.stdout.encoding or "utf-8"
        print(report["error"]["message"].encode(encoding, errors="backslashreplace").decode(encoding))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
