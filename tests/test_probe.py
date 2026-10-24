import asyncio
import base64
import builtins
import json
import os
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
    assert report["phase1_milestone_complete"] is False
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
    ('success', 'verify_matter', None),
    ('ambiguous_search', 'submit_matter_search', 'strict mode violation'),
    ('not_found', 'verify_matter', 'not found: No Records Found'),
    ('mismatch', 'verify_matter', 'identifier mismatch'),
    ('timeout', 'verify_matter', 'Timeout'),
    ('missing_field', 'locate_matter_field', 'Timeout'),
    ('inactive_editor', 'locate_matter_field', 'Timeout'),
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
    else:
        assert report['status'] == 'failed'
        assert report['verified_matter_number'] is None
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
    assert 'Go Directly to Matter' in (directory / 'page.txt').read_text()
