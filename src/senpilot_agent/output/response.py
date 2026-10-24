"""Factual, deterministic email text."""

from __future__ import annotations

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


def format_result(result: MatterResult, attached_count: int, sender: str = "") -> str:
    counts = ", ".join(
        "{} {}".format(result.category_counts[name], name) for name in CATEGORIES
    )
    detail = [
        _greeting(sender),
        "",
        "{}: {}".format(result.matter_number, result.title),
        "Status: {}. Type: {}. Category: {}. Outcome: {}.".format(
            result.status, result.matter_type, result.category, result.outcome
        ),
        "Date received: {}. {}: {}.".format(
            result.date_received, result.second_date_label, result.second_date
        ),
        "The matter shows {} category listings across five tabs: {}.".format(
            result.listing_total, counts
        ),
        "I attached {} files from {} downloaded {} listings as a ZIP ({} listings in this category).".format(
            attached_count, len(result.successful_documents), result.requested_category,
            result.category_counts[result.requested_category],
        ),
    ]
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
    body = format_result(result, 0, sender)
    return body.replace(
        "I attached 0 files from {} downloaded {} listings as a ZIP ({} listings in this category).".format(
            len(result.successful_documents), result.requested_category,
            result.category_counts[result.requested_category],
        ),
        ("This matter lists no {}. The ZIP is empty.".format(result.requested_category)
         if result.category_counts[result.requested_category] == 0
         else "No files could be attached from the selected {} listings. The ZIP is empty.".format(result.requested_category)),
    )


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
