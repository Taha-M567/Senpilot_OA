import asyncio
import json
from types import SimpleNamespace

import pytest

from senpilot_agent import content


def test_real_extraction_worker_preserves_physical_pages_and_text(pdf_bytes, tmp_path):
    path = tmp_path / 'source.pdf'
    path.write_bytes(pdf_bytes(['The Board approved the project.', 'The filing deadline is August 15, 2026.']))
    destination = tmp_path / 'extracted' / 'C0001.json'
    result = asyncio.run(content.extract_document(path, destination))
    assert result['status'] == 'extracted'
    assert result['complete_text']
    assert result['page_count'] == 2
    assert [item['page'] for item in result['pages']] == [1, 2]
    assert 'approved' in result['pages'][0]['text']
    assert 'August 15' in result['pages'][1]['text']
    assert json.loads(destination.read_text()) == result


@pytest.mark.parametrize(('texts', 'expected'), [([None], [1]), (['Readable first page.', None], [2]), ([''], [1])])
def test_scanned_empty_and_mixed_pdfs_are_explicitly_incomplete(pdf_bytes, tmp_path, texts, expected):
    path = tmp_path / 'scan.pdf'
    path.write_bytes(pdf_bytes(texts))
    result = content._extract_pdf(path)
    assert result['status'] == 'incomplete_text'
    assert not result['complete_text']
    assert result['unreadable_pages'] == expected
    assert result['warnings']


def test_encrypted_pdf_is_unreviewed(pdf_bytes, tmp_path):
    path = tmp_path / 'encrypted.pdf'
    path.write_bytes(pdf_bytes(encrypted=True))
    assert content._extract_pdf(path)['status'] == 'encrypted'


@pytest.mark.parametrize(('filename', 'payload', 'status'), [
    ('bad.pdf', b'%PDF-1.7\ncorrupt\n%%EOF', 'extraction_failed'),
    ('audio.mp3', b'some media bytes', 'unsupported'), ('empty.pdf', b'', 'extraction_failed'),
])
def test_bad_and_unsupported_files_fail_honestly(tmp_path, filename, payload, status):
    path = tmp_path / filename
    path.write_bytes(payload)
    result = content._extract_pdf(path)
    assert result['status'] == status
    assert not result['complete_text']


@pytest.mark.parametrize('limit', ['MAX_FILE_BYTES', 'MAX_PAGES', 'MAX_CHARACTERS', 'MAX_PAGE_STREAM_BYTES'])
def test_limits_do_not_claim_full_review_or_truncate_successfully(pdf_bytes, tmp_path, monkeypatch, limit):
    path = tmp_path / 'large.pdf'
    path.write_bytes(pdf_bytes(['A complete sentence.', 'Another page.']))
    monkeypatch.setattr(content, limit, 1)
    result = content._extract_pdf(path)
    assert result['status'] == 'limit_exceeded'
    assert not result['complete_text']


def test_hanging_worker_is_killed_and_timeout_is_saved(tmp_path, monkeypatch):
    calls = []

    class Process:
        returncode = None

        async def communicate(self):
            if self.returncode is None:
                await asyncio.Event().wait()
            return b'', b''

        def kill(self):
            self.returncode = -1
            calls.append('killed')

    async def spawn(*args, **kwargs):
        return Process()

    monkeypatch.setattr(content.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(content, 'EXTRACTION_TIMEOUT_SECONDS', 0.01)
    destination = tmp_path / 'timeout.json'
    result = asyncio.run(content.extract_document(tmp_path / 'source.pdf', destination))
    assert calls == ['killed']
    assert result['status'] == 'timeout'
    assert json.loads(destination.read_text())['complete_text'] is False


def test_crashed_worker_is_a_saved_failure(tmp_path, monkeypatch):
    async def communicate():
        return b'not json', b''

    async def spawn(*args, **kwargs):
        return SimpleNamespace(returncode=1, communicate=communicate)

    monkeypatch.setattr(content.asyncio, 'create_subprocess_exec', spawn)
    result = asyncio.run(content.extract_document(tmp_path / 'source.pdf', tmp_path / 'result.json'))
    assert result['status'] == 'extraction_failed'
