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
    format_validated: bool

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
    unfamiliar file format is valid. A full document parser belongs in Phase 2.
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
    format_validated = False
    if path.suffix.lower() == ".pdf":
        if not header.startswith(b"%PDF-") or b"%%EOF" not in tail:
            raise ValueError("PDF signature or end marker is missing")
        validation = "PDF signature and end marker checked; not fully parsed"
        format_validated = True
    elif path.suffix.lower() in {".zip", ".docx", ".xlsx", ".pptx"}:
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise ValueError("Archive contains a corrupt member")
        except zipfile.BadZipFile as exc:
            raise ValueError("Download is not a valid ZIP container") from exc
        validation = "ZIP container CRC checked"
        format_validated = True
    elif path.suffix.lower() == ".mp3":
        _check_mp3_frame(path, size)
        validation = "MP3 ID3 size and first MPEG Layer III frame checked; not fully decoded"
        format_validated = True
    return FileEvidence(path.name, size, checksum, validation, format_validated)


def _check_mp3_frame(path: Path, size: int) -> None:
    """Bounded header/first-frame check, not an audio decoder or integrity scan."""
    with path.open("rb") as stream:
        tag = stream.read(10)
        start = 0
        if tag.startswith(b"ID3"):
            if len(tag) != 10 or tag[3] not in {2, 3, 4} or any(b & 0x80 for b in tag[6:10]):
                raise ValueError("Invalid MP3 ID3 header")
            start = 10 + sum(b << shift for b, shift in zip(tag[6:10], (21, 14, 7, 0)))
            if start >= size:
                raise ValueError("MP3 ID3 tag exceeds the audio payload")
        stream.seek(start)
        data = stream.read(4096)
    for offset in range(max(0, len(data) - 3)):
        a, b, c, _ = data[offset:offset + 4]
        version, layer = (b >> 3) & 3, (b >> 1) & 3
        rate, sample = c >> 4, (c >> 2) & 3
        if a != 255 or b & 0xE0 != 0xE0 or version == 1 or layer != 1 or rate in {0, 15} or sample == 3:
            continue
        bitrates = ([0,32,40,48,56,64,80,96,112,128,160,192,224,256,320] if version == 3
                    else [0,8,16,24,32,40,48,56,64,80,96,112,128,144,160])
        frequency = [44100, 48000, 32000][sample] // ({3:1, 2:2, 0:4}[version])
        length = ((144000 if version == 3 else 72000) * bitrates[rate] // frequency) + ((c >> 1) & 1)
        if start + offset + length <= size:
            return
    raise ValueError("MP3 MPEG Layer III frame signature or complete first frame is missing")
