import asyncio
import base64
import builtins
import json
import os
from pathlib import Path
import hashlib

import pytest

from senpilot_agent.probe import allowed_url, inspect_site
from senpilot_agent import probe


@pytest.mark.parametrize("url", [
    "https://uarb.novascotia.ca/fmi/webd/UARB15",
    "https://uarb.novascotia.ca:443/file",
    "wss://uarb.novascotia.ca/session",
])
def test_permits_only_intended_secure_origin(url):
    assert allowed_url(url)


@pytest.mark.parametrize("url", [
    "http://uarb.novascotia.ca/file", "https://example.com/",
    "https://uarb.novascotia.ca.example.com/", "file:///etc/passwd",
    "https://uarb.novascotia.ca:444/", "https://user:pass@uarb.novascotia.ca/",
    "https://uarb.novascotia.ca:invalid/", "https://[malformed/",
])
def test_rejects_other_origins_and_credentials(url):
    assert not allowed_url(url)


def test_missing_playwright_produces_report_without_claiming_success(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ModuleNotFoundError("Playwright intentionally unavailable in fixture")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    directory, report = asyncio.run(inspect_site(tmp_path))
    assert report["status"] == "failed"
    assert report["stage"] == "import_playwright"
    assert report["phase1_milestone_complete"] is False
    assert json.loads((directory / "report.json").read_text())["error"]["type"] == "ModuleNotFoundError"
    assert report['intended_request'] == {'matter_number': None}
    assert report['verified_matter_number'] is None


@pytest.fixture
def offline_site(monkeypatch):
    from playwright.async_api import BrowserContext

    cache = Path(__file__).resolve().parents[1] / '.cache' / 'ms-playwright'
    if cache.exists() and 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ:
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(cache))
    template = (Path(__file__).parent / 'fixtures' / 'matter-search.html').read_text(encoding='utf-8')
    new_page = BrowserContext.new_page

    async def fixture_page(context):
        page = await new_page(context)

        async def attachment(route):
            # No server/network: reproduce the attachment response to the dialog
            # filename click, while retaining the production origin guard.
            name = route.request.url.rsplit('/', 1)[-1]
            payload = {
                'html.pdf': b'<html>Server error</html>',
                'empty.pdf': b'',
                'truncated.pdf': b'%PDF-1.7\ntruncated',
                'unknown.mp3': b'fixture unknown format',
            }.get(name, b'%PDF-1.7\nsynthetic offline download\n%%EOF\n')
            await route.fulfill(status=200, body=payload, headers={
                'Content-Type': 'application/octet-stream',
                'Content-Disposition': f'attachment; filename="{name}"',
            })

        await page.route('https://uarb.novascotia.ca/fixture/*', attachment)
        return page

    monkeypatch.setattr(BrowserContext, 'new_page', fixture_page)

    def load(mode):
        html = template.replace('__MODE__', mode)
        monkeypatch.setattr(probe, 'UARB_URL', 'data:text/html;base64,' + base64.b64encode(html.encode()).decode())

    return load


@pytest.mark.parametrize(('mode', 'stage', 'error'), [
    ('success', 'extract_category_counts', None),
    ('blank_optional', 'extract_category_counts', None),
    ('all_zero', 'extract_category_counts', None),
    ('ambiguous_search', 'submit_matter_search', 'strict mode violation'),
    ('not_found', 'verify_matter', 'not found: No Records Found'),
    ('mismatch', 'verify_matter', 'identifier mismatch'),
    ('timeout', 'verify_matter', 'Timeout'),
    ('missing_field', 'locate_matter_field', 'Timeout'),
    ('inactive_editor', 'locate_matter_field', 'Timeout'),
    ('missing_metadata', 'extract_metadata', 'Timeout'),
    ('ambiguous_metadata', 'extract_metadata', 'strict mode violation'),
    ('missing_title', 'extract_metadata', 'Matter title must be nonempty'),
    ('metadata_timeout', 'extract_metadata', 'Matter title must be nonempty'),
    ('missing_count', 'extract_category_counts', 'Invalid or unavailable count for Other Documents'),
    ('ambiguous_count', 'extract_category_counts', 'strict mode violation'),
    ('malformed_count', 'extract_category_counts', 'Invalid or unavailable count for Other Documents'),
    ('negative_count', 'extract_category_counts', 'Invalid or unavailable count for Other Documents'),
    ('count_timeout', 'extract_category_counts', 'Invalid or unavailable count for Other Documents'),
])
def test_offline_search_and_failure_evidence(mode, stage, error, offline_site, tmp_path):
    offline_site(mode)
    directory, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', timeout_ms=1000))
    assert report['stage'] == stage
    assert report['intended_request'] == {'matter_number': 'M12205'}
    assert report['phase1_milestone_complete'] is False
    assert report['capture_errors'] == []
    assert report['failed_requests'] == []
    assert report['blocked_origins'] == []
    assert json.loads((directory / 'report.json').read_text()) == report
    for filename in ('page.png', 'page.html', 'page.txt', 'trace.zip'):
        assert (directory / filename).stat().st_size > 0
    if error is None:
        assert report['status'] == 'matter_found'
        assert report['observed_matter_number'] == report['verified_matter_number'] == 'M12205'
        assert 'Back to Search Results' in (directory / 'page.txt').read_text()
        assert report['metadata_counts_complete'] is True
        expected_metadata = {
            'matter_number': 'M12205', 'title': 'Fixture water project',
            'status': 'Open', 'type': 'Capital Expenditure Approvals', 'category': 'Water',
            'date_received': '01/02/2026', 'decision_date': '03/04/2026', 'outcome': 'Fixture outcome',
        }
        if mode == 'blank_optional':
            for name in ('status', 'type', 'category', 'date_received', 'decision_date', 'outcome'):
                expected_metadata[name] = None
        assert report['matter_metadata'] == expected_metadata
        expected_counts = {'Exhibits': 2, 'Key Documents': 4, 'Other Documents': 7, 'Transcripts': 0, 'Recordings': 0}
        if mode == 'all_zero':
            expected_counts = dict.fromkeys(expected_counts, 0)
        assert report['category_counts'] == expected_counts
        assert report['total_document_count'] == sum(expected_counts.values())
    else:
        assert report['status'] == 'failed'
        assert report['verified_matter_number'] == ('M12205' if stage.startswith('extract_') else None)
        assert report['metadata_counts_complete'] is False
        assert report['category_counts'] == {}
        assert report['total_document_count'] is None
        if stage == 'extract_category_counts':
            assert report['matter_metadata']['title'] == 'Fixture water project'
        assert error in report['error']['message']
        if mode == 'mismatch':
            assert report['observed_matter_number'] == 'M54321'


def test_offline_inspection_does_not_search(offline_site, tmp_path):
    offline_site('success')
    directory, report = asyncio.run(inspect_site(tmp_path, timeout_ms=1000))
    assert report['status'] == 'inspection_captured'
    assert report['stage'] == 'inspect_dom'
    assert report['intended_request'] == {'matter_number': None}
    assert report['verified_matter_number'] is None
    assert report['metadata_counts_complete'] is False
    assert report['matter_metadata'] == report['category_counts'] == {}
    assert report['total_document_count'] is None
    assert 'Go Directly to Matter' in (directory / 'page.txt').read_text()


@pytest.mark.parametrize(('mode', 'stage', 'error'), [
    ('success', 'validate_download', None),
    ('all_zero', 'open_category', 'no documents'),
    ('wrong_selected_tab', 'open_category', 'Timeout'),
    ('wrong_list_count', 'open_category', 'Found Count'),
    ('ambiguous_grid', 'select_document', 'Locator expected'),
    ('missing_document_number', 'select_document', 'Locator expected'),
    ('missing_dialog', 'prepare_download', 'Timeout'),
    ('ambiguous_filename', 'prepare_download', 'Locator expected'),
    ('download_timeout', 'download_document', 'Timeout'),
    ('html_download', 'validate_download', 'HTML page'),
    ('empty_download', 'validate_download', 'empty'),
    ('truncated_download', 'validate_download', 'end marker'),
    ('unknown_download', 'validate_download', 'format is not validated'),
])
def test_one_document_workflow(mode, stage, error, offline_site, tmp_path):
    offline_site(mode)
    directory, report = asyncio.run(inspect_site(
        tmp_path, matter_number='M12205', download_one=True, timeout_ms=1000,
    ))
    assert report['stage'] == stage
    assert report['verified_matter_number'] == 'M12205'
    assert report['metadata_counts_complete'] is True
    assert report['intended_request'] == {
        'matter_number': 'M12205', 'document_category': 'Other Documents', 'download_limit': 1,
    }
    assert report['capture_errors'] == report['blocked_origins'] == []
    # Chromium aborts a navigation when it becomes an attachment. Keep that raw
    # diagnostic rather than hiding all ERR_ABORTED failures in production.
    assert report['failed_requests'] == (
        [{'host': 'uarb.novascotia.ca', 'reason': 'net::ERR_ABORTED'}]
        if stage == 'validate_download' else []
    )
    assert json.loads((directory / 'report.json').read_text()) == report
    assert (directory / 'trace.zip').stat().st_size > 0
    if error:
        assert report['status'] == 'failed'
        assert error in report['error']['message']
        assert report['phase1_milestone_complete'] is False
        assert report['downloaded_documents'] == []
        assert report['downloaded_document_count'] == 0
        if stage == 'validate_download':
            assert (directory / report['download_candidate']['path']).exists()
    else:
        assert report['status'] == 'document_downloaded'
        assert report['phase1_milestone_complete'] is True
        assert report['downloaded_document_count'] == 1
        document, = report['downloaded_documents']
        assert document['matter_number'] == 'M12205'
        assert document['document_category'] == 'Other Documents'
        assert document['document_number'] == '123456'
        assert document['title'] == 'Fixture first document'
        assert document['displayed_filename'] == document['suggested_filename'] == '123456.pdf'
        # The browser is closed: bytes must persist independently of its temps.
        path = directory / document['path']
        payload = path.read_bytes()
        assert path.parent == directory / 'downloads'
        assert payload == b'%PDF-1.7\nsynthetic offline download\n%%EOF\n'
        assert document['file_evidence']['sha256'] == hashlib.sha256(payload).hexdigest()
        assert document['file_evidence']['size_bytes'] == len(payload)
        assert document['file_evidence']['format_validated'] is True


def test_download_requires_matter_before_browser_launch(tmp_path):
    _, report = asyncio.run(inspect_site(tmp_path, download_one=True))
    assert report['status'] == 'failed'
    assert report['stage'] == 'validate_request'
    assert 'requires a matter' in report['error']['message']


def test_save_failure_keeps_matter_and_candidate_without_claiming_download(
    offline_site, tmp_path, monkeypatch,
):
    from playwright.async_api import Download

    async def unwritable(download, path):
        raise OSError('Fixture destination is unwritable')

    monkeypatch.setattr(Download, 'save_as', unwritable)
    offline_site('success')
    directory, report = asyncio.run(inspect_site(
        tmp_path, matter_number='M12205', download_one=True, timeout_ms=1000,
    ))
    assert report['status'] == 'failed'
    assert report['stage'] == 'download_document'
    assert 'unwritable' in report['error']['message']
    assert report['metadata_counts_complete'] is True
    assert report['download_candidate']['document_number'] == '123456'
    assert not (directory / report['download_candidate']['path']).exists()
    assert report['downloaded_document_count'] == 0
    assert report['phase1_milestone_complete'] is False
