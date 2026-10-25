"""Package only validated collection results, with a verified manifest."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4
import zipfile

from .files import validate_download


def create_delivery_archive(run_dir: Path, documents: list[dict], manifest: dict) -> dict:
    if not documents or len(documents) > 10:
        raise ValueError("A delivery ZIP requires between 1 and 10 validated documents")
    run_dir = run_dir.resolve()
    downloads = run_dir / "downloads"
    entries = []
    paths = []
    for document in documents:
        relative = Path(document["path"])
        path = run_dir / relative
        if relative.is_absolute() or path.parent.resolve() != downloads or path.is_symlink():
            raise ValueError("Archive source must be a regular file inside this run's downloads directory")
        evidence = validate_download(path).to_dict()
        if not evidence["format_validated"] or evidence != document["file_evidence"]:
            raise ValueError("Archive source no longer matches its validated file evidence")
        if path.name in {".", ".."} or any(c in path.name for c in "\\/:"):
            raise ValueError("Unsafe archive member name")
        member = f"documents/{path.name}"
        entries.append({**document, "archive_member": member})
        paths.append(path)
    members = [entry["archive_member"] for entry in entries]
    if len(set(members)) != len(members):
        raise ValueError("Duplicate archive members are not allowed")
    payload = json.dumps({**manifest, "files": entries}, indent=2, ensure_ascii=False).encode("utf-8")
    temporary = run_dir / f"delivery-{uuid4().hex}.tmp"
    destination = run_dir / "delivery.zip"
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, member in zip(paths, members):
                archive.write(path, member)
            archive.writestr("manifest.json", payload)
        with zipfile.ZipFile(temporary) as archive:
            if archive.namelist() != members + ["manifest.json"] or archive.testzip() is not None:
                raise ValueError("Delivery ZIP members or CRC checks failed")
            if archive.read("manifest.json") != payload:
                raise ValueError("Delivery ZIP manifest does not match")
            for entry in entries:
                if archive.getinfo(entry["archive_member"]).file_size != entry["file_evidence"]["size_bytes"]:
                    raise ValueError("Delivery ZIP document size does not match")
                with archive.open(entry["archive_member"]) as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                if checksum != entry["file_evidence"]["sha256"]:
                    raise ValueError("Delivery ZIP document checksum does not match")
        temporary.replace(destination)
        return {"path": destination.name, "document_count": len(entries),
                "members": members + ["manifest.json"],
                "file_evidence": validate_download(destination).to_dict()}
    finally:
        temporary.unlink(missing_ok=True)
