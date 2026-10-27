"""Bounded PDF text extraction in a disposable subprocess; no OCR or visual review."""

import asyncio
import json
import os
from pathlib import Path
import sys


MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PAGES = 100
MAX_CHARACTERS = 100_000
MAX_PAGE_STREAM_BYTES = 10 * 1024 * 1024
EXTRACTION_TIMEOUT_SECONDS = 60
TEXT_SCOPE = 'Extracted PDF text only; images, diagrams, and visual layout are not reviewed.'


def _extract_pdf(path: Path) -> dict:
    from pypdf import PdfReader

    result = {'status': 'starting', 'scope': TEXT_SCOPE, 'page_count': None,
              'pages': [], 'unreadable_pages': [], 'warnings': [], 'complete_text': False}
    try:
        if not path.is_file() or path.is_symlink():
            raise ValueError('Expected a regular downloaded file')
        if path.suffix.lower() != '.pdf':
            result.update(status='unsupported', message='Only PDF text extraction is supported')
            return result
        if path.stat().st_size > MAX_FILE_BYTES:
            result.update(status='limit_exceeded', message='PDF exceeds the 20 MiB file limit')
            return result
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            result.update(status='encrypted', message='Password-protected PDFs are not reviewed')
            return result
        result['page_count'] = len(reader.pages)
        if not 1 <= result['page_count'] <= MAX_PAGES:
            result.update(status='limit_exceeded', message='PDF must contain between 1 and 100 pages')
            return result
        characters = 0
        for number, page in enumerate(reader.pages, 1):
            contents = page.get_contents()
            if contents is not None and len(contents.get_data()) > MAX_PAGE_STREAM_BYTES:
                result.update(status='limit_exceeded', message=f'Page {number} exceeds the content-stream limit')
                return result
            text = page.extract_text() or ''
            characters += len(text)
            if characters > MAX_CHARACTERS:
                result.update(status='limit_exceeded', message='PDF exceeds the 100,000-character text limit')
                return result
            result['pages'].append({'page': number, 'text': text})
            if not text.strip():
                result['unreadable_pages'].append(number)
        if result['unreadable_pages']:
            result['warnings'].append('Pages without usable text may be scanned, image-only, or blank.')
            result.update(status='incomplete_text', message='Not every PDF page has extractable text')
        else:
            result.update(status='extracted', complete_text=True)
    except Exception as exc:
        result.update(status='extraction_failed', message=f'PDF parsing failed ({type(exc).__name__})')
    return result


async def extract_document(path: Path, destination: Path) -> dict:
    """Persist all extraction outcomes; kill a parser that exceeds its deadline."""
    environment = dict(os.environ)
    source = str(Path(__file__).resolve().parents[1])
    environment['PYTHONPATH'] = source + os.pathsep + environment.get('PYTHONPATH', '')
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'senpilot_agent.content', str(path.resolve()),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=environment,
        )
        async with asyncio.timeout(EXTRACTION_TIMEOUT_SECONDS):
            stdout, _ = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError('PDF extraction worker failed')
        result = json.loads(stdout)
        if not isinstance(result, dict) or 'status' not in result:
            raise ValueError('Invalid extraction worker result')
    except TimeoutError:
        result = {'status': 'timeout', 'scope': TEXT_SCOPE, 'complete_text': False,
                  'pages': [], 'page_count': None, 'unreadable_pages': [], 'warnings': [],
                  'message': 'PDF extraction exceeded its 60-second deadline'}
    except Exception as exc:
        result = {'status': 'extraction_failed', 'scope': TEXT_SCOPE, 'complete_text': False,
                  'pages': [], 'page_count': None, 'unreadable_pages': [], 'warnings': [],
                  'message': f'PDF extraction worker failed ({type(exc).__name__})'}
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.communicate()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return result


if __name__ == '__main__':
    print(json.dumps(_extract_pdf(Path(sys.argv[1])), ensure_ascii=True))
