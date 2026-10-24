"""Deterministic parsing for one matter and one document type."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from senpilot_agent.models import CATEGORIES


MATTER_PATTERN = re.compile(r"(?<![A-Z0-9])M\d{5}(?!\d)", re.IGNORECASE)
CATEGORY_PATTERNS = {
    "Exhibits": re.compile(r"\bexhibits?\b", re.IGNORECASE),
    "Key Documents": re.compile(r"\bkey\s+documents?\b", re.IGNORECASE),
    "Other Documents": re.compile(r"\bother\s+documents?\b", re.IGNORECASE),
    "Transcripts": re.compile(r"\btranscripts?\b", re.IGNORECASE),
    "Recordings": re.compile(r"\brecordings?\b", re.IGNORECASE),
}


@dataclass(frozen=True)
class ParsedRequest:
    matter_number: Optional[str]
    document_type: Optional[str]
    error: Optional[str] = None

    @property
    def valid(self) -> bool:
        return self.error is None


def parse_request(subject: str, body: str) -> ParsedRequest:
    text = (subject or "") + "\n" + (body or "")
    matters = sorted({match.group().upper() for match in MATTER_PATTERN.finditer(text)})
    categories = [
        category
        for category in CATEGORIES
        if CATEGORY_PATTERNS[category].search(text)
    ]
    if len(matters) != 1 and len(categories) != 1:
        return ParsedRequest(None, None, "Please specify one matter number (M plus five digits) and one document type: " + ", ".join(CATEGORIES) + ".")
    if len(matters) != 1:
        return ParsedRequest(None, categories[0] if categories else None, "Please specify exactly one matter number in the form M12345.")
    if len(categories) != 1:
        return ParsedRequest(matters[0], None, "Please specify exactly one document type: " + ", ".join(CATEGORIES) + ".")
    return ParsedRequest(matters[0], categories[0])
