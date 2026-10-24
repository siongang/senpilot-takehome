"""Structured records shared by the browser, queue, and response builder."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


CATEGORIES = (
    "Exhibits",
    "Key Documents",
    "Other Documents",
    "Transcripts",
    "Recordings",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Document:
    source_id: str
    title: str
    filed_date: str
    security: str
    extension: str
    display_position: int
    status: str = "pending"
    filenames: List[str] = field(default_factory=list)
    saved_paths: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class MatterResult:
    matter_number: str
    requested_category: str
    title: str
    status: str
    matter_type: str
    category: str
    date_received: str
    second_date_label: str
    second_date: str
    outcome: str
    category_counts: Dict[str, int]
    documents: List[Document] = field(default_factory=list)
    scraped_at: str = field(default_factory=utc_now)
    source_url: str = "https://uarb.novascotia.ca/fmi/webd/UARB15"

    @property
    def listing_total(self) -> int:
        return sum(self.category_counts.values())

    @property
    def successful_documents(self) -> List[Document]:
        return [document for document in self.documents if document.status == "downloaded"]

    @property
    def saved_files(self) -> List[str]:
        return [
            path
            for document in self.successful_documents
            for path in document.saved_paths
        ]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "MatterResult":
        copy = dict(value)
        copy["documents"] = [Document(**item) for item in copy.get("documents", [])]
        return cls(**copy)
