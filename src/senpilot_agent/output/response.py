"""Factual, deterministic email text."""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parseaddr

from senpilot_agent.models import CATEGORIES, MatterResult, split_categories


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
    return lines[0], " ".join(lines[1:])


def _requested_names(result: MatterResult) -> list[str]:
    try:
        return list(split_categories(result.requested_category))
    except ValueError:
        return []


def _document_total(result: MatterResult) -> str:
    total = result.document_total
    if total is None:
        return "Documents: unavailable."
    return "Documents: {}.".format(total)


def _category_files(result: MatterResult, category: str) -> int:
    return sum(
        len(document.saved_paths)
        for document in result.successful_documents
        if document.category == category
    )


def _attachment_line(result: MatterResult, attached_count: int) -> str:
    categories = _requested_names(result)
    if len(categories) > 1:
        parts = []
        for category in categories:
            files = _category_files(result, category)
            if not files:
                continue
            listed = result.category_counts[category]
            if files == listed:
                parts.append("{} from {}".format(_plural(files, "file"), category))
            else:
                parts.append("{} from {} ({} on that tab)".format(
                    _plural(files, "file"), category, _plural(listed, "listing"),
                ))
        if not parts:
            return "The ZIP is empty."
        return "The ZIP contains {}.".format(", ".join(parts))
    category = categories[0] if categories else result.requested_category
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
    ask_for_documents: bool = True,
) -> str:
    headline, description = _title_parts(result.title)
    requested = set(_requested_names(result))
    detail = [
        _greeting(sender),
        "",
        "Matter number: {}".format(result.matter_number),
        "Title: {}".format(headline),
    ]
    if description:
        detail.append("Description: {}".format(description))
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
    ])
    for name in CATEGORIES:
        marker = " (requested)" if name in requested else ""
        detail.append("{}: {}{}".format(name, result.category_counts[name], marker))
    if not requested and ask_for_documents:
        detail.extend(["", (
            "I have not retrieved any files. Would you like me to retrieve any document types for {}? "
            "Reply with the matter number and one or more of: {}."
        ).format(result.matter_number, ", ".join(CATEGORIES))])
    line = attachment_line if attachment_line is not None else _attachment_line(result, attached_count)
    if line:
        detail.extend(["", line])
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
    names = _requested_names(result)
    if not names:
        return format_result(result, 0, sender, attachment_line="")
    if all(result.category_counts.get(name, 0) == 0 for name in names):
        if len(names) == 1:
            listed = names[0]
        else:
            listed = ", ".join(names[:-1]) + " or " + names[-1]
        line = "This matter lists no {}. The ZIP is empty.".format(listed)
    elif len(names) == 1:
        count = result.category_counts[names[0]]
        line = "No files could be attached from the selected {} {}. The ZIP is empty.".format(
            names[0], "listing" if count == 1 else "listings",
        )
    else:
        line = "No files could be attached from the selected document types. The ZIP is empty."
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
