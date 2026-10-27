import hashlib
import zipfile

import pytest

from senpilot_agent.files import create_archive, safe_download_path, validate_download, write_json


@pytest.mark.parametrize("name", ["../../escape.pdf", r"C:\temp\file.pdf", "CON", "...", "", "a:b?.pdf"])
def test_filename_stays_in_download_directory(tmp_path, name):
    path = safe_download_path(tmp_path / "downloads", name)
    assert path.parent == (tmp_path / "downloads").resolve()
    assert path.name not in {"CON", "", ".", ".."}
    assert not any(char in path.name for char in ':?<>|/\\')


def test_duplicate_suggested_names_get_unique_paths(tmp_path):
    assert safe_download_path(tmp_path, "report.pdf") != safe_download_path(tmp_path, "report.pdf")


def test_interrupted_checkpoint_replace_preserves_previous_json(tmp_path, monkeypatch):
    from pathlib import Path
    destination = tmp_path/'review.json'
    write_json(destination, {'status':'saved'})
    def interrupted(*args):
        raise PermissionError('Checkpoint is locked')
    monkeypatch.setattr(Path, 'replace', interrupted)
    with pytest.raises(PermissionError):
        write_json(destination, {'status':'new'})
    assert destination.read_text(encoding='utf-8').strip() == '{\n  "status": "saved"\n}'
    assert list(tmp_path.iterdir()) == [destination]


def test_pdf_basic_validation_and_checksum(tmp_path):
    payload = b"%PDF-1.7\nsynthetic test fixture\n%%EOF\n"
    path = tmp_path / "file.pdf"
    path.write_bytes(payload)
    evidence = validate_download(path)
    assert evidence.sha256 == hashlib.sha256(payload).hexdigest()
    assert evidence.size_bytes == len(payload)
    assert "not fully parsed" in evidence.validation


@pytest.mark.parametrize("payload", [b"", b"error", b"%PDF-1.7\ntruncated", b"<html>error</html>", b"\xef\xbb\xbf <!DOCTYPE html>error"])
def test_rejects_empty_truncated_and_html_pdf_responses(tmp_path, payload):
    path = tmp_path / "file.pdf"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        validate_download(path)


def test_missing_file_rejected(tmp_path):
    with pytest.raises(ValueError, match="regular"):
        validate_download(tmp_path / "missing")


def test_unknown_format_is_explicitly_unvalidated(tmp_path):
    path = tmp_path / "recording.mp3"
    path.write_bytes(b"unrecognized recording bytes")
    assert validate_download(path).validation == "nonempty; format not validated"


def test_zip_container_integrity(tmp_path):
    path = tmp_path / "file.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("example.txt", "fixture")
    assert validate_download(path).validation == "ZIP container CRC checked"


def test_invalid_zip_rejected(tmp_path):
    path = tmp_path / "file.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="ZIP"):
        validate_download(path)


def test_create_archive_roundtrips_only_requested_files(tmp_path):
    paths = [tmp_path / 'first.pdf', tmp_path / 'second.pdf']
    for i, path in enumerate(paths):
        path.write_bytes(f'%PDF-1.7\n{i}\n%%EOF'.encode())
    (tmp_path / 'not-requested.txt').write_text('exclude me')
    destination = tmp_path / 'documents.zip'
    evidence = create_archive(paths, destination)
    assert evidence == validate_download(destination)
    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == [path.name for path in paths]
        for path in paths:
            assert archive.read(path.name) == path.read_bytes()
    assert not list(tmp_path.glob('*.part'))


def test_archive_rejects_invalid_files_and_existing_destination(tmp_path):
    destination = tmp_path / 'documents.zip'
    invalid = tmp_path / 'invalid.pdf'
    invalid.write_text('<html>Error</html>')
    with pytest.raises(ValueError):
        create_archive([invalid], destination)
    assert not destination.exists()
    with pytest.raises(ValueError):
        create_archive([], destination)
    valid = tmp_path / 'valid.pdf'
    valid.write_bytes(b'%PDF-1.7\n%%EOF')
    destination.write_bytes(b'keep me')
    with pytest.raises(ValueError):
        create_archive([valid], destination)
    assert destination.read_bytes() == b'keep me'
