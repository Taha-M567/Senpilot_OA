"""One-shot email-text requests and the final model-to-download boundary."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .collection import CATEGORIES, CollectionRequest, _fields


class RequestFailure(ValueError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def read_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


@dataclass(frozen=True)
class EmailRequest:
    subject: str
    body_text: str

    def __post_init__(self):
        if not isinstance(self.subject, str) or not isinstance(self.body_text, str):
            raise ValueError("subject and body_text must be strings")

    @classmethod
    def from_dict(cls, value: dict):
        _fields(value, {"subject", "body_text"})
        return cls(**value)


def extract_matter_number(subject: str, body_text: str) -> str:
    # Unicode word boundaries also reject a valid-looking suffix in a larger token.
    matters = {m.upper() for text in (subject, body_text)
               for m in re.findall(r"(?<!\w)[Mm][0-9]{5}(?!\w)", text)}
    if len(matters) != 1:
        raise RequestFailure("invalid_request", "Submit a new request containing exactly one valid matter number, for example M12205.")
    return matters.pop()


def validate_selection(value: dict, catalog: dict) -> CollectionRequest | None:
    """Validate references and structure, without reinterpreting or reranking text."""
    _fields(value, {"status", "matter_number", "categories", "category_basis",
                    "assumptions", "limit", "ranked_documents", "message"})
    if value["status"] not in {"ready", "insufficient_detail", "no_matches"}:
        raise ValueError("Invalid selection status")
    if value["matter_number"] != catalog["matter_number"]:
        raise ValueError("Selection matter does not match the verified catalog")
    categories = value["categories"]
    if (not isinstance(categories, list) or any(not isinstance(c, str) or c not in CATEGORIES for c in categories)
            or len(categories) != len(set(categories))):
        raise ValueError("Invalid or duplicate categories")
    basis = value["category_basis"]
    if basis not in {"explicit", "inferred", "default_overview"}:
        raise ValueError("Invalid category basis")
    assumptions = value["assumptions"]
    if not isinstance(assumptions, list) or any(not isinstance(a, str) or not a.strip() for a in assumptions):
        raise ValueError("Invalid assumptions")
    if basis != "explicit" and not assumptions:
        raise ValueError("Inferred/default scope requires a disclosed assumption")
    if basis == "default_overview" and any(c in {"Transcripts", "Recordings"} for c in categories):
        raise ValueError("Default overview cannot include transcripts or recordings")
    limit = value["limit"]
    ranked = value["ranked_documents"]
    if type(limit) is not int or not 1 <= limit <= 10 or not isinstance(ranked, list) or len(ranked) > limit:
        raise ValueError("Selection limit must be 1..10 and bound all selections")
    if value["status"] != "ready":
        if ranked or not isinstance(value["message"], str) or not value["message"].strip():
            raise ValueError("Terminal non-ready selections require a message and no documents")
        return None
    if not ranked or not categories or value["message"] is not None:
        raise ValueError("Ready selections require documents/categories and a null message")
    candidates = {c["candidate_id"]: c for c in catalog["documents"]}
    seen = set()
    documents = []
    for priority, entry in enumerate(ranked, 1):
        _fields(entry, {"candidate_id", "reason", "matched_text"})
        identifier = entry["candidate_id"]
        if not isinstance(identifier, str) or identifier in seen or identifier not in candidates:
            raise ValueError("Unknown or duplicate candidate ID")
        seen.add(identifier)
        candidate = candidates[identifier]
        if not candidate["eligible"] or candidate["category"] not in categories:
            raise ValueError("Selected candidate is ineligible or outside declared scope")
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise ValueError("Each selection requires a reason")
        evidence = entry["matched_text"]
        metadata = [candidate.get(k) for k in ("title", "document_number", "hearing_date", "date", "category")]
        metadata.extend(candidate.get("witnesses", []))
        if (not isinstance(evidence, str) or not evidence.strip()
                or not any(isinstance(text, str) and evidence in text for text in metadata)):
            raise ValueError("Matching evidence is absent from the selected listing metadata")
        documents.append({"category": candidate["category"], "priority": priority,
                          **candidate["download_reference"]})
    # This also rejects canonical duplicates across categories and invalid native refs.
    return CollectionRequest.from_dict({"matter_number": catalog["matter_number"],
                                        "limit": limit, "documents": documents})
