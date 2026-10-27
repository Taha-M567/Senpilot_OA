import asyncio
from copy import deepcopy
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from openai import APITimeoutError, InternalServerError, RateLimitError
import pytest

from senpilot_agent import planner
from senpilot_agent.downloads import CATEGORIES


def interpretation(**changes):
    data = dict(matter_numbers=['M12205'], categories=[], requested_count=None,
                topics=[], document_names=[], date_restrictions=[], ordering_preferences=[],
                clarification_question=None)
    data.update(changes)
    return planner.EmailRequest(**data)


def response(parsed, *, finish_reason='stop', refusal=None, **changes):
    data = dict(choices=[SimpleNamespace(finish_reason=finish_reason,
                    message=SimpleNamespace(parsed=parsed, refusal=refusal))], model='gemini-3.1-flash-lite',
                _request_id='req_test', id='resp_test',
                usage=SimpleNamespace(model_dump=lambda **kwargs: {'prompt_tokens': 42, 'completion_tokens': 12}))
    data.update(changes)
    return SimpleNamespace(**data)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_MODEL', raising=False)


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key-must-not-be-logged')
    monkeypatch.delenv('GEMINI_MODEL', raising=False)
    parse = AsyncMock()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {'api_key': 'test-key-must-not-be-logged', 'base_url': planner.GEMINI_BASE_URL,
                              'timeout': 60.0, 'max_retries': 0}

        async def __aenter__(self):
            return client

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(planner, 'AsyncOpenAI', Client)
    return parse


@pytest.fixture
def browser(monkeypatch):
    inventory = [dict(candidate_id=f'C{i:04d}', document_number=str(1000 + i), title=f'Board Order {i}',
                      date=f'01/0{i}/2026', categories=['Key Documents'],
                      list_order={'Key Documents': i - 1}, original_order=i - 1) for i in range(1, 4)]
    report = dict(status='inventory_ready', stage='inventory_complete', inventory_complete=True,
                  matter_metadata={'matter_number': 'M12205', 'title': 'Verified matter'},
                  category_counts=dict.fromkeys(CATEGORIES, 0), candidate_inventory=inventory,
                  inventory_category_counts=dict.fromkeys(CATEGORIES, 0))

    async def inspect(output, **kwargs):
        return output / 'evidence', deepcopy(report)

    mock = AsyncMock(side_effect=inspect)
    monkeypatch.setattr(planner, 'inspect_site', mock)
    return mock, report


@pytest.mark.parametrize(('requested', 'limit', 'expected'), [(None, 10, 10), (2, 10, 2), (20, 3, 3), (5, 2, 2)])
def test_two_calls_limits_diagnostics_and_inventory_display_fields(llm, browser, tmp_path, requested, limit, expected):
    mock, source = browser
    llm.side_effect = [response(interpretation(requested_count=requested)), response(planner.Ranking(
        selections=[planner.Selection(candidate_id='C0002', reason='The title identifies a Board Order.')]))]
    directory, report = asyncio.run(planner.plan_email('Prioritize documents for M12205.', tmp_path, limit=limit))
    assert report['status'] == 'completed', report.get('error')
    assert report['result_limit'] == expected
    assert mock.call_args.kwargs['inventory_categories'] == list(CATEGORIES)
    assert report['ranked_selections'] == [{**source['candidate_inventory'][1], 'reason': 'The title identifies a Board Order.'}]
    assert llm.await_count == 2
    for call in llm.call_args_list:
        assert call.kwargs['model'] == 'gemini-3.1-flash-lite'
        assert call.kwargs['reasoning_effort'] == 'low'
        assert 'tools' not in call.kwargs
    payload = json.loads(llm.call_args_list[1].kwargs['messages'][1]['content'])
    assert payload['candidate_inventory'] == source['candidate_inventory']
    assert payload['result_limit'] == expected
    assert report['calls'][0]['request_id'] == 'req_test'
    assert report['calls'][0]['token_usage']['prompt_tokens'] == 42
    assert json.loads((directory / 'plan.json').read_text()) == report
    assert 'test-key-must-not-be-logged' not in (directory / 'plan.json').read_text()


def test_explicit_categories_and_criteria_are_preserved(llm, browser, tmp_path):
    llm.side_effect = [response(interpretation(categories=[' key   documents ', 'KEY DOCUMENTS'],
        topics=['water'], document_names=['Board Order'], date_restrictions=['after January 2026'],
        ordering_preferences=['oldest first'])), response(planner.Ranking(selections=[]))]
    _, report = asyncio.run(planner.plan_email('M12205 Key Documents about water', tmp_path))
    assert report['status'] == 'completed'
    assert browser[0].call_args.kwargs['inventory_categories'] == ['Key Documents']
    payload = json.loads(llm.call_args.kwargs['messages'][1]['content'])
    assert payload['request']['date_restrictions'] == ['after January 2026']
    assert payload['request']['ordering_preferences'] == ['oldest first']


@pytest.mark.parametrize(('interpreted', 'email'), [
    (interpretation(matter_numbers=[]), 'Please find orders.'),
    (interpretation(matter_numbers=['M12205', 'M54321']), 'M12205 and M54321'),
    (interpretation(), 'M12205 and M54321'),
    (interpretation(matter_numbers=['M54321']), 'M12205'),
    (interpretation(categories=['Unsupported']), 'M12205'),
    (interpretation(requested_count=0), 'M12205'),
    (interpretation(clarification_question='Which ordering should take precedence?'), 'M12205 newest and oldest first'),
    (interpretation(clarification_question='Do you want metadata recommendations instead?'), 'Download and email M12205 files'),
])
def test_clarification_prevents_browser_launch(llm, browser, tmp_path, interpreted, email):
    llm.return_value = response(interpreted)
    directory, report = asyncio.run(planner.plan_email(email, tmp_path))
    assert report['status'] == 'clarification'
    assert report['clarification_question']
    browser[0].assert_not_called()
    assert llm.await_count == 1
    assert (directory / 'plan.json').exists()


@pytest.mark.parametrize('ids', [['invented'], ['C0001', 'C0001'], ['C0001', 'C0002']])
def test_invalid_selection_ids_duplicates_and_excess_results_fail(llm, browser, tmp_path, ids):
    llm.side_effect = [response(interpretation()), response(planner.Ranking(selections=[
        planner.Selection(candidate_id=value, reason='Title indicates an order.') for value in ids]))]
    _, report = asyncio.run(planner.plan_email('M12205', tmp_path, limit=1 if len(set(ids)) > 1 else 10))
    assert report['status'] == 'failed'
    assert report['ranked_selections'] == []


@pytest.mark.parametrize('bad', [
    response(None), response(interpretation(), finish_reason='length'), response({'wrong': 'schema'}),
    response(None, refusal='Refused'), response(None, choices=[]),
])
def test_refusal_incomplete_or_malformed_response_is_visible_failure(llm, browser, tmp_path, bad):
    llm.return_value = bad
    _, report = asyncio.run(planner.plan_email('M12205', tmp_path))
    assert report['status'] == 'failed'
    assert report['error']['message']
    browser[0].assert_not_called()


@pytest.mark.parametrize('at_ranking', [False, True])
@pytest.mark.parametrize('error', [
    APITimeoutError(request=httpx.Request('POST', planner.GEMINI_BASE_URL + 'chat/completions')),
    RateLimitError('test-key-must-not-be-logged', response=httpx.Response(429, headers={'x-request-id': 'req_failed'},
                  request=httpx.Request('POST', planner.GEMINI_BASE_URL + 'chat/completions')), body=None),
    InternalServerError('test-key-must-not-be-logged', response=httpx.Response(503,
                  request=httpx.Request('POST', planner.GEMINI_BASE_URL + 'chat/completions')), body=None),
    ValueError('test-key-must-not-be-logged'),
])
def test_api_errors_and_timeouts_never_fallback_or_leak_credentials(llm, browser, tmp_path, error, at_ranking):
    llm.side_effect = [response(interpretation()), error] if at_ranking else error
    directory, report = asyncio.run(planner.plan_email('M12205', tmp_path))
    assert report['status'] == 'failed'
    assert report['ranked_selections'] == []
    assert llm.await_count == (2 if at_ranking else 1)
    assert report['calls'][-1]['error_type'] == type(error).__name__
    if getattr(error, 'status_code', None):
        assert f'HTTP {error.status_code}' in report['error']['message']
    assert 'test-key-must-not-be-logged' not in (directory / 'plan.json').read_text()


@pytest.mark.parametrize('status', ['empty', 'failed', 'clarification', 'incomplete'])
def test_no_ranking_for_empty_or_incomplete_inventory(llm, browser, tmp_path, status):
    source = browser[1]
    if status == 'empty':
        source['candidate_inventory'] = []
    elif status == 'incomplete':
        source['inventory_complete'] = False
    else:
        source.update(status=status, stage='read_inventory', clarification_question='Narrow categories.')
    llm.return_value = response(interpretation())
    _, report = asyncio.run(planner.plan_email('M12205', tmp_path))
    assert report['status'] == {'empty': 'completed', 'clarification': 'clarification'}.get(status, 'failed')
    assert llm.await_count == 1


def test_missing_key_fails_without_browser_or_gemini(llm, browser, tmp_path, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY')
    _, report = asyncio.run(planner.plan_email('M12205', tmp_path))
    assert report['status'] == 'failed'
    assert 'GEMINI_API_KEY' in report['error']['message']
    llm.assert_not_called()
    browser[0].assert_not_called()


def test_settings_load_from_dotenv(llm, browser, tmp_path, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY')
    (tmp_path / '.env').write_text(
        '\ufeffGEMINI_API_KEY="test-key-must-not-be-logged"\nGEMINI_MODEL="file-model"\n', encoding='utf-8')
    llm.side_effect = [response(interpretation()), response(planner.Ranking(selections=[]))]
    directory, report = asyncio.run(planner.plan_email('M12205', tmp_path))
    assert report['status'] == 'completed', report.get('error')
    assert report['provider'] == 'gemini'
    assert all(call.kwargs['model'] == 'file-model' for call in llm.call_args_list)
    assert 'GEMINI_API_KEY' not in os.environ
    assert 'test-key-must-not-be-logged' not in (directory / 'plan.json').read_text()


def test_process_environment_overrides_dotenv(llm, browser, tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('GEMINI_API_KEY=file-key\nGEMINI_MODEL=file-model\n', encoding='utf-8')
    monkeypatch.setenv('GEMINI_MODEL', 'environment-model')
    llm.side_effect = [response(interpretation()), response(planner.Ranking(selections=[]))]
    _, report = asyncio.run(planner.plan_email('M12205', tmp_path))
    assert report['status'] == 'completed'
    assert all(call.kwargs['model'] == 'environment-model' for call in llm.call_args_list)


def test_openai_key_is_not_used_for_gemini(llm, browser, tmp_path, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY')
    monkeypatch.setenv('OPENAI_API_KEY', 'different-provider-key')
    _, report = asyncio.run(planner.plan_email('M12205', tmp_path))
    assert report['status'] == 'failed'
    assert 'GEMINI_API_KEY' in report['error']['message']
    llm.assert_not_called()


def test_model_override_is_used_for_both_calls(llm, browser, tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_MODEL', 'configured-model')
    llm.side_effect = [response(interpretation()), response(planner.Ranking(selections=[]))]
    _, report = asyncio.run(planner.plan_email('M12205', tmp_path))
    assert report['status'] == 'completed'
    assert report['model'] == 'configured-model'
    assert all(call.kwargs['model'] == 'configured-model' for call in llm.call_args_list)


def test_blank_reason_is_rejected():
    with pytest.raises(planner.PlanningError, match='empty selection reason'):
        planner.validate_selections(planner.Ranking(selections=[
            planner.Selection(candidate_id='C1', reason='   ')]), [{'candidate_id': 'C1'}], 10)


def test_real_sdk_structured_output_parsing_with_mock_http(browser, tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'offline-sdk-test')
    monkeypatch.delenv('GEMINI_MODEL', raising=False)
    sdk = planner.AsyncOpenAI
    results = [interpretation(), planner.Ranking(selections=[
        planner.Selection(candidate_id='C0001', reason='Title identifies a Board Order.')])]
    requests = []

    def handle(request):
        assert request.url.host == 'generativelanguage.googleapis.com'
        assert request.url.path == '/v1beta/openai/chat/completions'
        payload = json.loads(request.content)
        requests.append(payload)
        result = results[len(requests) - 1]
        assert payload['response_format']['type'] == 'json_schema'
        assert payload['response_format']['json_schema']['strict'] is True
        assert payload['response_format']['json_schema']['schema']['additionalProperties'] is False
        assert not payload.get('tools')
        return httpx.Response(200, headers={'x-request-id': f'req_sdk_{len(requests)}'}, json={
            'id': f'resp_sdk_{len(requests)}', 'object': 'chat.completion', 'created': 1,
            'model': 'gemini-3.1-flash-lite',
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': result.model_dump_json()}}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30},
        })

    monkeypatch.setattr(planner, 'AsyncOpenAI', lambda **kwargs: sdk(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))))
    _, report = asyncio.run(planner.plan_email('M12205', tmp_path))
    assert report['status'] == 'completed', report.get('error')
    assert len(requests) == 2
    assert [call['request_id'] for call in report['calls']] == ['req_sdk_1', 'req_sdk_2']
    assert report['ranked_selections'][0]['title'] == 'Board Order 1'


@pytest.mark.parametrize(('email', 'limit'), [('', 10), ('M12205', 0), ('M12205', 11)])
def test_invalid_input_returns_clarification_without_calls(llm, browser, tmp_path, email, limit):
    _, report = asyncio.run(planner.plan_email(email, tmp_path, limit=limit))
    assert report['status'] == 'clarification'
    llm.assert_not_called()
    browser[0].assert_not_called()


def test_end_to_end_plan_uses_real_fixture_inventory(llm, document_site, tmp_path):
    document_site(many=True)

    async def parse(**kwargs):
        if kwargs['response_format'] is planner.EmailRequest:
            return response(interpretation(topics=['decisions', 'orders']))
        inventory = json.loads(kwargs['messages'][1]['content'])['candidate_inventory']
        assert len(inventory) == 15
        return response(planner.Ranking(selections=[
            planner.Selection(candidate_id=inventory[-1]['candidate_id'], reason='Selected by listed metadata.')]))

    llm.side_effect = parse
    directory, report = asyncio.run(planner.plan_email('Priority decisions/orders for M12205', tmp_path, timeout_ms=2000))
    assert report['status'] == 'completed', report.get('error')
    assert report['ranked_selections'][0]['document_number'] == '1012'
    assert report['inventory_complete']
    assert report['ranked_selections'][0]['candidate_id'] in {item['candidate_id'] for item in report['candidate_inventory']}
    assert not list(directory.rglob('downloads'))
    assert all(path.name == 'trace.zip' for path in directory.rglob('*.zip'))


def test_review_end_to_end_exact_downloads_real_extraction_and_mocked_reviews(llm, document_site, pdf_bytes, tmp_path):
    import zipfile
    from senpilot_agent.review import ReferencedReview

    text = 'The Board approved the project subject to conditions.'
    document_site(many=True, pdf_bytes=pdf_bytes([text]))

    async def parse(**kwargs):
        payload = json.loads(kwargs['messages'][1]['content'])
        if kwargs['response_format'] is planner.EmailRequest:
            assert 'PDF text review are supported' in kwargs['messages'][0]['content']
            return response(interpretation(review_questions=['What was approved?']))
        if kwargs['response_format'] is planner.Ranking:
            inventory = payload['candidate_inventory']
            return response(planner.Ranking(selections=[
                planner.Selection(candidate_id=inventory[-1]['candidate_id'], reason='Last selected document.'),
                planner.Selection(candidate_id=inventory[0]['candidate_id'], reason='First selected document.')]))
        assert kwargs['response_format'] is ReferencedReview
        assert ''.join(span['text'] for span in payload['pages'][0]['spans']) == text
        assert payload['request']['review_questions'] == ['What was approved?']
        return response(ReferencedReview.model_validate({
            'candidate_id': payload['candidate_id'], 'relevance': 'matches', 'limitations': [],
            'findings': [{'statement': 'The project was approved subject to conditions.',
                          'citations': [{'source_id': payload['pages'][0]['spans'][0]['source_id']}]}],
        }))

    llm.side_effect = parse
    directory, report = asyncio.run(planner.plan_email('Download and review priority documents for M12205.',
        tmp_path, review=True, timeout_ms=2000))
    assert report['status'] == 'completed', report
    assert report['downloaded_count'] == report['reviewed_count'] == 2
    assert [item['document_number'] for item in report['documents']] == ['1012', 'H-1']
    assert llm.await_count == 4
    metadata_plan = json.loads((directory / 'plan.json').read_text())
    assert metadata_plan['status'] == 'completed'
    assert len(metadata_plan['calls']) == 2
    assert len(report['calls']) == 2
    assert len(list((directory / 'downloads').iterdir())) == 2
    assert len(list((directory / 'extracted').iterdir())) == 2
    with zipfile.ZipFile(directory / report['archive']['relative_path']) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == [item['download']['filename'] for item in report['documents']]
        for item in report['documents']:
            assert archive.read(item['download']['filename']) == pdf_bytes([text])


@pytest.mark.parametrize('status', ['clarification', 'failed'])
def test_review_mode_preserves_early_failures_and_writes_review_reports(llm, browser, tmp_path, status):
    if status == 'clarification':
        llm.return_value = response(interpretation(clarification_question='Which question should I answer?'))
    else:
        llm.side_effect = ValueError('API failure')
    directory, report = asyncio.run(planner.plan_email('M12205', tmp_path, review=True))
    assert report['status'] == status
    assert (directory / 'plan.json').exists()
    assert (directory / 'review.json').exists()
    assert (directory / 'review.md').exists()
    browser[0].assert_not_called()


def test_review_setup_never_falls_back_to_an_openai_key_and_preserves_selection(tmp_path, monkeypatch):
    plan = dict(status='completed', stage='plan_complete', calls=[], provider='gemini',
        model=planner.DEFAULT_MODEL, prompt_version=planner.PROMPT_VERSION,
        interpreted_request={}, matter_metadata={'matter_number': 'M12205'}, category_counts={},
        ranked_selections=[dict(candidate_id='C0001', document_number='123', title='Board Order',
                                date=None, categories=['Key Documents'])])

    async def planned(*args, **kwargs):
        return tmp_path, plan

    def no_client(**kwargs):
        pytest.fail('A missing Gemini key must not fall back to OPENAI_API_KEY')

    monkeypatch.setattr(planner, '_plan_email', planned)
    monkeypatch.setattr(planner, 'AsyncOpenAI', no_client)
    monkeypatch.setenv('OPENAI_API_KEY', 'other-provider-secret')
    _, report = asyncio.run(planner.plan_email('M12205', tmp_path, review=True))
    assert report['status'] == 'failed'
    assert report['stage'] == 'configure_review'
    assert report['failed_count'] == 1
    assert report['documents'][0]['status'] == 'not_started'
    assert report['documents'][0]['candidate_id'] == 'C0001'
    assert 'other-provider-secret' not in (tmp_path / 'review.json').read_text()


def test_metadata_only_mode_clarifies_content_questions_before_browser(llm, browser, tmp_path):
    llm.return_value = response(interpretation(review_questions=['What did the Board decide?']))
    _, report = asyncio.run(planner.plan_email('What did the Board decide in M12205?', tmp_path))
    assert report['status'] == 'clarification'
    assert '--review' in report['clarification_question']
    browser[0].assert_not_called()
    assert llm.await_count == 1
