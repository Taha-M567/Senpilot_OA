"""Run email requests from a local JSON inbox file."""

import argparse
import asyncio
from pathlib import Path
import sys

from .email_flow import process_emails, read_emails


def main() -> int:
    parser = argparse.ArgumentParser(description='Process document requests from a JSON inbox file.')
    parser.add_argument('--emails-json', required=True, type=Path, help='Path to a UTF-8 JSON inbox file')
    parser.add_argument('--output', type=Path, default=Path('artifacts'))
    parser.add_argument('--headed', action='store_true', help='Show the browser')
    parser.add_argument('--channel', choices=['chrome', 'msedge'])
    parser.add_argument('--timeout-ms', type=int, default=30_000)
    parser.add_argument('--inspect-seconds', type=int, default=0)
    args = parser.parse_args()
    if args.emails_json == Path('-'):
        parser.error('--emails-json requires a file path; standard input is not supported')
    if not 1000 <= args.timeout_ms <= 120_000:
        parser.error('--timeout-ms must be between 1000 and 120000')
    if not 0 <= args.inspect_seconds <= 60:
        parser.error('--inspect-seconds must be between 0 and 60')
    try:
        inbox_text = args.emails_json.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError) as exc:
        parser.error(f'Cannot read --emails-json ({type(exc).__name__})')
    try:
        read_emails(inbox_text)
    except ValueError:
        parser.error('--emails-json must contain a nonempty emails object with unique IDs and '
                     'nonempty subject/content strings; no other fields are supported')
    try:
        directory, replies, exit_code = asyncio.run(process_emails(
            inbox_text, args.output, headed=args.headed, channel=args.channel,
            timeout_ms=args.timeout_ms, inspect_seconds=args.inspect_seconds,
        ))
    except OSError as exc:
        print(f'Cannot write email replies ({type(exc).__name__}).', file=sys.stderr)
        return 1
    print(f"Processed {len(replies['emails'])} email(s).")
    print(f"Replies: {directory.resolve() / 'replies.json'}")
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
