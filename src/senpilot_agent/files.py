"""Small, deterministic checks for downloaded files; no browser dependencies."""

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import re
from uuid import uuid4
import zipfile


@dataclass(frozen=True)
class FileEvidence:
    filename: str
    size_bytes: int
    sha256: str
    validation: str

    def to_dict(self) -> dict:
        return asdict(self)


def safe_download_path(directory: Path, suggested_filename: str) -> Path:
    """Use an opaque prefix, discard all path components, and stay in directory."""
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    basename = suggested_filename.replace("\\", "/").rsplit("/", 1)[-1]
    basename = re.sub(r'[^\w.() -]', "_", basename, flags=re.ASCII)
    basename = basename.strip(" .") or "document"
    # Keep the suffix when shortening long names; UUID also avoids Windows devices.
    suffix = Path(basename).suffix[:16]
    stem = basename[:-len(suffix)] if suffix else basename
    filename = f"{uuid4().hex}_{stem[:140]}{suffix}"
    destination = directory / filename
    if destination.parent != directory:
        raise ValueError("Download path escapes its directory")
    return destination


def validate_download(path: Path) -> FileEvidence:
    """Reject empty/error responses; perform basic format checks and hash contents.

    These checks do not establish semantic correctness, malware safety, or that an
    unfamiliar file format is valid. PDF text extraction is handled by content.py.
    """
    if not path.is_file() or path.is_symlink():
        raise ValueError("Download must be a regular, non-symlink file")
    size = path.stat().st_size
    if size == 0:
        raise ValueError("Download is empty")
    with path.open("rb") as stream:
        header = stream.read(1024)
        stream.seek(max(0, size - 2048))
        tail = stream.read()
        stream.seek(0)
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    normalized = header.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if normalized.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        raise ValueError("Downloaded an HTML page instead of a document")
    validation = "nonempty; format not validated"
    if path.suffix.lower() == ".pdf":
        if not header.startswith(b"%PDF-") or b"%%EOF" not in tail:
            raise ValueError("PDF signature or end marker is missing")
        validation = "PDF signature and end marker checked; not fully parsed"
    elif path.suffix.lower() in {".zip", ".docx", ".xlsx", ".pptx"}:
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise ValueError("Archive contains a corrupt member")
        except zipfile.BadZipFile as exc:
            raise ValueError("Download is not a valid ZIP container") from exc
        validation = "ZIP container CRC checked"
    return FileEvidence(path.name, size, checksum, validation)


def create_archive(paths: list[Path], destination: Path) -> FileEvidence:
    """Publish a checked ZIP containing only the supplied, validated files."""
    if not paths or len({path.name for path in paths}) != len(paths):
        raise ValueError("Archive requires files with unique names")
    for path in paths:
        validate_download(path)
    if destination.exists():
        raise ValueError("Archive destination already exists")
    temporary = destination.with_name(f'.{destination.name}.{uuid4().hex}.part')
    try:
        with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                archive.write(path, arcname=path.name)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("Created archive failed CRC validation")
        temporary.rename(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return validate_download(destination)
