import asyncio
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from senpilot_agent import probe
from senpilot_agent.llm import LLMConfig
from senpilot_agent.selection import EmailRequest
from test_probe import offline_site


def configure_provider(monkeypatch, choose):
    openai=pytest.importorskip('openai')
    import httpx
    from test_llm import provider_response
    calls=[]
    real=openai.AsyncOpenAI
    def handler(request):
        assert request.url.path=='/v1/responses'
        payload=json.loads(request.content)
        data=json.loads(payload['input'][0]['content'])
        calls.append(data)
        return httpx.Response(200,json=provider_response(choose(data)))
    def constructor(**kwargs):
        assert kwargs['max_retries']==0
        kwargs['http_client']=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return real(**kwargs)
    monkeypatch.setattr(openai,'AsyncOpenAI',constructor)
    return calls


def choose_references(data, references=('100025','H-1')):
    candidates=data['candidates']
    selected=[next(c for c in candidates if (c.get('document_number') or c.get('hearing_date'))==ref) for ref in references]
    return {'status':'ready','matter_number':data['verified_matter_number'],
        'categories':list(dict.fromkeys(c['category'] for c in selected)),
        'category_basis':'explicit','assumptions':[],'limit':10,
        'ranked_documents':[{'candidate_id':c['candidate_id'],'reason':'Requested illustrative listing',
                             'matched_text':c.get('title') or c['witnesses'][0]} for c in selected], 'message':None}


@pytest.mark.parametrize('mode',['collection_success','collection_catalog_delayed','collection_catalog_buffer'])
def test_complete_offscreen_catalog_to_one_model_call_to_verified_zip(offline_site,tmp_path,monkeypatch,mode):
    offline_site(mode)
    calls=configure_provider(monkeypatch,choose_references)
    directory,report=asyncio.run(probe.inspect_site(tmp_path,
        email_request=EmailRequest('M12205','Prioritize document 100025, then Exhibit H-1.'),
        llm_config=LLMConfig('fixture-model','offline-key'),timeout_ms=10000))
    assert report['status']=='collection_complete',report.get('error')
    assert len(calls)==1 and len(calls[0]['candidates'])==30
    assert {c['document_number'] for c in calls[0]['candidates'] if c.get('document_number')} >= {'100001','100025','H-1','200003'}
    assert [d['document_number'] for d in report['downloaded_documents']]==['100025','H-1']
    catalog=json.loads((directory/'catalog.json').read_text())
    assert catalog['complete'] and catalog['eligible_count']==30
    assert all(c['extension']=='.pdf' for c in catalog['documents'])
    assert len({d['candidate_id'] for d in catalog['documents']})==30
    for category,count in report['category_counts'].items():
        assert [d['listing_position'] for d in catalog['documents'] if d['category']==category]==list(range(count))
    with zipfile.ZipFile(directory/'delivery.zip') as archive:
        assert archive.testzip() is None
        assert len(archive.namelist())==3
        assert all('ranking' not in name and 'request.json' not in name and 'catalog' not in name for name in archive.namelist())
        manifest=json.loads(archive.read('manifest.json'))
        assert manifest['category_counts']==report['category_counts']
        for file in manifest['files']:
            assert hashlib.sha256(archive.read(file['archive_member'])).hexdigest()==file['file_evidence']['sha256']
    for name in ('request.json','catalog.json','ranking-input.json','model-response.json','selection.json','collection-request.json','report.json'):
        assert (directory/name).is_file()


def test_dry_run_ranks_without_downloading(offline_site,tmp_path,monkeypatch):
    offline_site('collection_success')
    calls=configure_provider(monkeypatch,choose_references)
    directory,report=asyncio.run(probe.inspect_site(tmp_path,email_request=EmailRequest('M12205','Documents'),
        llm_config=LLMConfig('fixture-model','offline-key'),dry_run=True,timeout_ms=10000))
    assert report['status']=='selection_ready',report.get('error')
    assert len(calls)==1 and (directory/'collection-request.json').exists()
    assert not (directory/'delivery.zip').exists() and not (directory/'downloads').exists()


@pytest.mark.parametrize('status',['insufficient_detail','no_matches'])
def test_terminal_request_has_no_continuation_and_next_submission_is_independent(offline_site,tmp_path,monkeypatch,status):
    offline_site('collection_success')
    def choose(data):
        value=choose_references(data)
        value.update(status=status,ranked_documents=[],message='Which category would you like?' if status=='insufficient_detail' else 'Submit a new detailed request from scratch.')
        return value
    calls=configure_provider(monkeypatch,choose)
    directories=[]
    for _ in range(2):
        directory,report=asyncio.run(probe.inspect_site(tmp_path,email_request=EmailRequest('M12205','Documents'),
            llm_config=LLMConfig('fixture-model','offline-key'),timeout_ms=10000))
        directories.append(directory)
        assert report['status']==status,report.get('error')
        assert 'Submit a new detailed request from scratch.' in report['selection_message']
        assert '?' not in report['selection_message']
        assert not (directory/'downloads').exists()
        assert not (directory/'collection-request.json').exists()
        assert not any('thread' in k or 'continuation' in k or 'conversation' in k for k in report)
    assert directories[0]!=directories[1] and len(calls)==2


@pytest.mark.parametrize('body',['No matter','M122055','M12205 M12345'])
def test_invalid_matter_never_opens_browser_or_invokes_model(tmp_path,monkeypatch,body):
    def unexpected(*a,**k): pytest.fail('Invalid matter advanced past the regex gate')
    monkeypatch.setattr(probe,'rank_documents',unexpected)
    monkeypatch.setattr(LLMConfig,'from_env',unexpected)
    directory,report=asyncio.run(probe.inspect_site(tmp_path,email_request=EmailRequest('',body)))
    assert report['status']=='invalid_request'
    assert report['stage']=='validate_request'
    assert not (directory/'page.png').exists()


@pytest.mark.parametrize('mode',['collection_stalled','collection_catalog_gap','collection_catalog_changes','collection_catalog_count_changes'])
def test_incomplete_or_changing_catalog_invokes_no_model(offline_site,tmp_path,monkeypatch,mode):
    offline_site(mode)
    monkeypatch.setattr(probe,'rank_documents',lambda *a,**k:pytest.fail('Incomplete catalog reached model'))
    directory,report=asyncio.run(probe.inspect_site(tmp_path,email_request=EmailRequest('M12205','Documents'),
        llm_config=LLMConfig('fixture-model','offline-key'),timeout_ms=3000))
    assert report['status']=='catalog_incomplete',report.get('error')
    assert report['verified_matter_number']=='M12205'
    assert not (directory/'catalog.json').exists() and not (directory/'downloads').exists()


def test_empty_matter_finishes_without_provider_or_download(offline_site,tmp_path,monkeypatch):
    offline_site('collection_all_zero')
    calls=configure_provider(monkeypatch,lambda data:pytest.fail('Empty catalog reached provider'))
    directory,report=asyncio.run(probe.inspect_site(tmp_path,email_request=EmailRequest('M12205','Documents'),
        llm_config=LLMConfig('fixture-model','offline-key'),timeout_ms=10000))
    assert report['status']=='no_matches',report.get('error')
    assert report['catalog']['complete'] and report['catalog']['eligible_count']==0
    assert calls==[] and not (directory/'downloads').exists()


def test_missing_model_configuration_generates_diagnostic_before_browser(tmp_path,monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('SENPILOT_LLM_MODEL',raising=False)
    directory,report=asyncio.run(probe.inspect_site(tmp_path,email_request=EmailRequest('M12205','Documents')))
    assert report['status']=='configuration_error'
    assert report['stage']=='configure_model'
    assert not (directory/'page.png').exists()


def test_private_rows_count_toward_coverage_but_are_not_sent_to_model(offline_site,tmp_path,monkeypatch):
    offline_site('collection_private')
    calls=configure_provider(monkeypatch,choose_references)
    directory,report=asyncio.run(probe.inspect_site(tmp_path,email_request=EmailRequest('M12205','Documents'),
        llm_config=LLMConfig('fixture-model','offline-key'),dry_run=True,timeout_ms=10000))
    assert report['status']=='selection_ready',report.get('error')
    catalog=json.loads((directory/'catalog.json').read_text())
    assert len(catalog['documents'])==30 and catalog['eligible_count']==29
    assert len(calls[0]['candidates'])==29
    assert not any(c.get('document_number')=='100002' for c in calls[0]['candidates'])


def test_repeated_hearing_dates_are_distinct_addressable_catalog_candidates(offline_site,tmp_path,monkeypatch):
    offline_site('collection_dates_ambiguous')
    calls=configure_provider(monkeypatch,choose_references)
    directory,report=asyncio.run(probe.inspect_site(tmp_path,email_request=EmailRequest('M12205','Documents'),
        llm_config=LLMConfig('fixture-model','offline-key'),dry_run=True,timeout_ms=10000))
    assert report['status']=='selection_ready',report.get('error')
    catalog=json.loads((directory/'catalog.json').read_text())
    hearings=[c for c in catalog['documents'] if c['hearing_date']]
    assert len(hearings)==4 and all(c['eligible'] for c in hearings)
    assert len({(c['category'],c['hearing_date'],c['download_reference']['title']) for c in hearings})==4


def test_invalid_model_selection_does_not_create_executable_request(offline_site,tmp_path,monkeypatch):
    offline_site('collection_success')
    def choose(data):
        value=choose_references(data)
        value['ranked_documents'][0]['candidate_id']='invented'
        return value
    calls=configure_provider(monkeypatch,choose)
    directory,report=asyncio.run(probe.inspect_site(tmp_path,email_request=EmailRequest('M12205','Documents'),
        llm_config=LLMConfig('fixture-model','offline-key'),timeout_ms=10000))
    assert report['status']=='invalid_selection'
    assert len(calls)==1 and not (directory/'downloads').exists()
    assert not (directory/'collection-request.json').exists()
