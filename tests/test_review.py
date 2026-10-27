import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import zipfile

import httpx
from openai import APITimeoutError, InternalServerError, RateLimitError
import pytest

from senpilot_agent import planner, review
from senpilot_agent.files import validate_download


TEXT = 'The Board approved the project subject to conditions.'


def result(candidate_id='C0001', **changes):
    data = dict(candidate_id=candidate_id, relevance='matches', limitations=[], findings=[
        review.Finding(statement='The project was approved subject to conditions.',
                       citations=[review.Citation(page=1, quote=TEXT)])])
    data.update(changes)
    return review.DocumentReview(**data)


def response(parsed, refusal=None, finish_reason='stop'):
    if isinstance(parsed, review.DocumentReview):
        parsed = review.ReferencedReview(candidate_id=parsed.candidate_id, relevance=parsed.relevance,
            limitations=parsed.limitations, findings=[review.ReferencedFinding(statement=finding.statement,
                citations=[review.CitationReference(source_id=(f'{parsed.candidate_id}-p1-s1'
                    if citation.page == 1 and citation.quote == TEXT else 'unknown-source'))
                    for citation in finding.citations]) for finding in parsed.findings])
    return SimpleNamespace(model=planner.DEFAULT_MODEL, _request_id='req_review', id='resp_review', usage=None,
        choices=[SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(parsed=parsed, refusal=refusal))])


@pytest.fixture
def workflow(tmp_path, pdf_bytes, monkeypatch):
    directory = tmp_path / 'run'
    directory.mkdir()
    records = [dict(candidate_id=f'C{i:04d}', document_number=str(1000 + i), title=f'Board Order {i}',
                    date='01/01/2026', categories=['Key Documents'], reason='Title identifies an order.') for i in (1, 2)]
    request = planner.EmailRequest(matter_numbers=['M12205'], categories=['Key Documents'], requested_count=2,
        topics=[], document_names=[], date_restrictions=[], ordering_preferences=[],
        clarification_question=None, review_questions=['Was the project approved?'])
    plan = dict(status='completed', stage='plan_complete', provider='gemini', model=planner.DEFAULT_MODEL,
                prompt_version=planner.PROMPT_VERSION, interpreted_request=request.model_dump(mode='json'),
                matter_metadata={'matter_number': 'M12205'}, category_counts={'Key Documents': 2},
                ranked_selections=records, calls=[])
    browser = dict(status='documents_ready', stage='selected_retrieval_complete', category_counts={'Key Documents': 3},
                   downloads=[], download_errors=[])
    for item in records:
        path = directory / 'downloads' / f"{item['candidate_id']}.pdf"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(pdf_bytes([TEXT]))
        browser['downloads'].append({**item, **validate_download(path).to_dict(),
            'relative_path': path.relative_to(directory).as_posix(), 'suggested_filename': 'order.pdf'})

    async def inspect(output, **kwargs):
        assert kwargs['selected_records'] == records
        assert kwargs['download_directory'] == directory
        return output / 'evidence', deepcopy(browser)

    monkeypatch.setattr(review, 'inspect_site', AsyncMock(side_effect=inspect))
    parse = AsyncMock(side_effect=[response(result('C0001')), response(result('C0002'))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    return directory, plan, browser, client, parse


def test_complete_review_reports_citations_counts_and_exact_archive(workflow):
    directory, plan, browser, client, parse = workflow
    report = asyncio.run(review.review_selected(directory, plan, client, planner._structured_call))
    assert report['status'] == 'completed'
    assert report['downloaded_count'] == report['reviewed_count'] == 2
    assert report['failed_count'] == 0
    assert report['category_count_changes'] == {'Key Documents': {'before': 2, 'after': 3}}
    assert [item['candidate_id'] for item in report['documents']] == ['C0001', 'C0002']
    assert all(item['extraction']['complete_text'] for item in report['documents'])
    assert len(report['calls']) == 2
    assert all(call['prompt_version'] == review.REVIEW_PROMPT_VERSION for call in report['calls'])
    payload = json.loads(parse.call_args_list[0].kwargs['messages'][1]['content'])
    assert payload['request']['review_questions'] == ['Was the project approved?']
    assert payload['pages'][0]['spans'] == [{'source_id': 'C0001-p1-s1', 'text': TEXT}]
    assert all('tools' not in call.kwargs for call in parse.call_args_list)
    assert json.loads((directory / 'review.json').read_text()) == report
    assert '[PDF page 1](downloads/C0001.pdf#page=1)' in (directory / 'review.md').read_text()
    assert 'original metadata ranking' in (directory / 'review.md').read_text()
    with zipfile.ZipFile(directory / report['archive']['relative_path']) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {'C0001.pdf', 'C0002.pdf'}
        assert all(archive.read(item['filename']) == (directory / item['relative_path']).read_bytes()
                   for item in browser['downloads'])


@pytest.mark.parametrize('bad', [
    response(result('invented')),
    response(result(findings=[review.Finding(statement='Unsupported finding.',
                    citations=[review.Citation(page=1, quote='The Board rejected the project.')])])),
    response(result(findings=[review.Finding(statement='Finding on an invented page.',
                    citations=[review.Citation(page=99, quote=TEXT)])])),
    response(None, refusal='Cannot review'), response(None), response({'invalid': 'schema'}),
    response(None, finish_reason='length'),
    APITimeoutError(request=httpx.Request('POST', planner.GEMINI_BASE_URL)),
    RateLimitError('secret-not-for-report', response=httpx.Response(429,
                   request=httpx.Request('POST', planner.GEMINI_BASE_URL)), body=None),
    InternalServerError('secret-not-for-report', response=httpx.Response(503,
                   request=httpx.Request('POST', planner.GEMINI_BASE_URL)), body=None),
])
def test_failed_review_is_visible_preserves_other_review_and_downloads(workflow, bad):
    directory, plan, browser, client, parse = workflow
    parse.side_effect = [bad, response(result('C0002'))]
    report = asyncio.run(review.review_selected(directory, plan, client, planner._structured_call))
    assert report['status'] == 'partial_review'
    assert report['reviewed_count'] == report['failed_count'] == 1
    assert report['downloaded_count'] == 2
    assert report['documents'][0]['status'] == 'review_failed'
    assert report['documents'][0]['review'] is None
    assert report['documents'][1]['status'] == 'reviewed'
    assert parse.await_count == 2
    assert report['archive']['document_count'] == 2
    assert 'secret-not-for-report' not in (directory / 'review.json').read_text()


@pytest.mark.parametrize(('payload', 'filename', 'status'), [
    (b'%PDF-1.7\nnot a parsed PDF\n%%EOF', 'broken.pdf', 'extraction_failed'),
    (b'media contents', 'recording.mp3', 'unsupported'),
])
def test_unreviewable_files_stay_downloaded_without_content_claims(workflow, payload, filename, status):
    directory, plan, browser, client, parse = workflow
    item = browser['downloads'][0]
    path = directory / 'downloads' / filename
    path.write_bytes(payload)
    item.update(**validate_download(path).to_dict(), relative_path=path.relative_to(directory).as_posix())
    parse.side_effect = [response(result('C0002'))]
    report = asyncio.run(review.review_selected(directory, plan, client, planner._structured_call))
    assert report['status'] == 'partial_review'
    assert report['documents'][0]['status'] == 'unreviewed'
    assert report['documents'][0]['extraction']['status'] == status
    assert report['archive']['document_count'] == 2
    assert parse.await_count == 1


def test_partial_download_retains_rank_and_only_archives_successes(workflow):
    directory, plan, browser, client, parse = workflow
    browser['download_errors'] = [dict(candidate_id='C0001', message='Selected record changed')]
    browser['downloads'] = browser['downloads'][1:]
    parse.side_effect = [response(result('C0002'))]
    report = asyncio.run(review.review_selected(directory, plan, client, planner._structured_call))
    assert report['status'] == 'partial_review'
    assert report['documents'][0]['status'] == 'download_failed'
    assert report['documents'][1]['status'] == 'reviewed'
    assert report['archive']['document_count'] == 1
    with zipfile.ZipFile(directory / report['archive']['relative_path']) as archive:
        assert archive.namelist() == ['C0002.pdf']


def test_browser_failure_is_visible_and_does_not_create_empty_archive(workflow):
    directory, plan, browser, client, parse = workflow
    browser.update(status='failed', downloads=[], stage='verify_matter')
    report = asyncio.run(review.review_selected(directory, plan, client, planner._structured_call))
    assert report['status'] == 'partial_review'
    assert report['failed_count'] == 2
    assert 'verify_matter' in report['error']['message']
    assert report['archive'] is None
    parse.assert_not_called()


def test_archive_failure_prevents_success_but_preserves_reviews(workflow, monkeypatch):
    directory, plan, browser, client, parse = workflow

    def fail(*args):
        raise OSError('Archive cannot be written')

    monkeypatch.setattr(review, 'create_archive', fail)
    report = asyncio.run(review.review_selected(directory, plan, client, planner._structured_call))
    assert report['status'] == 'partial_review'
    assert report['reviewed_count'] == 2
    assert report['archive'] is None
    assert 'Archive creation failed' in report['error']['message']


def test_changed_download_is_not_reviewed_or_archived(workflow):
    directory, plan, browser, client, parse = workflow
    path = directory / browser['downloads'][0]['relative_path']
    path.write_bytes(b'%PDF-1.7\nchanged contents\n%%EOF')
    parse.side_effect = [response(result('C0002'))]
    report = asyncio.run(review.review_selected(directory, plan, client, planner._structured_call))
    assert report['status'] == 'partial_review'
    assert report['documents'][0]['status'] == 'extraction_failed'
    assert 'checksum changed' in report['documents'][0]['error']['message']
    assert report['documents'][1]['status'] == 'reviewed'
    assert report['archive'] is None
    assert parse.await_count == 1


def test_interrupted_review_preserves_all_completed_downloads_and_manifest(workflow):
    directory, plan, browser, client, parse = workflow
    parse.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(review.review_selected(directory, plan, client, planner._structured_call))
    report = json.loads((directory / 'review.json').read_text())
    assert report['status'] == 'partial_review'
    assert report['downloaded_count'] == 2
    assert report['reviewed_count'] == 0
    assert all(item['status'] == 'unreviewed' for item in report['documents'])
    assert report['archive']['document_count'] == 2


def test_readable_output_failure_is_recorded_without_losing_completed_reviews(workflow, monkeypatch):
    from pathlib import Path

    directory, plan, browser, client, parse = workflow
    original = Path.write_text

    def write(path, *args, **kwargs):
        if path.name == 'review.md':
            raise PermissionError('Report is locked')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', write)
    with pytest.raises(PermissionError):
        asyncio.run(review.review_selected(directory, plan, client, planner._structured_call))
    report = json.loads((directory / 'review.json').read_text())
    assert report['status'] == 'failed'
    assert report['reviewed_count'] == 2
    assert report['archive']['document_count'] == 2
    assert 'could not be written' in report['error']['message']


def test_no_matches_skip_browser_review_and_archive(workflow):
    directory, plan, browser, client, parse = workflow
    plan['ranked_selections'] = []
    report = asyncio.run(review.review_selected(directory, plan, client, planner._structured_call))
    assert report['status'] == 'completed'
    assert report['documents'] == []
    assert report['archive'] is None
    review.inspect_site.assert_not_called()
    parse.assert_not_called()


def test_citation_normalizes_whitespace_but_rejects_empty_support():
    valid = result(findings=[review.Finding(statement='Approved.', citations=[review.Citation(page=1, quote='The Board\napproved')])])
    review.validate_review(valid, 'C0001', [{'page': 1, 'text': TEXT}])
    for bad in [result(findings=[]), result(findings=[review.Finding(statement='Unsupported.', citations=[])]),
                result(findings=[review.Finding(statement='False quotation.', citations=[review.Citation(page=1, quote='Not in source')])]),
                result(findings=[review.Finding(statement='Wrong page.', citations=[review.Citation(page=99, quote=TEXT)])]),
                result(findings=[review.Finding(statement='', citations=[review.Citation(page=1, quote=TEXT)])])]:
        with pytest.raises(ValueError):
            review.validate_review(bad, 'C0001', [{'page': 1, 'text': TEXT}])


def test_evidence_excerpts_cover_all_text_and_resolve_quotes_locally():
    text = ('A sentence about project approvals.\n' * 80) + 'LongToken' * 70
    request = review.ReviewRequest(candidate_id='C0001', document=review.DocumentIdentity(
        document_number='123', title='Board Order', date=None, categories=['Key Documents']),
        request=planner.EmailRequest(matter_numbers=['M12205'], categories=[], requested_count=None,
            topics=[], document_names=[], date_restrictions=[], ordering_preferences=[], clarification_question=None),
        pages=[review.TextPage(page=1, text=text), review.TextPage(page=2, text='A second page.')])
    payload, sources = review.prepare_evidence(request)
    for actual, original in zip(payload['pages'], request.pages):
        assert ''.join(span['text'] for span in actual['spans']) == ' '.join(original.text.split())
        assert all(0 < len(span['text']) <= 280 for span in actual['spans'])
    source_id = payload['pages'][1]['spans'][0]['source_id']
    answer = review.ReferencedReview(candidate_id='C0001', relevance='matches', limitations=[], findings=[
        review.ReferencedFinding(statement='The document has another page.',
            citations=[review.CitationReference(source_id=source_id)])])
    resolved = review.resolve_review(answer, sources)
    assert resolved.findings[0].citations[0].page == 2
    assert resolved.findings[0].citations[0].quote == 'A second page.'
    review.validate_review(resolved, 'C0001', [item.model_dump() for item in request.pages])
    for ids in [['made-up'], [source_id, source_id]]:
        bad = answer.model_copy(update={'findings': [review.ReferencedFinding(statement='Finding',
            citations=[review.CitationReference(source_id=value) for value in ids])]})
        with pytest.raises(ValueError, match='unknown or duplicate'):
            review.resolve_review(bad, sources)


def test_source_references_parse_through_actual_sdk_with_mock_google_http():
    from openai import AsyncOpenAI

    source_id = 'C0001-p1-s1'
    answer = review.ReferencedReview(candidate_id='C0001', relevance='matches', limitations=[], findings=[
        review.ReferencedFinding(statement='The project was approved subject to conditions.',
            citations=[review.CitationReference(source_id=source_id)])])

    def handle(request):
        assert request.url.host == 'generativelanguage.googleapis.com'
        payload = json.loads(request.content)
        schema = payload['response_format']['json_schema']['schema']
        assert set(schema['$defs']['CitationReference']['properties']) == {'source_id'}
        assert schema['properties']['findings']['maxItems'] == 5
        assert not payload.get('tools')
        return httpx.Response(200, json={'id': 'review_sdk', 'object': 'chat.completion', 'created': 1,
            'model': planner.DEFAULT_MODEL, 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': answer.model_dump_json()}}]})

    async def check():
        async with AsyncOpenAI(api_key='offline-test', base_url=planner.GEMINI_BASE_URL,
            max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))) as client:
            parsed = await planner._structured_call(client, planner.DEFAULT_MODEL, review.ReferencedReview,
                review.REVIEW_PROMPT, {'public_test_data': True}, [], 'review_sdk',
                prompt_version=review.REVIEW_PROMPT_VERSION)
        resolved = review.resolve_review(parsed, {source_id: review.Citation(page=1, quote=TEXT)})
        return review.validate_review(resolved, 'C0001', [{'page': 1, 'text': TEXT}])

    checked = asyncio.run(check())
    assert checked['findings'][0]['citations'] == [{'page': 1, 'quote': TEXT}]
