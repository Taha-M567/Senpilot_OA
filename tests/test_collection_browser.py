import asyncio
import base64
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from senpilot_agent import probe
from senpilot_agent.collection import CollectionRequest


@pytest.fixture
def collection_site(monkeypatch):
    from playwright.async_api import BrowserContext

    cache = Path(__file__).resolve().parents[1] / '.cache/ms-playwright'
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(cache))
    original = BrowserContext.new_page
    active_mode = None
    requests = []

    async def new_page(context):
        page = await original(context)

        async def attachment(route):
            name = route.request.url.rsplit('/', 1)[-1]
            requests.append(name)
            content = b'<html>error</html>' if name == 'html.pdf' else (
                f'%PDF-1.7\nfixture {"identical" if active_mode == "collection_duplicate_bytes" else name}\n%%EOF\n'.encode()
            )
            if name.endswith('.MP3'):
                content = b'\xff\xfb\x90\x64' + b'\0' * 413
            await route.fulfill(status=200, body=content, headers={
                'Content-Type': 'application/octet-stream',
                # Match FileMaker's observed unquoted export header in this case.
                'Content-Disposition': f'attachment; filename={name}' if active_mode == 'collection_dates_comma'
                else f'attachment; filename="{name}"',
            })

        await page.route('https://uarb.novascotia.ca/fixture/*', attachment)
        # Any unexpected domain remains subject to the production context guard.
        return page

    monkeypatch.setattr(BrowserContext, 'new_page', new_page)

    def load(mode='collection_success'):
        nonlocal active_mode
        active_mode = mode
        template = (Path(__file__).parent / 'fixtures/matter-search.html').read_text(encoding='utf-8')
        html = template.replace('__MODE__', mode)
        monkeypatch.setattr(probe, 'UARB_URL', 'data:text/html;base64,' + base64.b64encode(html.encode()).decode())
        return requests

    return load


def run(tmp_path, documents, limit=10):
    request = CollectionRequest.from_dict({'matter_number':'M12205', 'limit':limit, 'documents':[
        {'category':category, 'document_number':number, 'priority':priority}
        for category,number,priority in documents
    ]})
    return asyncio.run(probe.inspect_site(tmp_path, collection_request=request, timeout_ms=10000))


def check_zip(directory, report, expected):
    assert report['downloaded_document_count'] == len(expected)
    assert [d['document_number'] for d in report['downloaded_documents']] == expected
    path = directory / report['archive']['path']
    assert report['archive']['document_count'] == len(expected)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == report['archive']['file_evidence']['sha256']
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['matter_metadata']['matter_number'] == 'M12205'
        assert manifest['category_counts'] == report['category_counts']
        assert manifest['downloaded_document_count'] == len(expected)
        assert [d['document_number'] for d in manifest['files']] == expected
        assert archive.namelist() == [d['archive_member'] for d in manifest['files']] + ['manifest.json']
        for entry in manifest['files']:
            data = archive.read(entry['archive_member'])
            assert data == (directory / entry['path']).read_bytes()
            assert len(data) == entry['file_evidence']['size_bytes']
            assert hashlib.sha256(data).hexdigest() == entry['file_evidence']['sha256']
    assert json.loads((directory/'report.json').read_text()) == report
    assert report['blocked_origins'] == report['capture_errors'] == []


def test_priority_cap_offscreen_and_return_to_earlier_rows(collection_site, tmp_path):
    requests = collection_site()
    # Last row outranks the initial viewport. Eleven candidates, only ten fetched.
    documents = [('Other Documents', '100025', 1)] + [
        ('Other Documents', str(100001+i), 2) for i in range(10)
    ]
    documents = documents[1:] + documents[:1]  # Input order must not override priority.
    directory, report = run(tmp_path, documents)
    expected = ['100025'] + [str(100001+i) for i in range(9)]
    assert requests == [number+'.pdf' for number in expected]
    assert report['status'] == 'collection_complete'
    assert report['collection_complete'] is True
    check_zip(directory, report, expected)


def test_category_switching_uses_exact_category_identifier(collection_site, tmp_path):
    requests = collection_site()
    directory, report = run(tmp_path, [
        ('Key Documents','200003',1), ('Exhibits','H-2',2), ('Other Documents','100020',3),
    ])
    assert report['status'] == 'collection_complete'
    assert requests == ['200003.pdf', 'H-2.pdf', '100020.pdf']
    check_zip(directory, report, ['200003','H-2','100020'])


@pytest.mark.parametrize(('mode','middle','stage'), [
    ('collection_success','999999','locate_document'),
    ('collection_html','100002','validate_download'),
    ('collection_private','100002','locate_document'),
    ('collection_missing_dialog','100002','prepare_download'),
])
def test_isolated_failure_produces_explicit_partial_zip_and_continues(mode, middle, stage, collection_site, tmp_path):
    collection_site(mode)
    directory, report = run(tmp_path, [
        ('Other Documents','100001',1), ('Other Documents',middle,2), ('Other Documents','100003',3),
    ])
    assert report['status'] == 'collection_partial'
    assert report['collection_complete'] is False
    assert [d['status'] for d in report['document_outcomes']] == ['downloaded','failed','downloaded']
    assert report['document_outcomes'][1]['stage'] == stage
    check_zip(directory, report, ['100001','100003'])


def test_unrecoverable_dialog_stops_further_attempts_but_packages_saved_file(collection_site, tmp_path):
    requests = collection_site('collection_close_failure')
    directory, report = run(tmp_path, [('Other Documents','100001',1),('Other Documents','100002',2)])
    assert report['status'] == 'collection_partial'
    assert requests == ['100001.pdf']
    assert report['document_outcomes'][1]['status'] == 'not_attempted'
    assert report['error']['type'] == 'TimeoutError'
    check_zip(directory, report, ['100001'])


def test_download_event_timeout_stops_to_prevent_late_event_misattribution(collection_site, tmp_path):
    requests = collection_site('collection_download_timeout')
    directory, report = run(tmp_path, [
        ('Other Documents','100001',1), ('Other Documents','100002',2), ('Other Documents','100003',3),
    ])
    assert requests == ['100001.pdf']
    assert report['status'] == 'collection_partial'
    assert report['document_outcomes'][1]['stage'] == 'download_document'
    assert report['document_outcomes'][1]['error']['type'] == 'DownloadStateUncertain'
    assert report['document_outcomes'][2]['status'] == 'not_attempted'
    check_zip(directory, report, ['100001'])


@pytest.mark.parametrize('mode', ['collection_ambiguous','collection_stalled'])
def test_bad_or_stalled_virtual_list_fails_without_downloads(mode, collection_site, tmp_path):
    requests = collection_site(mode)
    directory, report = run(tmp_path, [('Other Documents','100001' if mode=='collection_ambiguous' else '100025',1)])
    assert requests == []
    assert report['status'] == 'failed'
    assert report['archive'] is None
    assert not (directory/'delivery.zip').exists()
    assert report['document_outcomes'][0]['stage'] == 'locate_document'


def test_empty_category_does_not_download_arbitrary_files(collection_site, tmp_path):
    requests = collection_site()
    request = CollectionRequest.from_dict({'matter_number':'M12205','documents':[
        {'category':'Recordings','hearing_date':'01/07/2026','priority':1},
    ]})
    directory, report = asyncio.run(probe.inspect_site(tmp_path,collection_request=request,timeout_ms=10000))
    assert report['status'] == 'failed'
    assert requests == []
    assert report['archive'] is None
    assert report['document_outcomes'][0]['stage'] == 'open_category'


def test_empty_input_has_metadata_and_no_zip(collection_site, tmp_path):
    requests = collection_site()
    directory, report = run(tmp_path, [])
    assert report['status'] == 'collection_empty'
    assert report['metadata_counts_complete'] is True
    assert report['archive'] is None
    assert requests == []


def test_distinct_references_with_identical_bytes_keep_both_associations(collection_site, tmp_path):
    collection_site('collection_duplicate_bytes')
    directory, report = run(tmp_path, [('Other Documents','100001',1),('Other Documents','100002',2)])
    assert report['status'] == 'collection_complete'
    assert report['document_outcomes'][1]['status'] == 'downloaded'
    assert report['downloaded_documents'][1]['same_content_as'] == '100001'
    check_zip(directory, report, ['100001','100002'])


def test_archive_failure_preserves_valid_downloads_without_claiming_delivery(collection_site, tmp_path, monkeypatch):
    collection_site()

    def failed(*args, **kwargs):
        raise OSError('Fixture archive disk failure')

    monkeypatch.setattr(probe, 'create_delivery_archive', failed)
    directory, report = run(tmp_path, [('Other Documents','100001',1)])
    assert report['status'] == 'failed'
    assert report['stage'] == 'create_archive'
    assert report['archive'] is None
    assert report['collection_complete'] is False
    assert report['downloaded_document_count'] == 1
    assert (directory/report['downloaded_documents'][0]['path']).exists()


def test_hearing_date_references_export_dialog_witnesses_and_mp3(collection_site, tmp_path):
    requests = collection_site('collection_dates')
    request = CollectionRequest.from_dict({'matter_number':'M12205','documents':[
        {'category':'Transcripts','hearing_date':'01/08/2026','priority':1},
        {'category':'Recordings','hearing_date':'01/07/2026','title':'Day 1','priority':2},
    ]})
    directory, report = asyncio.run(probe.inspect_site(tmp_path,collection_request=request,timeout_ms=10000))
    assert report['status'] == 'collection_complete'
    assert requests == ['Transcripts-01082026.pdf','Recordings-01072026.MP3']
    assert report['downloaded_documents'][0]['witnesses'] == ['Witness 1','Another witness']
    assert 'not fully decoded' in report['downloaded_documents'][1]['file_evidence']['validation']
    check_zip(directory,report,['01/08/2026','01/07/2026'])


def test_export_comma_is_replaced_before_server_builds_disposition_header(collection_site, tmp_path):
    requests = collection_site('collection_dates_comma')
    request = CollectionRequest.from_dict({'matter_number':'M12205','documents':[
        {'category':'Transcripts','hearing_date':'01/07/2026','priority':1},
    ]})
    directory, report = asyncio.run(probe.inspect_site(tmp_path,collection_request=request,timeout_ms=10000))
    assert report['status'] == 'collection_complete'
    assert requests == ['Transcripts_01072026.pdf']
    doc = report['downloaded_documents'][0]
    assert doc['original_export_filename'] == 'Transcripts,01072026.pdf'
    assert doc['export_filename'] == doc['suggested_filename'] == 'Transcripts_01072026.pdf'
    check_zip(directory,report,['01/07/2026'])


@pytest.mark.parametrize(('mode','stage'), [
    ('collection_dates_ambiguous','locate_document'),
    ('collection_dates_blank_export','prepare_export'),
])
def test_ambiguous_date_and_blank_export_filename_fail_safely(mode, stage, collection_site, tmp_path):
    requests = collection_site(mode)
    request = CollectionRequest.from_dict({'matter_number':'M12205','documents':[
        {'category':'Recordings','hearing_date':'01/07/2026','priority':1},
    ]})
    directory,report = asyncio.run(probe.inspect_site(tmp_path,collection_request=request,timeout_ms=10000))
    assert report['status'] == 'failed'
    assert report['document_outcomes'][0]['stage'] == stage
    assert requests == []
    assert report['archive'] is None
