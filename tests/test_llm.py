import asyncio
import json

import pytest

openai = pytest.importorskip('openai')
import httpx

from senpilot_agent import llm
from senpilot_agent.llm import LLMConfig, build_payload, rank_documents
from senpilot_agent.selection import EmailRequest, RequestFailure
from test_selection import catalog, catalog_for, selection_for


def provider_response(value, status='completed', refusal=False):
    content = [{'type':'refusal','refusal':'Declined'}] if refusal else [
        {'type':'output_text','text':json.dumps(value),'annotations':[]}]
    return {'id':'resp_offline', 'object':'response', 'created_at':1, 'status':status,
            'model':'fixture-model', 'output':[{'type':'message','id':'msg_offline',
            'status':'completed','role':'assistant','content':content}],
            'usage':{'input_tokens':123,'output_tokens':45,'total_tokens':168}}


def run_with_transport(tmp_path, catalog, handler, config=None):
    config=config or LLMConfig('fixture-model','offline-key')
    report={}
    async def run():
        async with openai.AsyncOpenAI(api_key='offline-key',max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as client:
            return await rank_documents(EmailRequest('M12205','Only Board Orders'),catalog,config,tmp_path,report,client=client)
    return run,report


def test_one_generation_payload_contains_every_eligible_candidate_and_no_key(tmp_path,catalog):
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        assert request.url.path == '/v1/responses'
        return httpx.Response(200,json=provider_response(selection_for(catalog)))
    run,report=run_with_transport(tmp_path,catalog,handler)
    result=asyncio.run(run())
    assert result==selection_for(catalog)
    assert len(calls)==1
    payload=calls[0]
    data=json.loads(payload['input'][0]['content'])
    assert [d['candidate_id'] for d in data['candidates']] == [d['candidate_id'] for d in catalog['documents']]
    assert payload['text']['format']['strict'] is True
    assert payload['truncation']=='disabled' and payload['store'] is False
    assert 'tools' not in payload and 'previous_response_id' not in payload
    assert report['ranking']['usage']['input_tokens']==123
    assert report['ranking']['configuration']=={'timeout_seconds':60,'max_attempts':2,'retry_delay_seconds':1,'sdk_retries':0}
    assert report['ranking']['sizing']['method']=='utf8_bytes_plus_4096'
    for file in tmp_path.iterdir():
        assert 'offline-key' not in file.read_text(encoding='utf-8')


@pytest.mark.parametrize('status,code,attempts',[(503,'server_error',2),(500,'server_error',2),
    (429,'rate_limit_exceeded',2),(429,'insufficient_quota',1),(401,'invalid_api_key',1),
    (400,'invalid_request_error',1)])
def test_retryable_errors_get_one_identical_retry_only(tmp_path,catalog,status,code,attempts):
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(status,json={'error':{'message':'synthetic provider error','type':code,'code':code}})
    run,report=run_with_transport(tmp_path,catalog,handler)
    with pytest.raises(RequestFailure) as exc:
        asyncio.run(run())
    assert exc.value.status=='ranking_failed'
    assert len(calls)==len(report['ranking']['attempts'])==attempts
    assert all(c==calls[0] for c in calls)
    assert not (tmp_path/'collection-request.json').exists()


def test_transient_error_then_success_has_two_attempts_not_two_stages(tmp_path,catalog):
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return (httpx.Response(503,json={'error':{'message':'temporary','code':'server_error'}}) if len(calls)==1
                else httpx.Response(200,json=provider_response(selection_for(catalog))))
    run,report=run_with_transport(tmp_path,catalog,handler)
    assert asyncio.run(run())['status']=='ready'
    assert len(calls)==2 and calls[0]==calls[1]
    assert [a['outcome'] for a in report['ranking']['attempts']]==['provider_error','received']


def test_network_timeout_is_bounded_at_two_attempts(tmp_path,catalog):
    calls=[]
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('synthetic timeout',request=request)
    run,report=run_with_transport(tmp_path,catalog,handler)
    with pytest.raises(RequestFailure,match='provider'):
        asyncio.run(run())
    assert len(calls)==2


def test_whole_attempt_deadline_also_retries_once(tmp_path,catalog):
    calls=[]
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(5)
        pytest.fail('Request did not respect the whole-attempt deadline')
    run,report=run_with_transport(tmp_path,catalog,handler,LLMConfig('fixture-model','offline-key',timeout_seconds=1))
    with pytest.raises(RequestFailure): asyncio.run(run())
    assert len(calls)==2
    assert all(a['error_type']=='TimeoutError' for a in report['ranking']['attempts'])


@pytest.mark.parametrize('mode,expected',[('refusal','model_refused'),('incomplete','invalid_selection'),
    ('bad_json','invalid_selection'),('duplicate_json','invalid_selection'),('tool_call','invalid_selection')])
def test_unusable_answer_never_gets_repair_retry(tmp_path,catalog,mode,expected):
    calls=[]
    def handler(request):
        calls.append(request)
        response=provider_response(selection_for(catalog),status='incomplete' if mode=='incomplete' else 'completed',refusal=mode=='refusal')
        if mode=='bad_json': response['output'][0]['content'][0]['text']='{'
        if mode=='duplicate_json': response['output'][0]['content'][0]['text']='{"status":"ready","status":"ready"}'
        if mode=='tool_call': response['output']=[{'type':'function_call','id':'fc_1','call_id':'call_1','name':'unsafe','arguments':'{}'}]
        return httpx.Response(200,json=response)
    run,report=run_with_transport(tmp_path,catalog,handler)
    with pytest.raises(RequestFailure) as exc:
        asyncio.run(run())
    assert exc.value.status==expected and len(calls)==1


@pytest.mark.parametrize('mode,status',[('oversized','ranking_capacity_exceeded'),
    ('incomplete','catalog_incomplete'),('no_public','no_matches')])
def test_preflight_failures_send_zero_requests(tmp_path,catalog,mode,status):
    def unexpected(request): pytest.fail('Preflight failure sent a model request')
    if mode=='incomplete': catalog['complete']=False
    if mode=='no_public':
        for d in catalog['documents']: d['eligible']=False
    cfg=LLMConfig('fixture-model','offline-key',input_budget=1) if mode=='oversized' else None
    run,report=run_with_transport(tmp_path,catalog,unexpected,cfg)
    with pytest.raises(RequestFailure) as exc:
        asyncio.run(run())
    assert exc.value.status==status
    assert not report.get('ranking',{}).get('attempts')


def test_config_requires_credentials_and_bounded_budget(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('SENPILOT_LLM_MODEL',raising=False)
    with pytest.raises(RequestFailure,match='Configure'):
        LLMConfig.from_env()
    for values in ({'input_budget':True},{'input_budget':128000},{'timeout_seconds':float('nan')}):
        with pytest.raises(RequestFailure): LLMConfig('fixture-model','offline',**values)
    assert 'offline' not in repr(LLMConfig('fixture-model','offline'))


def test_real_constructor_disables_sdk_retries(tmp_path,catalog,monkeypatch):
    calls=[]
    real=openai.AsyncOpenAI
    def constructor(**kwargs):
        assert kwargs['max_retries']==0 and kwargs['timeout']==60
        kwargs['http_client']=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r:(calls.append(r) or httpx.Response(200,json=provider_response(selection_for(catalog))))))
        return real(**kwargs)
    monkeypatch.setattr(openai,'AsyncOpenAI',constructor)
    report={}
    asyncio.run(rank_documents(EmailRequest('M12205','Orders'),catalog,LLMConfig('fixture-model','offline-key'),tmp_path,report))
    assert len(calls)==1


def test_injected_client_cannot_add_hidden_sdk_retries(tmp_path,catalog):
    calls=[]
    async def run():
        async with openai.AsyncOpenAI(api_key='offline-key',max_retries=2,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:calls.append(r)))) as client:
            await rank_documents(EmailRequest('M12205','Orders'),catalog,LLMConfig('fixture-model','offline-key'),tmp_path,{},client=client)
    with pytest.raises(RequestFailure,match='disable automatic retries'):
        asyncio.run(run())
    assert calls==[]
