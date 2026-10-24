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
        "I attached {} of the {} {} as a ZIP.".format(
            attached_count,
            result.category_counts[result.requested_category],
            result.requested_category,
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
        "I attached 0 of the {} {} as a ZIP.".format(
            result.category_counts[result.requested_category],
            result.requested_category,
        ),
        "No {} were available to attach. The ZIP is empty.".format(
            result.requested_category
        ),
    )
