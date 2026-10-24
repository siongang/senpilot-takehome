"""Playwright adapter for Nova Scotia UARB FileMaker WebDirect."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

from senpilot_agent.models import CATEGORIES, Document, MatterResult


UARB_URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"
TAB_PATTERN = r"^{}\s*-\s*(\d+)$"
# Base64 adds about one third to the ZIP when AgentMail receives it inline.
MAX_SAVED_BYTES = 4_000_000


class MatterNotFound(RuntimeError):
    pass


def _text(page: Page, selector: str) -> str:
    return page.locator(selector).first.inner_text(timeout=10000).strip()


def _text_any(page: Page, *selectors: str) -> str:
    for selector in selectors:
        candidate = page.locator(selector)
        if candidate.count():
            return candidate.first.inner_text(timeout=10000).strip()
    raise ValueError("Missing matter field: " + ", ".join(selectors))


def _row_text(row, selector: str) -> str:
    match = row.locator(selector)
    return match.first.inner_text(timeout=5000).strip() if match.count() else ""


def _row_extension(row) -> str:
    for value in row.locator(".fm-textarea .text").all_inner_texts():
        value = value.strip()
        if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", value):
            return value
    return ""


def _safe_name(value: str) -> str:
    name = Path(value).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "document"


def _unique_path(folder: Path, name: str) -> Path:
    safe_name = _safe_name(name)
    path = folder / safe_name
    suffix = 2
    while path.exists():
        path = folder / "{}-{}{}".format(
            Path(safe_name).stem, suffix, Path(safe_name).suffix
        )
        suffix += 1
    return path


def _read_counts(page: Page) -> Dict[str, int]:
    counts = {}
    for category in CATEGORIES:
        tab = page.get_by_role(
            "button", name=re.compile(TAB_PATTERN.format(re.escape(category)))
        )
        label = tab.first.inner_text(timeout=10000).strip()
        match = re.match(TAB_PATTERN.format(re.escape(category)), label)
        if match is None:
            raise ValueError("Could not read count for " + category)
        counts[category] = int(match.group(1))
    return counts


def _read_header(page: Page, matter_number: str, category: str) -> MatterResult:
    found = _text_any(page, ".fm_object_286 .text", ".fm_object_219 .text").upper()
    if found != matter_number:
        raise MatterNotFound(
            "Requested {}, but the site displayed {}".format(matter_number, found)
        )
    second_date_label = (
        "Decision Date"
        if page.get_by_text("Decision Date", exact=True).count()
        else "Date Final Submissions"
    )
    return MatterResult(
        matter_number=found,
        requested_category=category,
        title=_text_any(page, '[id^="b0p0o290"]', '[id^="b0p0o223"]'),
        status=_text_any(page, ".fm_object_289 .text", ".fm_object_222 .text"),
        matter_type=_text_any(page, ".fm_object_298 .text", ".fm_object_231 .text"),
        category=_text_any(page, ".fm_object_287 .text", ".fm_object_220 .text"),
        date_received=_text_any(page, ".fm_object_292 .text", ".fm_object_225 .text"),
        second_date_label=second_date_label,
        second_date=_text_any(page, ".fm_object_294 .text", ".fm_object_227 .text"),
        outcome=_text_any(page, ".fm_object_295 .text", ".fm_object_228 .text"),
        category_counts=_read_counts(page),
    )


def _visible_documents(page: Page, start_position: int) -> List[Document]:
    documents = []
    rows = page.locator("tr.v-grid-row[role='row']")
    for index in range(rows.count()):
        row = rows.nth(index)
        if row.get_by_role("button", name="GO GET IT").count() == 0:
            continue
        source_id = _row_text(row, ".fm_object_201 .text")
        if not source_id:
            source_id = _row_text(row, ".fm_object_268 .text")
        if not source_id:
            source_id = _row_text(row, ".fm_object_300 .text")
        if not source_id:
            continue
        documents.append(
            Document(
                source_id=source_id,
                title=_row_text(row, ".fm_object_202 .text"),
                filed_date=_row_text(row, ".fm_object_203 .text"),
                security=_row_text(row, ".fm_object_204 .text"),
                extension=_row_extension(row),
                display_position=start_position + index,
            )
        )
    return documents


def _download_row(page: Page, document: Document, folder: Path) -> None:
    row = page.locator("tr.v-grid-row[role='row']").filter(
        has=page.get_by_text(document.source_id, exact=True)
    ).first
    row.get_by_role("button", name="GO GET IT").click(timeout=10000)
    try:
        controls = page.locator('[role="button"].fm-download-button:visible')
        controls.first.wait_for(state="visible", timeout=20000)
        for index in range(controls.count()):
            control = controls.nth(index)
            offered_name = control.inner_text().strip()
            if not offered_name:
                continue
            with page.expect_download(timeout=60000) as event:
                control.click(timeout=10000)
            download = event.value
            if download.failure():
                raise RuntimeError(
                    "Download failed for {}: {}".format(
                        document.source_id, download.failure()
                    )
                )
            filename = download.suggested_filename or offered_name
            destination = _unique_path(folder, filename)
            download.save_as(destination)
            if destination.stat().st_size == 0:
                destination.unlink(missing_ok=True)
                raise RuntimeError("Downloaded file is empty: " + filename)
            if destination.suffix.lower() == ".pdf":
                with destination.open("rb") as handle:
                    if handle.read(5) != b"%PDF-":
                        destination.unlink(missing_ok=True)
                        raise RuntimeError("Downloaded file is not a PDF: " + filename)
            document.filenames.append(destination.name)
            document.saved_paths.append(str(destination))
    finally:
        close = page.get_by_role("button", name="Close", exact=True)
        if close.count():
            close.last.click(timeout=10000)
        else:
            page.get_by_text("Close", exact=True).last.click(timeout=10000)
    document.status = "downloaded" if document.saved_paths else "failed"


def scrape_matter(
    matter_number: str,
    requested_category: str,
    output_dir: Path,
    *,
    max_documents: int = 10,
    headless: bool = True,
) -> MatterResult:
    """Search one matter, save up to ten Public document rows, and return facts."""
    matter_number = matter_number.upper()
    if not re.fullmatch(r"M\d{5}", matter_number):
        raise ValueError("Matter number must look like M12345")
    if requested_category not in CATEGORIES:
        raise ValueError("Unsupported document type: " + requested_category)
    if not 1 <= max_documents <= 10:
        raise ValueError("max_documents must be between 1 and 10")
    output_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(
            accept_downloads=True, viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        page.set_default_timeout(15000)
        try:
            page.goto(UARB_URL, wait_until="domcontentloaded", timeout=60000)
            field = page.locator(".fm_object_254 .text")
            field.wait_for(state="visible", timeout=60000)
            field.click()
            page.locator('.fm_object_254 .text[contenteditable="true"]').wait_for(
                state="visible", timeout=10000
            )
            # WebDirect reads keyboard/blur events; a bulk fill displays text but
            # can submit an empty search value and show "No Records Found".
            field.press_sequentially(matter_number, delay=70)
            field.press("Tab")
            # Some WebDirect sessions submit on blur; in others the Search
            # control stays visible. Give the automatic transition a chance.
            try:
                page.get_by_role("button", name=re.compile(r"^Exhibits\s*-\s*\d+$")).wait_for(
                    state="visible", timeout=5000
                )
            except PlaywrightTimeout:
                search = page.locator(".fm_object_258 button")
                if search.count():
                    search.click(timeout=10000)
            try:
                page.get_by_role("button", name=re.compile(r"^Exhibits\s*-\s*\d+$")).wait_for(
                    state="visible", timeout=30000
                )
            except PlaywrightTimeout as error:
                raise MatterNotFound(
                    "Matter {} did not open in the UARB site. Page said: {}".format(
                        matter_number, page.locator("body").inner_text()[:350]
                    )
                ) from error
            result = _read_header(page, matter_number, requested_category)
            if result.category_counts[requested_category] == 0:
                return result
            page.get_by_role(
                "button",
                name=re.compile(
                    TAB_PATTERN.format(re.escape(requested_category))
                ),
            ).click()
            rows = page.locator("tr.v-grid-row[role='row']").filter(
                has=page.get_by_role("button", name="GO GET IT")
            )
            rows.first.wait_for(state="visible", timeout=30000)
            seen = set()
            stagnant_scrolls = 0
            while len(result.successful_documents) < max_documents:
                visible = _visible_documents(page, len(seen) + 1)
                fresh = [document for document in visible if document.source_id not in seen]
                for document in fresh:
                    seen.add(document.source_id)
                    result.documents.append(document)
                    if document.security.lower() != "public":
                        document.status = "skipped"
                        document.error = "Security label: " + document.security
                        continue
                    try:
                        _download_row(page, document, output_dir)
                        saved_bytes = sum(
                            Path(path).stat().st_size
                            for path in result.saved_files
                        )
                        if saved_bytes > MAX_SAVED_BYTES:
                            for path in document.saved_paths:
                                Path(path).unlink(missing_ok=True)
                            document.saved_paths.clear()
                            document.filenames.clear()
                            document.status = "skipped"
                            document.error = "Attachment size budget exceeded"
                    except Exception as error:
                        document.status = "failed"
                        document.error = str(error)
                    if len(result.successful_documents) >= max_documents:
                        break
                if len(result.successful_documents) >= max_documents:
                    break
                if len(seen) >= result.category_counts[requested_category]:
                    break
                if not fresh:
                    stagnant_scrolls += 1
                    if stagnant_scrolls >= 3:
                        break
                else:
                    stagnant_scrolls = 0
                mounted = page.locator("tr.v-grid-row[role='row']")
                if mounted.count() == 0:
                    break
                mounted.last.hover()
                page.mouse.wheel(0, 650)
                page.wait_for_timeout(350)
            return result
        finally:
            context.close()
            browser.close()
