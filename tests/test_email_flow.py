import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from senpilot_agent import email_flow


def inbox(**messages):
    return json.dumps({'emails': messages})


def document(**changes):
    item = dict(candidate_id='C0001', document_number='123', title='Board Order',
                categories=['Key Documents'], date='01/02/2026', reason='Title identifies an order.')
    item.update(changes)
    return item


@pytest.mark.parametrize('text', [
    'not JSON', 'null', '[]', '{}', '{"emails": []}', '{"emails": {}}',
    '{"emails": {}, "extra": true}', '{"emails": {"": {"subject": "S", "content": "C"}}}',
    '{"emails": {"one": null}}', '{"emails": {"one": {"subject": "S"}}}',
    '{"emails": {"one": {"subject": 1, "content": "C"}}}',
    '{"emails": {"one": {"subject": "S", "content": "  "}}}',
    '{"emails": {"one": {"subject": "S", "content": "C", "extra": true}}}',
    '{"emails": {"one": {"subject": "S", "content": "C"}, "one": {"subject": "T", "content": "D"}}}',
    '{"emails": {"one": {"subject": "S", "subject": "T", "content": "C"}}}',
])
def test_invalid_inbox_stops_before_creating_output_or_calling_planner(text, monkeypatch, tmp_path):
    planner = AsyncMock()
    monkeypatch.setattr(email_flow, 'plan_email', planner)
    output = tmp_path / 'output'
    with pytest.raises(ValueError):
        asyncio.run(email_flow.process_emails(text, output))
    planner.assert_not_called()
    assert not output.exists()


def test_whole_inbox_is_validated_before_first_email(monkeypatch, tmp_path):
    planner = AsyncMock()
    monkeypatch.setattr(email_flow, 'plan_email', planner)
    text = inbox(first={'subject': 'Valid M12205', 'content': 'Recommend orders.'},
                 second={'subject': 'Invalid', 'content': ''})
    with pytest.raises(ValueError):
        asyncio.run(email_flow.process_emails(text, tmp_path))
    planner.assert_not_called()


def test_batch_includes_subject_preserves_ids_and_saves_reply_before_next_email(monkeypatch, tmp_path):
    calls = []
    run = tmp_path / 'document-run'

    async def plan(text, output, **options):
        calls.append(text)
        assert options == {'limit': 3, 'review': False}
        if len(calls) == 2:
            saved = json.loads((output / 'replies.json').read_text(encoding='utf-8'))
            assert list(saved['emails']) == ['email1']
        return run, {'status': 'completed', 'basis': 'Titles and metadata only.',
                     'ranked_selections': [document()]}

    monkeypatch.setattr(email_flow, 'plan_email', plan)
    source = inbox(email1={'subject': 'M12205 orders', 'content': 'Recommend three.'},
                   email2={'subject': 'Re: M12205', 'content': 'Find key documents with caf\u00e9 in the title.'})
    directory, replies, exit_code = asyncio.run(email_flow.process_emails(source, tmp_path, limit=3, review=False))
    assert calls == ['Subject: M12205 orders\n\nRecommend three.',
                     'Subject: Re: M12205\n\nFind key documents with caf\u00e9 in the title.']
    assert exit_code == 0
    assert list(replies) == ['emails']
    assert list(replies['emails']) == ['email1', 'email2']
    assert replies['emails']['email1']['subject'] == 'Re: M12205 orders'
    assert replies['emails']['email2']['subject'] == 'Re: M12205'
    assert set(replies['emails']['email1']) == {'subject', 'content'}
    body = replies['emails']['email1']['content']
    assert '123 - Board Order' in body and 'Why selected: Title identifies an order.' in body
    assert 'Titles and metadata only.' in body and str(run / 'plan.json') in body
    assert 'Finding:' not in body
    assert json.loads((directory / 'replies.json').read_text(encoding='utf-8')) == replies


def test_review_reply_includes_only_published_findings_and_local_attachment_paths(tmp_path):
    report = {'status': 'partial_review', 'basis': 'PDF text only.', 'documents': [
        document(status='reviewed', download={'relative_path': 'downloads/order.pdf', 'sha256': 'a' * 64}, review={
            'relevance': 'matches', 'findings': [{'statement': 'Approval requires reports.',
                'citations': [{'page': 2, 'quote': 'Semi-annual reports are required.'}]}],
            'limitations': ['Images were not reviewed.']}),
        document(document_number='456', title='Board Decision', status='review_failed',
                 download={'relative_path': 'downloads/decision.pdf'}, review=None,
                 error={'message': 'Gemini review failed (HTTP 503).'},
                 rejected_review={'findings': [{'statement': 'UNVERIFIED CLAIM'}]}),
        document(document_number='789', title='Scanned appendix', status='unreviewed',
                 download={'relative_path': 'downloads/scan.pdf'}, review=None,
                 extraction={'status': 'incomplete_text', 'page_count': 3, 'complete_text': False,
                    'warnings': ['Some pages may contain scanned images.'], 'unreadable_pages': [2]}),
    ], 'archive': {'relative_path': 'M12205_selected_documents.zip'},
       'downloaded_count': 3, 'reviewed_count': 1, 'failed_count': 2}
    reply = email_flow.format_reply('Order request', tmp_path, report)
    body = reply['content']
    assert 'partially complete' in body
    assert body.index('123 - Board Order') < body.index('456 - Board Decision')
    assert 'Approval requires reports.' in body
    assert 'PDF page 2: "Semi-annual reports are required."' in body
    assert 'Images were not reviewed.' in body
    assert 'Gemini review failed (HTTP 503).' in body
    assert 'UNVERIFIED CLAIM' not in body
    assert 'Downloaded: 3; reviewed: 1; failed: 2.' in body
    assert 'SHA-256: ' + 'a' * 64 in body
    assert 'PDF text extraction: incomplete_text; pages: 3; complete text coverage: no.' in body
    assert 'Extraction limitation: Some pages may contain scanned images.' in body
    assert 'Pages without usable text: 2' in body
    for path in ('downloads/order.pdf', 'downloads/decision.pdf', 'downloads/scan.pdf',
                 'M12205_selected_documents.zip', 'review.json'):
        assert str((tmp_path / path).resolve()) in body


@pytest.mark.parametrize(('first_status', 'second_status', 'expected'), [
    ('clarification', 'completed', 2), ('failed', 'completed', 1),
    ('partial_review', 'clarification', 1), ('clarification', 'failed', 1),
])
def test_per_email_results_continue_and_failure_exit_takes_precedence(
        monkeypatch, tmp_path, first_status, second_status, expected):
    def report(status):
        return dict(status=status, clarification_question='Which matter?', error={'message': 'API failed'},
                    ranked_selections=[])

    planner = AsyncMock(side_effect=[(tmp_path, report(first_status)), (tmp_path, report(second_status))])
    monkeypatch.setattr(email_flow, 'plan_email', planner)
    source = inbox(first={'subject': 'First', 'content': 'M12205'}, second={'subject': 'Second', 'content': 'M12205'})
    _, replies, exit_code = asyncio.run(email_flow.process_emails(source, tmp_path))
    assert planner.await_count == 2
    assert list(replies['emails']) == ['first', 'second']
    assert exit_code == expected
    if first_status == 'clarification':
        assert 'Which matter?' in replies['emails']['first']['content']


def test_unexpected_email_failure_is_sanitized_and_other_emails_continue(monkeypatch, tmp_path):
    planner = AsyncMock(side_effect=[PermissionError('sensitive internal text'),
                                     (tmp_path, {'status': 'completed', 'ranked_selections': []})])
    monkeypatch.setattr(email_flow, 'plan_email', planner)
    source = inbox(first={'subject': 'First', 'content': 'M12205'}, second={'subject': 'Second', 'content': 'M12205'})
    _, replies, exit_code = asyncio.run(email_flow.process_emails(source, tmp_path))
    assert exit_code == 1
    assert 'PermissionError' in replies['emails']['first']['content']
    assert 'sensitive internal text' not in json.dumps(replies)
    assert 'No matching documents were found.' in replies['emails']['second']['content']


def test_reply_write_failure_is_not_reported_as_success(monkeypatch, tmp_path):
    async def plan(text, output, **options):
        (output / 'replies.json').mkdir()
        return tmp_path, {'status': 'completed', 'ranked_selections': []}

    monkeypatch.setattr(email_flow, 'plan_email', plan)
    with pytest.raises(OSError):
        asyncio.run(email_flow.process_emails(inbox(one={'subject': 'Test', 'content': 'M12205'}), tmp_path))


def test_later_write_failure_preserves_previous_complete_reply_file(monkeypatch, tmp_path):
    planner = AsyncMock(return_value=(tmp_path, {'status': 'completed', 'ranked_selections': []}))
    monkeypatch.setattr(email_flow, 'plan_email', planner)
    original_write = Path.write_text
    writes = 0

    def write(path, *args, **kwargs):
        nonlocal writes
        if path.name == 'replies.json.tmp':
            writes += 1
            if writes == 2:
                original_write(path, 'incomplete JSON', encoding='utf-8')
                raise OSError('Disk full')
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', write)
    source = inbox(first={'subject': 'First', 'content': 'M12205'}, second={'subject': 'Second', 'content': 'M12205'})
    with pytest.raises(OSError):
        asyncio.run(email_flow.process_emails(source, tmp_path))
    saved = json.loads(next(tmp_path.glob('*/replies.json')).read_text(encoding='utf-8'))
    assert list(saved['emails']) == ['first']
