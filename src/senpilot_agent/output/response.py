"""Factual, deterministic email text."""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parseaddr

from senpilot_agent.models import CATEGORIES, MatterResult


def _sender_name(sender: str) -> str:
    """Display name from a From header. An address alone is not a name."""
    if not isinstance(sender, str):
        return ""
    name = " ".join(parseaddr(sender)[0].split())
    if (
        not name
        or len(name) > 80
        or "@" in name
        or not any(character.isalpha() for character in name)
    ):
        return ""
    return name


def _greeting(sender: str) -> str:
    name = _sender_name(sender)
    return "Hi {},".format(name) if name else "Hi,"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural if plural is not None else singular + "s")
    return "{} {}".format(count, word)


def _display_date(value: str) -> str:
    """Show a site MM/DD/YYYY value as 'June 26, 2025'. Leave other text unchanged."""
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value.strip())
    if match is None:
        return value
    month, day, year = (int(part) for part in match.groups())
    try:
        parsed = datetime(year, month, day)
    except ValueError:
        return value
    return "{} {}, {}".format(parsed.strftime("%B"), parsed.day, parsed.year)


def _title_parts(title: str) -> tuple[str, str]:
    """First line is the heading. Later lines are the description."""
    if not isinstance(title, str):
        return "", ""
    lines = [line.strip() for line in title.splitlines() if line.strip()]
    if not lines:
        return "", ""
    return lines[0], "\n".join(lines[1:])


def _listing_summary(result: MatterResult) -> str:
    """State the tab total in documents once the saved lists have been deduplicated."""
    listings = result.listing_total
    documents = result.document_total
    if documents is None:
        return (
            "These five tabs show {}. The same document can appear on more than one tab, "
            "so this is not a count of unique files."
        ).format(_plural(listings, "listing"))
    if listings == documents:
        return "These five tabs show {}.".format(_plural(documents, "document"))
    return "These five tabs show {} of {}.".format(
        _plural(listings, "listing"), _plural(documents, "document"),
    )


def _document_total(result: MatterResult) -> str:
    total = result.document_total
    if total is None:
        return "Total documents in this matter: unavailable (saved-list inventory was not recorded)."
    return (
        "Total documents in this matter: {}. Counted from the saved lists, with repeated "
        "document numbers counted once, including confidential records. "
        "A listed document may contain multiple files."
    ).format(total)


def _attachment_line(result: MatterResult, attached_count: int) -> str:
    category = result.requested_category
    downloaded = len(result.successful_documents)
    listed = result.category_counts[category]
    files = _plural(attached_count, "file")
    if attached_count == downloaded:
        if attached_count == listed:
            return "The ZIP contains {} from {}.".format(files, category)
        return "The ZIP contains {} from {} ({} on that tab).".format(
            files, category, _plural(listed, "listing"),
        )
    source = "{} in {}".format(_plural(downloaded, "listing"), category)
    if downloaded == listed:
        return "The ZIP contains {} from {}.".format(files, source)
    return "The ZIP contains {} from {} ({} on that tab).".format(
        files, source, _plural(listed, "listing"),
    )


def format_result(
    result: MatterResult,
    attached_count: int,
    sender: str = "",
    attachment_line: str | None = None,
) -> str:
    headline, description = _title_parts(result.title)
    detail = [
        _greeting(sender),
        "",
        "Here are the results for {}.".format(result.matter_number),
        "",
        result.matter_number,
    ]
    if headline:
        detail.append(headline)
    if description:
        detail.extend(["", description])
    detail.extend([
        "",
        "Status: {}".format(result.status),
        "Type: {}".format(result.matter_type),
        "Category: {}".format(result.category),
        "Outcome: {}".format(result.outcome),
        "Date received: {}".format(_display_date(result.date_received)),
        "{}: {}".format(result.second_date_label, _display_date(result.second_date)),
        "",
        _document_total(result),
        "",
        "Listings",
    ])
    for name in CATEGORIES:
        requested = " (requested)" if name == result.requested_category else ""
        detail.append("{}: {}{}".format(name, result.category_counts[name], requested))
    detail.extend([
        "",
        _listing_summary(result),
        "",
        attachment_line if attachment_line is not None else _attachment_line(result, attached_count),
    ])
    failed = [item for item in result.documents if item.status == "failed"]
    if failed:
        detail.append(
            "{} selected document{} could not be downloaded.".format(
                len(failed), "" if len(failed) == 1 else "s"
            )
        )
    size_skipped = sum(
        item.error == "Attachment size budget exceeded" for item in result.documents
    )
    if size_skipped:
        detail.append(
            "I skipped {} larger file{} to keep the ZIP within the email attachment limit.".format(
                size_skipped, "" if size_skipped == 1 else "s"
            )
        )
    detail.extend(["", "Best,", "Regulatory Agent"])
    return "\n".join(detail)


def format_empty(result: MatterResult, sender: str = "") -> str:
    count = result.category_counts[result.requested_category]
    if count == 0:
        line = "This matter lists no {}. The ZIP is empty.".format(result.requested_category)
    else:
        line = "No files could be attached from the selected {} {}. The ZIP is empty.".format(
            result.requested_category, "listing" if count == 1 else "listings",
        )
    return format_result(result, 0, sender, attachment_line=line)


def format_not_found(matter_number: str, sender: str = "") -> str:
    return ("{}\n\nMatter {} was not found in the UARB records. "
            "Please check the matter number and try again.\n\nBest,\nRegulatory Agent").format(
                _greeting(sender), matter_number)


def format_failure(job: dict) -> str:
    """Explain a terminal outcome without leaking exception or credential details."""
    if job.get("send_started_at"):
        explanation = ("I could not confirm delivery of the response to your request. "
                       "It may already have arrived; please check this thread before resubmitting.")
    else:
        explanation = ("I could not complete your document request after processing stopped. "
                       "Please try again later or contact the agent operator with the reference below.")
    selection = ""
    if job.get("matter_number"):
        selection = "Matter: {}. Document type: {}.\n\n".format(
            job["matter_number"], job.get("requested_category") or "unspecified",
        )
    return "{}\n\n{}{}\n\nRequest reference: {}\n\nBest,\nRegulatory Agent".format(
        _greeting(job.get("sender") or ""), selection, explanation, job["id"])


def format_clarification(message: str, sender: str = "") -> str:
    return "{}\n\n{}\n\nBest,\nRegulatory Agent".format(_greeting(sender), message)
