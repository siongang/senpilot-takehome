"""Playwright adapter for Nova Scotia UARB FileMaker WebDirect."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, TypeVar

from playwright.sync_api import (
    Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeout, sync_playwright,
)

from senpilot_agent.models import CATEGORIES, Document, MatterResult, split_categories
from senpilot_agent.uarb.lists import read_list_pdf


UARB_URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"
TAB_PATTERN = r"^{}\s*-\s*(\d+)$"
# Base64 adds about one third to the ZIP when AgentMail receives it inline.
MAX_SAVED_BYTES = 4_000_000


class MatterNotFound(RuntimeError):
    pass


class DownloadLimitExceeded(RuntimeError):
    """A requested file cannot fit within the email attachment budget."""

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
        raise RuntimeError(
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


def _visible_documents(
    page: Page, start_position: int, category: str = "",
    source_ids: List[str] | None = None,
    recording_ids_by_row: Dict[tuple[str, str], str] | None = None,
) -> List[Document]:
    documents = []
    recording_ids_by_row = recording_ids_by_row if recording_ids_by_row is not None else {}
    snapshot_keys = set()
    rows = page.locator("tr.v-grid-row[role='row']")
    for index in range(rows.count()):
        row = rows.nth(index)
        if row.get_by_role("button", name="GO GET IT").count() == 0:
            continue
        if category == "Recordings":
            # The sound-file grid has no SF number; recognize rows across
            # overlapping virtual-grid windows before assigning the next ID.
            title = _row_text(row, ".fm_object_202 .text")
            filed_date = _row_text(row, ".fm_object_201 .text")
            key = (title.casefold(), filed_date)
            if not title or key in snapshot_keys:
                raise DownloadError("Recording rows cannot be uniquely identified")
            snapshot_keys.add(key)
            if key not in recording_ids_by_row:
                position = len(recording_ids_by_row)
                if source_ids is None or position >= len(source_ids):
                    raise DownloadError("Recording row has no matching Save List ID")
                recording_ids_by_row[key] = source_ids[position]
            source_id = recording_ids_by_row[key]
            if source_ids is None or source_id not in source_ids:
                raise DownloadError("Recording row has no matching Save List ID")
            display_position = source_ids.index(source_id) + 1
        else:
            source_id = _row_text(row, ".fm_object_201 .text")
            if not source_id:
                source_id = _row_text(row, ".fm_object_268 .text")
            if not source_id:
                source_id = _row_text(row, ".fm_object_300 .text")
            title = _row_text(row, ".fm_object_202 .text")
            filed_date = _row_text(row, ".fm_object_203 .text")
            display_position = start_position + index
        if not source_id:
            continue
        documents.append(
            Document(
                source_id=source_id,
                title=title,
                filed_date=filed_date,
                security=_row_text(row, ".fm_object_204 .text"),
                extension=_row_extension(row),
                display_position=display_position,
            )
        )
    return documents


class DownloadError(RuntimeError):
    """The site offered a file but it could not be saved and validated."""


T = TypeVar("T")


def _retry(operation: Callable[[], T], attempts: int = 2) -> T:
    """Retry browser/file transfer failures; programming errors propagate."""
    for attempt in range(attempts):
        try:
            return operation()
        except (PlaywrightError, DownloadError):
            if attempt + 1 == attempts:
                raise
    raise ValueError("attempts must be positive")


def _capture_failure(page: Page, folder: Path, label: str, error: Exception) -> None:
    """Diagnostics must never hide the original error."""
    try:
        diagnostics = folder / "diagnostics"
        diagnostics.mkdir(parents=True, exist_ok=True)
        stem = _unique_path(diagnostics, _safe_name(label) + ".txt")
        stem.write_text(str(error), encoding="utf-8")
        try:
            page.screenshot(path=str(stem.with_suffix(".png")), timeout=5000)
        except Exception:
            pass
        try:
            stem.with_suffix(".html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
    except Exception:
        pass


def _close_download_dialog(page: Page) -> None:
    # Hidden WebDirect controls can remain in the DOM after a dialog closes.
    close = page.get_by_role("button", name="Close", exact=True)
    for index in range(close.count() - 1, -1, -1):
        candidate = close.nth(index)
        if candidate.is_visible():
            candidate.click(timeout=10000)
            candidate.wait_for(state="hidden", timeout=10000)
            return
    close = page.get_by_text("Close", exact=True)
    for index in range(close.count() - 1, -1, -1):
        candidate = close.nth(index)
        if candidate.is_visible():
            candidate.click(timeout=10000)
            candidate.wait_for(state="hidden", timeout=10000)
            return
    if page.locator('[role="button"].fm-download-button:visible').count():
        raise DownloadError("Download dialog remained open without a Close control")


def _close_export_dialog(page: Page) -> None:
    dialog = page.get_by_role("dialog", name="Export Field to File")
    if dialog.is_visible():
        dialog.get_by_role("button", name="Cancel", exact=True).click(timeout=10000)
        dialog.wait_for(state="hidden", timeout=10000)


def _validate_file(destination: Path) -> None:
    if destination.stat().st_size == 0:
        raise DownloadError("Downloaded file is empty: " + destination.name)
    if destination.suffix.lower() == ".pdf":
        with destination.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise DownloadError("Downloaded file is not a PDF: " + destination.name)


def _download_row(page: Page, document: Document, folder: Path, *, max_files: int = 10) -> None:
    """Save a row atomically: failures remove every file from this attempt."""
    folder.mkdir(parents=True, exist_ok=True)
    saved: List[Path] = []
    try:
        rows = page.locator("tr.v-grid-row[role='row']")
        if document.category == "Recordings":
            rows = rows.filter(has=page.get_by_text(document.title, exact=True)).filter(
                has=page.get_by_text(document.filed_date, exact=True)
            )
            if rows.count() != 1:
                raise DownloadError("Could not uniquely locate recording " + document.source_id)
        else:
            rows = rows.filter(has=page.get_by_text(document.source_id, exact=True))
        row = rows.first
        row.get_by_role("button", name="GO GET IT").click(timeout=10000)
        if document.category == "Recordings":
            dialog = page.get_by_role("dialog", name="Export Field to File")
            dialog.wait_for(state="visible", timeout=20000)
            if not dialog.locator('input[type="text"]').input_value().strip():
                raise DownloadError("Recording export has no filename for " + document.source_id)
            # OK prepares the file; WebDirect then opens its usual Download Files dialog.
            dialog.get_by_role("button", name="OK", exact=True).click(timeout=10000)
            dialog.wait_for(state="hidden", timeout=10000)
        controls = page.locator('[role="button"].fm-download-button:visible')
        controls.first.wait_for(state="visible", timeout=20000)
        for index in range(controls.count()):
            if len(saved) >= max_files:
                break
            control = controls.nth(index)
            offered_name = control.inner_text().strip()
            if not offered_name:
                continue
            try:
                with page.expect_download(timeout=30000) as event:
                    control.click(timeout=10000)
            except PlaywrightTimeout as error:
                raise DownloadLimitExceeded(
                    "A requested file took too long to start downloading"
                ) from error
            download = event.value
            destination = _unique_path(folder, download.suggested_filename or offered_name)
            saved.append(destination)
            marker = folder / ".active-download"
            marker.write_text(destination.name, encoding="utf-8")
            try:
                # save_as can write while the transfer is in progress. The
                # supervisor watches this path and stops oversized transfers.
                download.save_as(destination)
            finally:
                marker.unlink(missing_ok=True)
            failure = download.failure()
            if failure:
                raise DownloadError("Download failed for {}: {}".format(document.source_id, failure))
            _validate_file(destination)
        if not saved:
            raise DownloadError("No downloadable files for " + document.source_id)
        _close_download_dialog(page)
    except Exception:
        for path in saved:
            path.unlink(missing_ok=True)
        try:
            if document.category == "Recordings":
                _close_export_dialog(page)
            _close_download_dialog(page)
        except Exception:
            pass
        raise
    document.saved_paths = [str(path) for path in saved]
    document.filenames = [path.name for path in saved]
    document.status = "downloaded"
    document.error = None


def _is_visible(locator) -> bool:
    try:
        return locator.first.is_visible() is True
    except Exception:
        return False


def _not_found_dialog(page: Page):
    """Modal shown when Go Directly to Matter matches nothing.

    The title is "No Records Found" and the body says no records matched.
    This is separate from the "No Matching Records" dialog on an empty tab.
    """
    return page.get_by_role("dialog", name=re.compile(r"No Records Found", re.IGNORECASE))


def _not_found_message(page: Page):
    return page.get_by_text(re.compile(r"No records matched your search request", re.IGNORECASE))


def _not_found_title(page: Page):
    return page.get_by_text(re.compile(r"No Records Found", re.IGNORECASE))


def _matter_missing(page: Page, *, body: bool = False) -> bool:
    if any(_is_visible(locator) for locator in (
        _not_found_dialog(page), _not_found_title(page), _not_found_message(page),
    )):
        return True
    if not body:
        return False
    try:
        text = page.locator("body").inner_text(timeout=1000)
    except Exception:
        return False
    return "no records found" in text.lower()


def _raise_if_matter_missing(page: Page, matter_number: str, *, body: bool = False) -> None:
    if _matter_missing(page, body=body):
        raise MatterNotFound("Matter {} was not found".format(matter_number))


def _wait_for_opened_matter(page: Page, matter_number: str, timeout: int) -> None:
    """Return when the matter tabs appear. A not-found dialog ends the wait."""
    _raise_if_matter_missing(page, matter_number)
    exhibits = page.get_by_role("button", name=re.compile(r"^Exhibits\s*-\s*\d+$"))
    opened = exhibits.or_(_not_found_dialog(page)).or_(_not_found_title(page)).or_(_not_found_message(page))
    try:
        opened.first.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeout as error:
        _raise_if_matter_missing(page, matter_number, body=True)
        raise error
    _raise_if_matter_missing(page, matter_number)


def _find_matter(page: Page, matter_number: str) -> None:
    """Reset navigation before each attempt and submit through keyboard events."""
    page.goto(UARB_URL, wait_until="domcontentloaded", timeout=60000)
    field = page.locator(".fm_object_254 .text")
    field.wait_for(state="visible", timeout=60000)
    field.click()
    page.locator('.fm_object_254 .text[contenteditable="true"]').wait_for(
        state="visible", timeout=10000
    )
    # WebDirect reads keyboard/blur events; a bulk fill displays text but
    # can submit an empty search value and show "No Records Found".
    field.press("ControlOrMeta+A")
    field.press_sequentially(matter_number, delay=70)
    field.press("Tab")
    # Some WebDirect sessions submit on blur; in others the Search
    # control stays visible. Give the automatic transition a chance.
    # Watch the not-found dialog during this wait so a missing matter does
    # not sit through the exhibits timeout or a covered Search click.
    try:
        _wait_for_opened_matter(page, matter_number, 5000)
        return
    except MatterNotFound:
        raise
    except PlaywrightTimeout:
        pass
    _raise_if_matter_missing(page, matter_number, body=True)
    search = page.locator(".fm_object_258 button")
    if search.count() and _is_visible(search):
        try:
            search.click(timeout=5000)
        except PlaywrightError as error:
            _raise_if_matter_missing(page, matter_number, body=True)
            raise error
    _wait_for_opened_matter(page, matter_number, 30000)


def _open_category(page: Page, category: str) -> None:
    """Open a nonempty category and wait for its downloadable rows."""
    tab = page.get_by_role(
        "button", name=re.compile(TAB_PATTERN.format(re.escape(category)))
    )
    selected = page.locator(".iwps_button_bar_segment.fm-selected").filter(has=tab)
    # WebDirect puts a persistent glass pane over the selected tab. A retry
    # after Save List is already on that tab, so clicking it can never work.
    if not selected.count():
        tab.click()
    # The old grid remains mounted briefly while WebDirect changes layouts.
    # Wait for the selected tab before using Save List or the download rows.
    selected.first.wait_for(
        state="visible", timeout=30000,
    )
    rows = page.locator("tr.v-grid-row[role='row']").filter(
        has=page.get_by_role("button", name="GO GET IT")
    )
    rows.first.wait_for(state="visible", timeout=30000)


def _download_list(page: Page, category: str, folder: Path) -> Path:
    """Save the report offered by Save List, always closing its download dialog."""
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / (category.lower().replace(" ", "-") + ".pdf")
    try:
        page.get_by_role("button", name="Save List", exact=True).click(timeout=10000)
        controls = page.locator('[role="button"].fm-download-button:visible')
        controls.first.wait_for(state="visible", timeout=20000)
        if controls.count() != 1 or not controls.first.inner_text().strip().lower().endswith(".pdf"):
            raise DownloadError("Save List did not offer exactly one PDF")
        with page.expect_download(timeout=60000) as event:
            controls.first.click(timeout=10000)
        download = event.value
        if download.failure():
            raise DownloadError("Saved list PDF download failed")
        download.save_as(destination)
        _validate_file(destination)
        _close_download_dialog(page)
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        try:
            _close_download_dialog(page)
        except Exception:
            pass
        raise


def _inventory_matter(page: Page, result: MatterResult, folder: Path) -> None:
    """Inventory every nonempty tab independently of the attachment limit."""
    inventory = {}
    paths = {}
    for category in CATEGORIES:
        expected = result.category_counts[category]
        if expected == 0:
            inventory[category] = []
            continue

        def export_category():
            _open_category(page, category)
            path = _download_list(page, category, folder)
            try:
                identifiers = read_list_pdf(path, result.matter_number, category, expected)
            except Exception as error:
                raise DownloadError("Could not verify {} saved list: {}".format(category, error)) from error
            return path, identifiers

        path, identifiers = _retry(export_category)
        inventory[category] = identifiers
        paths[category] = str(path)
    # Publish a total only after all five category inventories are complete.
    result.document_ids_by_category = inventory
    result.list_pdf_paths = paths


def _scroll_documents(page: Page) -> bool:
    mounted = page.locator("tr.v-grid-row[role='row']")
    if mounted.count() == 0:
        return False
    mounted.last.hover()
    page.mouse.wheel(0, 650)
    page.wait_for_timeout(350)
    return True


def _collect_documents(
    page: Page, result: MatterResult, output_dir: Path, limit: int, category: str | None = None,
) -> None:
    """Walk one category's rows in display order, with one result per source ID."""
    category = category or result.requested_category
    seen = set()
    recording_ids_by_row: Dict[tuple[str, str], str] = {}
    stagnant_scrolls = 0
    saved_before = len(result.saved_files)
    while len(result.saved_files) - saved_before < limit:
        if category == "Recordings":
            source_ids = (result.document_ids_by_category or {}).get(category)
            visible = _retry(lambda: _visible_documents(
                page, len(seen) + 1, category, source_ids, recording_ids_by_row,
            ))
        else:
            visible = _retry(lambda: _visible_documents(page, len(seen) + 1))
        fresh = []
        for document in visible:
            if document.source_id not in seen:
                seen.add(document.source_id)
                fresh.append(document)
        for document in fresh:
            document.category = category
            document.display_position = len(result.documents) + 1
            result.documents.append(document)
            if document.security.lower() != "public":
                document.status = "skipped"
                document.error = "Security label: " + document.security
                continue
            try:
                remaining = limit - (len(result.saved_files) - saved_before)
                _retry(lambda: _download_row(page, document, output_dir, max_files=remaining))
                saved_bytes = sum(Path(path).stat().st_size for path in result.saved_files)
                if saved_bytes > MAX_SAVED_BYTES:
                    raise DownloadLimitExceeded(
                        "Requested files exceed the email attachment size limit"
                    )
            except DownloadLimitExceeded:
                raise
            except Exception as error:
                document.status = "failed"
                document.error = str(error)
                _capture_failure(page, output_dir, "download-" + document.source_id, error)
            if len(result.saved_files) - saved_before >= limit:
                return
        if len(seen) >= result.category_counts[category]:
            return
        stagnant_scrolls = stagnant_scrolls + 1 if not fresh else 0
        if stagnant_scrolls >= 3 or not _scroll_documents(page):
            raise DownloadError(
                "{} grid showed {} of {} listed rows".format(
                    category, len(seen), result.category_counts[category],
                )
            )


def _new_session(browser):
    context = browser.new_context(
        accept_downloads=True, viewport={"width": 1920, "height": 1080}
    )
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        return context, page
    except Exception:
        context.close()
        raise


def scrape_matter(
    matter_number: str,
    requested_category: str,
    output_dir: Path,
    *,
    max_documents: int = 10,
    headless: bool = True,
) -> MatterResult:
    """Search one matter, save requested Public files by category, and return facts.

    An empty category string reads the matter and its counts without downloading.
    Each requested type is saved in its own folder, up to max_documents files.
    """
    matter_number = matter_number.upper()
    if not re.fullmatch(r"M\d{5}", matter_number):
        raise ValueError("Matter number must look like M12345")
    requested = split_categories(requested_category)
    if not 1 <= max_documents <= 10:
        raise ValueError("max_documents must be between 1 and 10")
    output_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = None
        try:
            context, page = _new_session(browser)
            try:
                _retry(lambda: _find_matter(page, matter_number))
                result = _retry(lambda: _read_header(page, matter_number, ", ".join(requested)))
                _inventory_matter(page, result, output_dir / "lists")
                for category in requested:
                    if result.category_counts[category] == 0:
                        continue
                    _retry(lambda category=category: _open_category(page, category))
                    _collect_documents(
                        page, result, output_dir / category, max_documents, category,
                    )
                return result
            except MatterNotFound:
                raise
            except Exception as error:
                _capture_failure(page, output_dir, "scrape", error)
                raise
        finally:
            try:
                if context is not None:
                    context.close()
            finally:
                browser.close()
