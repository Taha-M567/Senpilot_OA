"""CLI for document requests from email text or a local JSON inbox."""

import argparse
import asyncio
from pathlib import Path
import sys

from .planner import plan_email
from .email_flow import process_emails, read_emails


def main() -> int:
    parser = argparse.ArgumentParser(description='Recommend up to ten UARB documents per email from text or a JSON inbox.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--email-file', help='UTF-8 text file, or - for standard input')
    source.add_argument('--emails-json', help='JSON inbox with emails keyed by ID, or - for standard input')
    parser.add_argument('--limit', type=int, default=10, help='Maximum recommendations (1-10; default: 10)')
    parser.add_argument('--review', action='store_true', help='Download the shortlist, review PDF text, and create a ZIP')
    parser.add_argument('--output', type=Path, default=Path('artifacts'))
    parser.add_argument('--headed', action='store_true', help='Show the browser for debugging')
    parser.add_argument('--channel', choices=['chrome', 'msedge'])
    parser.add_argument('--timeout-ms', type=int, default=30_000)
    parser.add_argument('--inspect-seconds', type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        parser.error('--limit must be between 1 and 10')
    if not 1000 <= args.timeout_ms <= 120_000:
        parser.error('--timeout-ms must be between 1000 and 120000')
    if not 0 <= args.inspect_seconds <= 60:
        parser.error('--inspect-seconds must be between 0 and 60')
    input_path = args.emails_json if args.emails_json is not None else args.email_file
    input_flag = '--emails-json' if args.emails_json is not None else '--email-file'
    try:
        email_text = (sys.stdin.read() if input_path == '-'
                      else Path(input_path).read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError) as exc:
        parser.error(f'Cannot read {input_flag} ({type(exc).__name__})')
    if not email_text.strip():
        parser.error(f'{input_flag} must contain nonempty email input')
    if args.emails_json is not None:
        try:
            read_emails(email_text)
        except ValueError:
            parser.error('--emails-json must contain a nonempty emails object with unique IDs and '
                         'nonempty subject/content strings; no other fields are supported')
        try:
            directory, replies, exit_code = asyncio.run(process_emails(
                email_text, args.output, limit=args.limit, headed=args.headed, channel=args.channel,
                timeout_ms=args.timeout_ms, inspect_seconds=args.inspect_seconds, review=args.review,
            ))
        except OSError as exc:
            print(f'Cannot write email replies ({type(exc).__name__}).', file=sys.stderr)
            return 1
        print(f"Processed {len(replies['emails'])} email(s).")
        print(f"Replies: {directory.resolve() / 'replies.json'}")
        return exit_code
    try:
        directory, report = asyncio.run(plan_email(
            email_text, args.output, limit=args.limit, headed=args.headed, channel=args.channel,
            timeout_ms=args.timeout_ms, inspect_seconds=args.inspect_seconds,
            **({'review': True} if args.review else {}),
        ))
    except OSError as exc:
        print(f'Cannot write planning output ({type(exc).__name__}).', file=sys.stderr)
        return 1
    print(f"Status: {report['status']}")
    print(f"Plan: {directory.resolve() / 'plan.json'}")
    if args.review:
        print(f"Review: {directory.resolve() / 'review.json'}")
        print(f"Readable report: {directory.resolve() / 'review.md'}")
    print(report['basis'])
    if report['status'] == 'clarification':
        print(report['clarification_question'])
        return 2
    if report['status'] == 'failed':
        print(report['error']['message'])
        return 1
    if args.review:
        print(f"Downloaded {report['downloaded_count']}; reviewed {report['reviewed_count']}; "
              f"failed or unreviewed {report['failed_count']}.")
        if not report['documents'] and report['status'] == 'completed':
            print('No matching documents.')
        for index, item in enumerate(report['documents'], 1):
            print(f"{index}. {item['document_number']} | {item['title']} | {item['status']}")
            if item.get('error'):
                print(f"   {item['error']['message']}")
        if report.get('error'):
            print(report['error']['message'])
        if report['archive']:
            print(f"ZIP: {directory.resolve() / report['archive']['relative_path']}")
        return 0 if report['status'] == 'completed' else 1
    if not report['ranked_selections']:
        print('No matching documents.')
    for index, item in enumerate(report['ranked_selections'], 1):
        print(f"{index}. {item['document_number']} | {item['title']} | "
              f"{', '.join(item['categories'])} | {item['date'] or '(date not provided)'}")
        print(f"   {item['reason']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
