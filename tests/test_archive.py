import hashlib
import json
import zipfile

import pytest

from senpilot_agent.archive import create_delivery_archive
from senpilot_agent.files import safe_download_path, validate_download


def document(run_dir, number="123", payload=None):
    path = safe_download_path(run_dir / "downloads", number + ".pdf")
    path.write_bytes(payload or f"%PDF-1.7\nfixture {number}\n%%EOF\n".encode())
    return {"matter_number": "M12205", "category": "Other Documents", "document_number": number,
            "path": path.relative_to(run_dir).as_posix(), "file_evidence": validate_download(path).to_dict()}


def test_delivery_archive_contains_exact_files_manifest_and_priority_order(tmp_path):
    docs = [document(tmp_path, "456"), document(tmp_path, "123")]
    (tmp_path / "trace.zip").write_bytes(b"browser session must not be delivered")
    (tmp_path / "downloads" / "invalid.pdf").write_bytes(b"invalid file must not be delivered")
    result = create_delivery_archive(tmp_path, docs, {"status": "complete", "category_counts": {"Other Documents": 25}})
    assert result["document_count"] == 2
    path = tmp_path / result["path"]
    assert result["file_evidence"] == validate_download(path).to_dict()
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == [f"documents/{d['file_evidence']['filename']}" for d in docs] + ["manifest.json"]
        assert archive.testzip() is None
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["status"] == "complete"
        assert [d["document_number"] for d in manifest["files"]] == ["456", "123"]
        for entry in manifest["files"]:
            data = archive.read(entry["archive_member"])
            assert len(data) == entry["file_evidence"]["size_bytes"]
            assert hashlib.sha256(data).hexdigest() == entry["file_evidence"]["sha256"]
    assert not list(tmp_path.glob("*.tmp"))


def test_archive_rejects_changed_sources(tmp_path):
    doc = document(tmp_path)
    (tmp_path / doc["path"]).write_bytes(b"%PDF-1.7\nchanged\n%%EOF")
    with pytest.raises(ValueError, match="no longer matches"):
        create_delivery_archive(tmp_path, [doc], {})
    assert not (tmp_path / "delivery.zip").exists()


def test_archive_detects_change_during_packaging(tmp_path, monkeypatch):
    doc = document(tmp_path)
    original = zipfile.ZipFile.write

    def changed(archive, filename, *args, **kwargs):
        filename.write_bytes(filename.read_bytes().replace(b"fixture", b"changed"))
        return original(archive, filename, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", changed)
    with pytest.raises(ValueError, match="checksum"):
        create_delivery_archive(tmp_path, [doc], {})
    assert not (tmp_path / "delivery.zip").exists()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("relative", ["../outside.pdf", "page.png", "downloads/../../outside.pdf"])
def test_archive_rejects_paths_outside_downloads(tmp_path, relative):
    doc = document(tmp_path)
    doc["path"] = relative
    with pytest.raises(ValueError, match="inside this run"):
        create_delivery_archive(tmp_path, [doc], {})


def test_archive_rejects_duplicate_and_unvalidated_files(tmp_path):
    doc = document(tmp_path)
    with pytest.raises(ValueError, match="Duplicate"):
        create_delivery_archive(tmp_path, [doc, doc], {})
    path = safe_download_path(tmp_path / "downloads", "unknown.bin")
    path.write_bytes(b"unknown")
    doc.update(path=path.relative_to(tmp_path).as_posix(), file_evidence=validate_download(path).to_dict())
    with pytest.raises(ValueError, match="validated file evidence"):
        create_delivery_archive(tmp_path, [doc], {})


def test_archive_empty_or_over_limit_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="1 and 10"):
        create_delivery_archive(tmp_path, [], {})
    doc = document(tmp_path)
    with pytest.raises(ValueError, match="1 and 10"):
        create_delivery_archive(tmp_path, [doc] * 11, {})


def test_archive_write_failure_never_publishes_partial_zip(tmp_path, monkeypatch):
    doc = document(tmp_path)

    def no_disk(*args, **kwargs):
        raise OSError("Fixture disk write failure")

    monkeypatch.setattr(zipfile.ZipFile, "write", no_disk)
    with pytest.raises(OSError, match="disk write"):
        create_delivery_archive(tmp_path, [doc], {})
    assert not (tmp_path / "delivery.zip").exists()
    assert not list(tmp_path.glob("*.tmp"))
