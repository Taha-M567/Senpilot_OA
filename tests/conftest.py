import base64
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from senpilot_agent import probe


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    """Exercise real retry branches without spending provider cooldowns offline."""
    from senpilot_agent import planner, review
    pause = AsyncMock()
    monkeypatch.setattr(planner, 'retry_pause', pause)
    monkeypatch.setattr(review, 'retry_pause', pause)
    return pause


@pytest.fixture
def document_site(monkeypatch):
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(Path(__file__).resolve().parents[1] / '.cache/ms-playwright'))
    fixtures = Path(__file__).parent / 'fixtures'

    def load(mode='success', many=False, media=False, oversized=False, pdf_bytes=None):
        html = (fixtures / 'matter-search.html').read_text(encoding='utf-8').replace('__MODE__', 'success')
        if many or oversized:
            html = html.replace("'Other Documents': 7", f"'Other Documents': {1001 if oversized else 13}")
        if media:
            html = html.replace('Transcripts: 0, Recordings: 0', 'Transcripts: 2, Recordings: 2')
        script = (fixtures / 'document-list.js').read_text(encoding='utf-8').replace('__DOWNLOAD_MODE__', mode)
        if pdf_bytes is not None:
            html += '<script>window.fixturePdfBase64="' + base64.b64encode(pdf_bytes).decode() + '";</script>'
        html += '<script>' + script + '</script>'
        monkeypatch.setattr(probe, 'UARB_URL', 'data:text/html;base64,' + base64.b64encode(html.encode()).decode())

    return load


@pytest.fixture
def pdf_bytes():
    """Small valid PDFs with known text and image-only pages, without external authoring tools."""
    def create(texts=('The Board approved the project subject to conditions.',), encrypted=False):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject, NumberObject

        writer = PdfWriter()
        font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
        for text in texts:
            page = writer.add_blank_page(width=612, height=792)
            stream = DecodedStreamObject()
            if text is None:
                image = DecodedStreamObject()
                image.set_data(b'\xff\xff\xff')
                image.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                    NameObject('/Width'): NumberObject(1), NameObject('/Height'): NumberObject(1),
                    NameObject('/ColorSpace'): NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'): NumberObject(8)})
                page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({
                    NameObject('/Scan'): writer._add_object(image)})})
                stream.set_data(b'q 500 0 0 700 30 30 cm /Scan Do Q')
            else:
                page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
                escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
                stream.set_data(f'BT /F1 12 Tf 40 700 Td ({escaped}) Tj ET'.encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
        if encrypted:
            writer.encrypt('password')
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    return create
