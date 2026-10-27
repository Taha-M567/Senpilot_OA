import asyncio
import base64
import builtins
import json
import os
import zipfile
from pathlib import Path

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
    assert json.loads((directory / "report.json").read_text())["error"]["type"] == "ModuleNotFoundError"
    assert report['intended_request'] == {'matter_number': None}
    assert report['verified_matter_number'] is None


@pytest.fixture
def offline_site(monkeypatch):
    cache = Path(__file__).resolve().parents[1] / '.cache' / 'ms-playwright'
    if cache.exists() and 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ:
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(cache))
    template = (Path(__file__).parent / 'fixtures' / 'matter-search.html').read_text(encoding='utf-8')

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


@pytest.mark.parametrize(('category', 'expected'), [
    ('Exhibits', 2), ('Key Documents', 4), ('Other Documents', 7),
    ('Transcripts', 0), ('Recordings', 0),
])
def test_download_categories_and_archive_contents(category, expected, document_site, tmp_path):
    document_site()
    directory, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', category=category, timeout_ms=2000))
    assert report['status'] == ('documents_ready' if expected else 'no_documents'), report.get('error')
    assert report['retrieval_complete'] is True
    assert report['downloaded_document_count'] == expected
    assert report['download_errors'] == []
    assert report['capture_errors'] == []
    assert report['intended_request'] == {'matter_number': 'M12205', 'category': category, 'limit': 10}
    if not expected:
        assert report['archive'] is None
        assert not list(directory.glob('M12205_*.zip'))
        return
    with zipfile.ZipFile(directory / report['archive']['filename']) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {item['filename'] for item in report['downloads']}
        assert len(archive.namelist()) == expected
        for index, item in enumerate(report['downloads']):
            path = directory / item['relative_path']
            assert path.read_bytes() == archive.read(item['filename'])
            assert f'fixture {index}'.encode() in path.read_bytes()
            assert len(item['sha256']) == 64
            assert item['suggested_filename'] == 'same.pdf'
    assert len({item['filename'] for item in report['downloads']}) == expected


@pytest.mark.parametrize('category', ['Transcripts', 'Recordings'])
def test_nonempty_media_categories_use_the_same_download_flow(category, document_site, tmp_path):
    document_site(media=True)
    _, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', category=category, timeout_ms=2000))
    assert report['status'] == 'documents_ready', report.get('error')
    assert report['downloaded_document_count'] == report['archive']['document_count'] == 2


def test_document_labels_with_regex_characters_are_matched_literally(document_site, tmp_path):
    document_site('special_id')
    _, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', category='Exhibits', limit=1, timeout_ms=2000))
    assert report['status'] == 'documents_ready', report.get('error')
    assert report['downloads'][0]['document_id'] == 'H.1+(A)'


@pytest.mark.parametrize('limit', [1, 10])
def test_limit_and_overlapping_virtual_grid_pages(limit, document_site, tmp_path):
    document_site(many=True)
    _, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', category='Other Documents', limit=limit, timeout_ms=2000))
    assert report['status'] == 'documents_ready', report.get('error')
    assert report['downloaded_document_count'] == limit
    assert [item['document_id'] for item in report['downloads']] == [str(1000 + i) for i in range(limit)]
    assert report['archive']['document_count'] == limit
    assert report['category_counts']['Other Documents'] == 13


@pytest.mark.parametrize(('mode', 'expected'), [('partial', 2), ('all_fail', 0), ('download_timeout', 0), ('missing_dialog', 0)])
def test_failed_documents_are_reported_and_excluded_from_zip(mode, expected, document_site, tmp_path):
    document_site(mode)
    directory, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', category='Other Documents', limit=3, timeout_ms=1000))
    assert report['status'] == 'partial_download', report.get('error')
    assert report['retrieval_complete'] is False
    assert report['downloaded_document_count'] == expected
    assert len(report['download_errors']) == 3 - expected
    assert len(list((directory / 'downloads').glob('*'))) == expected
    if expected:
        with zipfile.ZipFile(directory / report['archive']['filename']) as archive:
            assert len(archive.namelist()) == expected
    else:
        assert report['archive'] is None


@pytest.mark.parametrize('mode', ['early_end', 'duplicate_ids', 'stuck_grid'])
def test_list_failures_preserve_evidence_and_any_partial_archive(mode, document_site, tmp_path):
    document_site(mode)
    directory, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', category='Other Documents', timeout_ms=1000))
    assert report['status'] == 'failed'
    assert report['retrieval_complete'] is False
    assert report['stage'] == 'read_document_list'
    assert report['error']['message']
    if report['downloads']:
        assert (directory / report['archive']['filename']).is_file()


@pytest.mark.parametrize(('category', 'matter', 'limit'), [
    ('Unknown', 'M12205', 10), ('Exhibits', None, 10), ('Exhibits', 'M12205', 0), ('Exhibits', 'M12205', 11),
    ('Exhibits', '../bad', 10), ('Exhibits', 'M12205', 1.5),
])
def test_invalid_retrieval_arguments_fail_before_creating_artifacts(category, matter, limit, tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(inspect_site(tmp_path, category=category, matter_number=matter, limit=limit))
    assert not list(tmp_path.iterdir())
