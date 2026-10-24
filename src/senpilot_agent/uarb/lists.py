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
    "Recordings": r"(?:Recordings? List|List of Sound Files)",
}
TABLE_HEADER = re.compile(
    r"^\s*(?:Doc(?:ument)?|Exhibit|Transcript|Recording|File)\s+(?:No\.?|Number|#)"
    r"\s{2,}(?P<description>Description|Title)\b", re.IGNORECASE,
)
# Exhibit identifiers include H-4(C)-iii; recordings use SF numbers, and
# document reports use numeric IDs. Dates and page numbers are not records.
DOCUMENT_NUMBER = re.compile(r"(?:\d+|SF\d+|[A-Z][A-Z0-9()./-]*-\d[A-Z0-9()./-]*)", re.IGNORECASE)


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
            # Layout extraction can place the first description character one
            # column before its header, so separate the ID at the whitespace.
            row = re.match(r"^\s*(\S+)\s{2,}", line[:description_column])
            if row and DOCUMENT_NUMBER.fullmatch(row.group(1)) and line[description_column:].strip():
                identifiers.append(row.group(1).upper())
        if description_column is None:
            raise ValueError("Saved list PDF has no recognizable document table")
    if len(identifiers) != expected_count:
        raise ValueError("Saved {} list has {} records; the tab reports {}".format(
            category, len(identifiers), expected_count,
        ))
    return identifiers
