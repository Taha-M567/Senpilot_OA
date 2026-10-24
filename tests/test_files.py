import hashlib
import zipfile

import pytest

from senpilot_agent.files import safe_download_path, validate_download


@pytest.mark.parametrize("name", ["../../escape.pdf", r"C:\temp\file.pdf", "CON", "...", "", "a:b?.pdf"])
def test_filename_stays_in_download_directory(tmp_path, name):
    path = safe_download_path(tmp_path / "downloads", name)
    assert path.parent == (tmp_path / "downloads").resolve()
    assert path.name not in {"CON", "", ".", ".."}
    assert not any(char in path.name for char in ':?<>|/\\')


def test_duplicate_suggested_names_get_unique_paths(tmp_path):
    assert safe_download_path(tmp_path, "report.pdf") != safe_download_path(tmp_path, "report.pdf")


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
