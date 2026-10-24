"""Factual, deterministic email text."""

from __future__ import annotations

from senpilot_agent.models import CATEGORIES, MatterResult


def format_result(result: MatterResult, attached_count: int) -> str:
    counts = ", ".join(
        "{} {}".format(result.category_counts[name], name) for name in CATEGORIES
    )
    detail = [
        "Hi,",
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


def format_empty(result: MatterResult) -> str:
    body = format_result(result, 0)
    return body.replace(
        "I attached 0 files from {} downloaded {} listings as a ZIP ({} listings in this category).".format(
            len(result.successful_documents), result.requested_category,
            result.category_counts[result.requested_category],
        ),
        ("This matter lists no {}. The ZIP is empty.".format(result.requested_category)
         if result.category_counts[result.requested_category] == 0
         else "No files could be attached from the selected {} listings. The ZIP is empty.".format(result.requested_category)),
    )


def format_failure(job: dict) -> str:
    """Explain a terminal outcome without leaking exception or credential details."""
    if job.get("send_started_at"):
        explanation = ("I could not confirm delivery of the response to your request. "
                       "It may already have arrived; please check this thread before resubmitting.")
    else:
        explanation = ("I could not complete your document request after processing stopped. "
                       "Please try again later or contact the agent operator with the reference below.")
    return "Hi,\n\n{}\n\nRequest reference: {}\n\nBest,\nRegulatory Agent".format(explanation, job["id"])
