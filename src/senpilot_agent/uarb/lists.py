"""Read document numbers from UARB's Save List PDF reports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

from pypdf import PdfReader


REPORT_TITLES = {
    "Exhibits": r"Exhibits? List",
    "Key Documents": r"Key Documents? List",
    "Other Documents": r"Other Documents? List",
    "Transcripts": r"Transcripts? List",
    "Recordings": r"Recordings? List",
}
TABLE_HEADER = re.compile(
    r"^\s*(?:Doc(?:ument)?|Exhibit|Transcript|Recording)\s+(?:No\.?|Number|#)"
    r"\s{2,}(?P<description>Description|Title)\b", re.IGNORECASE,
)
# Exhibit identifiers are local labels such as H-4(C)-iii; other reports use
# numeric document numbers. Dates and page numbers must never become records.
DOCUMENT_NUMBER = re.compile(r"(?:\d+|[A-Z][A-Z0-9()./-]*-\d[A-Z0-9()./-]*)", re.IGNORECASE)


def read_list_pdf(path: Path, matter_number: str, category: str, expected_count: int) -> List[str]:
    """Validate the report identity and full row count before returning its IDs.

    Layout extraction keeps wrapped descriptions out of the document-number
    column. Neither page footers nor numbers within a description are records.
    These PDFs inventory document records, not each record's attached binaries.
    """
    reader = PdfReader(path)
    if not reader.pages:
        raise ValueError("Saved list PDF has no pages")
    identifiers = []
    for page in reader.pages:
        text = page.extract_text(extraction_mode="layout")
        normalized = " ".join(text.split())
        if not re.search(r"\b" + re.escape(matter_number) + r"\b", normalized, re.IGNORECASE):
            raise ValueError("Saved list PDF belongs to a different matter")
        if not re.search(r"\b" + REPORT_TITLES[category] + r"\b", normalized, re.IGNORECASE):
            raise ValueError("Saved list PDF belongs to a different document category")
        description_column = None
        for line in text.splitlines():
            header = TABLE_HEADER.match(line)
            if header:
                description_column = header.start("description")
                continue
            if description_column is None:
                continue
            source_id = line[:description_column].strip()
            if DOCUMENT_NUMBER.fullmatch(source_id) and line[description_column:].strip():
                identifiers.append(source_id.upper())
        if description_column is None:
            raise ValueError("Saved list PDF has no recognizable document table")
    if len(identifiers) != expected_count:
        raise ValueError("Saved {} list has {} records; the tab reports {}".format(
            category, len(identifiers), expected_count,
        ))
    return identifiers
