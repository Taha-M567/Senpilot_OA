"""Process a local JSON inbox and write replies without sending email."""

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .planner import plan_email


def read_emails(text: str) -> dict[str, dict[str, str]]:
    """Validate the whole inbox before starting any browser or AI work."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('JSON objects must not contain duplicate keys')
            result[key] = value
        return result

    data = json.loads(text, object_pairs_hook=unique_object)
    if not isinstance(data, dict) or set(data) != {'emails'}:
        raise ValueError('JSON must contain an emails object')
    emails = data['emails']
    if not isinstance(emails, dict) or not emails:
        raise ValueError('emails must be a nonempty object keyed by email ID')
    for email_id, message in emails.items():
        if (not email_id.strip() or not isinstance(message, dict)
                or set(message) != {'subject', 'content'}
                or any(not isinstance(message[name], str) or not message[name].strip()
                       for name in ('subject', 'content'))):
            raise ValueError('Each email needs a nonempty ID, subject, and content; no other fields are supported')
    return emails


def format_reply(subject: str, directory: Path | None, report: dict) -> dict[str, str]:
    """Build an email body from checked results, without another model call."""
    status = report['status']
    opening = {
        'completed': 'Your document request is complete.',
        'clarification': 'I need more information to complete your request.',
        'partial_review': 'Your request is partially complete. Some steps could not be completed; see the details below.',
        'failed': 'I could not complete your document request.',
    }
    lines = [opening.get(status, 'Your document request did not complete.')]
    if report.get('basis'):
        lines.extend(['', report['basis']])
    if report.get('clarification_question'):
        lines.extend(['', report['clarification_question']])
    if report.get('error'):
        lines.extend(['', report['error']['message']])
    documents = report.get('documents', report.get('ranked_selections', []))
    if all(name in report for name in ('downloaded_count', 'reviewed_count', 'failed_count')):
        lines.extend(['', f"Downloaded: {report['downloaded_count']}; reviewed: {report['reviewed_count']}; "
                      f"failed: {report['failed_count']}."])
    if 'documents' in report and documents:
        lines.append('Document order follows the initial title and metadata ranking.')
    if status == 'completed' and not documents:
        lines.extend(['', 'No matching documents were found.'])
    for index, item in enumerate(documents, 1):
        lines.extend(['', f"{index}. {item['document_number']} - {item['title']}",
                      f"Categories: {', '.join(item['categories'])}; date: {item['date'] or 'not provided'}",
                      f"Why selected: {item['reason']}"])
        if item.get('status'):
            lines.append(f"Processing status: {item['status'].replace('_', ' ')}")
        if item.get('download') and directory is not None:
            lines.append(f"Downloaded file: {(directory / item['download']['relative_path']).resolve()}")
        if (item.get('download') or {}).get('sha256'):
            lines.append(f"SHA-256: {item['download']['sha256']}")
        if item.get('error'):
            lines.append(item['error']['message'])
        if item.get('extraction'):
            extraction = item['extraction']
            lines.append(f"PDF text extraction: {extraction['status']}; pages: {extraction['page_count']}; "
                         f"complete text coverage: {'yes' if extraction['complete_text'] else 'no'}.")
            for warning in extraction.get('warnings', []):
                lines.append(f'Extraction limitation: {warning}')
            if extraction.get('unreadable_pages'):
                lines.append('Pages without usable text: ' + ', '.join(map(str, extraction['unreadable_pages'])))
        review = item.get('review')
        if item.get('status') == 'reviewed' and review:
            lines.append(f"Relevance: {review['relevance'].replace('_', ' ')}")
            for finding in review['findings']:
                lines.append(f"Finding: {finding['statement']}")
                for citation in finding['citations']:
                    lines.append(f"  PDF page {citation['page']}: \"{citation['quote']}\"")
            for limitation in review['limitations']:
                lines.append(f'Limitation: {limitation}')
    if report.get('archive') and directory is not None:
        lines.extend(['', f"Selected files ZIP: {(directory / report['archive']['relative_path']).resolve()}"])
    if directory is not None:
        report_name = 'review.json' if 'documents' in report else 'plan.json'
        lines.extend(['', f"Detailed report: {(directory / report_name).resolve()}"])
    return {'subject': subject if subject.lower().startswith('re:') else f'Re: {subject}',
            'content': '\n'.join(lines)}


async def process_emails(text: str, output: Path = Path('artifacts'), **options) -> tuple[Path, dict, int]:
    """Run emails in input order and save each reply before starting the next email."""
    emails = read_emails(text)
    directory = Path(output) / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                                + '_' + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    replies = {'emails': {}}
    exit_code = 0
    for email_id, message in emails.items():
        run_directory = None
        try:
            run_directory, report = await plan_email(
                f"Subject: {message['subject']}\n\n{message['content']}", directory, **options,
            )
        except Exception as exc:
            report = {'status': 'failed', 'error': {
                'message': f'Email processing failed ({type(exc).__name__}); this email did not complete.'}}
        replies['emails'][email_id] = format_reply(message['subject'], run_directory, report)
        temporary = directory / 'replies.json.tmp'
        temporary.write_text(
            json.dumps(replies, indent=2, ensure_ascii=False), encoding='utf-8',
        )
        temporary.replace(directory / 'replies.json')
        if report['status'] not in {'completed', 'clarification'}:
            exit_code = 1
        elif report['status'] == 'clarification' and exit_code == 0:
            exit_code = 2
    return directory, replies, exit_code
