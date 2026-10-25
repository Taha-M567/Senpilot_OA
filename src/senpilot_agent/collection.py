"""Validated priorities supplied by an upstream stage; no ranking inference."""

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import re


CATEGORIES = ("Exhibits", "Key Documents", "Other Documents", "Transcripts", "Recordings")


def _fields(value, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, dict):
        raise ValueError("Request and document entries must be JSON objects")
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing or unknown:
        raise ValueError(f"Invalid fields: missing={sorted(missing)}, unknown={sorted(unknown)}")


@dataclass(frozen=True)
class DocumentPriority:
    category: str
    document_number: str | None
    priority: int
    hearing_date: str | None = None
    title: str | None = None

    def __post_init__(self):
        if self.category not in CATEGORIES:
            raise ValueError(f"Unsupported document category: {self.category!r}")
        if self.category in {"Transcripts", "Recordings"}:
            if self.document_number is not None or not isinstance(self.hearing_date, str):
                raise ValueError("Transcripts and Recordings require hearing_date instead of document_number")
            if not re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", self.hearing_date):
                raise ValueError("hearing_date must use the site's MM/DD/YYYY format")
            try:
                datetime.strptime(self.hearing_date, "%m/%d/%Y")
            except ValueError as exc:
                raise ValueError("Invalid hearing_date") from exc
        elif (self.hearing_date is not None or not isinstance(self.document_number, str)
              or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9()_. -]{0,79}", self.document_number)
              or self.document_number != self.document_number.strip()):
            raise ValueError("Document number must be an exact displayed identifier, such as 102674 or H-1")
        if type(self.priority) is not int or self.priority < 1:
            raise ValueError("Document priority must be a positive integer")
        if self.title is not None and (not isinstance(self.title, str) or not self.title.strip()):
            raise ValueError("Optional title must be a nonempty exact row title")

    @property
    def reference(self) -> str:
        return self.document_number if self.document_number is not None else self.hearing_date

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class CollectionRequest:
    matter_number: str
    documents: tuple[DocumentPriority, ...]
    limit: int = 10

    def __post_init__(self):
        if not isinstance(self.matter_number, str) or not re.fullmatch(r"M[0-9]{5}", self.matter_number):
            raise ValueError("Matter number must be M followed by exactly five ASCII digits")
        if type(self.limit) is not int or not 1 <= self.limit <= 10:
            raise ValueError("Collection limit must be an integer between 1 and 10")
        if not isinstance(self.documents, tuple) or not all(isinstance(d, DocumentPriority) for d in self.documents):
            raise ValueError("Documents must be a tuple of DocumentPriority entries")
        numbers = [("number", d.document_number) if d.document_number else
                   (d.category, d.hearing_date, d.title) for d in self.documents]
        if len(numbers) != len(set(numbers)):
            raise ValueError("Duplicate document numbers are not allowed, including across categories")

    @classmethod
    def from_dict(cls, value: dict) -> "CollectionRequest":
        _fields(value, {"matter_number", "documents"}, {"limit"})
        if not isinstance(value["documents"], list):
            raise ValueError("documents must be a JSON array")
        documents = []
        for entry in value["documents"]:
            _fields(entry, {"category", "priority"}, {"document_number", "hearing_date", "title"})
            documents.append(DocumentPriority(document_number=entry.get("document_number"),
                                              **{k: v for k, v in entry.items() if k != "document_number"}))
        return cls(value["matter_number"], tuple(documents), value.get("limit", 10))

    def selected(self) -> tuple[DocumentPriority, ...]:
        # Python's sort is stable: equal priority preserves upstream input order.
        return tuple(sorted(self.documents, key=lambda d: d.priority)[:self.limit])

    def to_dict(self) -> dict:
        return {"matter_number": self.matter_number, "limit": self.limit,
                "documents": [d.to_dict() for d in self.documents]}


def load_collection_request(path: Path) -> CollectionRequest:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return CollectionRequest.from_dict(json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_keys))
