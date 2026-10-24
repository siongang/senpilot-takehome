"""Browser recovery tests without a live website or Chromium process."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from senpilot_agent.models import CATEGORIES, Document, MatterResult
from senpilot_agent.uarb import browser


def document(source_id="1", security="Public"):
    return Document(source_id, "Example", "2025-01-01", security, ".pdf", 1)


def matter(count=3):
    return MatterResult(
        "M12205", "Exhibits", "Example", "Closed", "Water", "Water", "", "", "", "",
        {category: count if category == "Exhibits" else 0 for category in CATEGORIES},
    )


def search_page(*, body="Loading documents", dialog=False, dialog_after_click=False):
    """Page double whose matter wait can time out or show No Records Found."""
    page = MagicMock()
    exhibits = MagicMock()
    dialog_locator = MagicMock()
    sentence = MagicMock()
    race = MagicMock()
    visible = {"dialog": dialog}

    def dialog_visible():
        return visible["dialog"]

    dialog_locator.first.is_visible.side_effect = dialog_visible
    sentence.first.is_visible.return_value = False
    if dialog:
        race.first.wait_for.return_value = None
    else:
        race.first.wait_for.side_effect = PlaywrightTimeout("slow")
    exhibits.or_.return_value = race
    race.or_.return_value = race

    def get_by_role(role, name=None, **kwargs):
        return dialog_locator if role == "dialog" else exhibits

    page.get_by_role.side_effect = get_by_role
    page.get_by_text.return_value = sentence
    field = MagicMock()
    search = MagicMock()
    search.count.return_value = 1 if dialog_after_click else 0
    search.first.is_visible.return_value = bool(dialog_after_click)

    def click(*args, **kwargs):
        visible["dialog"] = True
        raise PlaywrightTimeout("dialog covers Search")

    search.click.side_effect = click
    body_locator = MagicMock()
    body_locator.inner_text.return_value = body

    def locator(selector):
        if selector == "body":
            return body_locator
        if selector == ".fm_object_258 button":
            return search
        return field

    page.locator.side_effect = locator
    return page, search


class ScraperTests(unittest.TestCase):
    def test_category_retry_skips_click_when_tab_is_selected(self):
        page = MagicMock()
        tab = page.get_by_role.return_value
        selected = page.locator.return_value.filter.return_value
        selected.count.return_value = 1
        browser._open_category(page, "Recordings")
        tab.click.assert_not_called()
        selected.first.wait_for.assert_any_call(state="visible", timeout=30000)
        selected.count.return_value = 0
        browser._open_category(page, "Recordings")
        tab.click.assert_called_once_with()

    def test_retry_recovers_browser_failures_but_is_bounded(self):
        operation = MagicMock(side_effect=[PlaywrightTimeout("slow"), "done"])
        self.assertEqual(browser._retry(operation), "done")
        self.assertEqual(operation.call_count, 2)
        operation = MagicMock(side_effect=browser.DownloadError("bad file"))
        with self.assertRaises(browser.DownloadError):
            browser._retry(operation)
        self.assertEqual(operation.call_count, 2)
        operation = MagicMock(side_effect=ValueError("bad input"))
        with self.assertRaises(ValueError):
            browser._retry(operation)
        operation.assert_called_once()

    def test_search_timeout_without_explicit_not_found_is_retryable(self):
        page, search = search_page()
        with self.assertRaises(PlaywrightTimeout):
            browser._retry(lambda: browser._find_matter(page, "M12205"))
        self.assertEqual(page.goto.call_count, 2)
        search.click.assert_not_called()

    def test_explicit_not_found_is_not_retried(self):
        page, search = search_page(body="No Records Found")
        with self.assertRaises(browser.MatterNotFound):
            browser._retry(lambda: browser._find_matter(page, "M99999"))
        self.assertEqual(page.goto.call_count, 1)
        search.click.assert_not_called()

    def test_not_found_dialog_stops_before_search_click(self):
        page, search = search_page(dialog=True)
        with self.assertRaises(browser.MatterNotFound):
            browser._retry(lambda: browser._find_matter(page, "M99999"))
        search.click.assert_not_called()
        self.assertEqual(page.goto.call_count, 1)

    def test_dialog_covering_search_is_not_retried(self):
        page, search = search_page(dialog_after_click=True)
        with self.assertRaises(browser.MatterNotFound):
            browser._retry(lambda: browser._find_matter(page, "M99999"))
        self.assertEqual(page.goto.call_count, 1)
        search.click.assert_called_once()

    def test_wrong_displayed_matter_does_not_mean_requested_matter_is_missing(self):
        with patch.object(browser, "_text_any", return_value="M12345"):
            with self.assertRaises(RuntimeError) as raised:
                browser._read_header(MagicMock(), "M99999", "Key Documents")
        self.assertNotIsInstance(raised.exception, browser.MatterNotFound)

    def test_multi_file_failure_rolls_back_attempt_before_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            page = MagicMock()
            controls = MagicMock()
            page.locator.return_value = controls
            controls.count.return_value = 2
            controls.nth.return_value.inner_text.return_value = "offered.pdf"
            calls = 0

            def expect_download(**kwargs):
                nonlocal calls
                calls += 1
                download = MagicMock()
                download.failure.return_value = None
                download.suggested_filename = "a.pdf" if calls % 2 else "b.pdf"
                content = b"bad pdf" if calls == 2 else b"%PDF-good"
                download.save_as.side_effect = lambda path: Path(path).write_bytes(content)
                event = MagicMock()
                event.__enter__.return_value = SimpleNamespace(value=download)
                return event

            page.expect_download.side_effect = expect_download
            item = document()
            with patch.object(browser, "_close_download_dialog"):
                browser._retry(lambda: browser._download_row(page, item, Path(folder)))
            self.assertEqual(calls, 4)
            self.assertEqual(item.status, "downloaded")
            self.assertEqual(item.filenames, ["a.pdf", "b.pdf"])
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["a.pdf", "b.pdf"])

    def test_failed_save_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            page = MagicMock()
            controls = page.locator.return_value
            controls.count.return_value = 1
            controls.nth.return_value.inner_text.return_value = "a.pdf"
            download = MagicMock()
            download.failure.return_value = None
            download.suggested_filename = "a.pdf"

            def interrupted(path):
                Path(path).write_bytes(b"partial")
                raise PlaywrightTimeout("transfer interrupted")

            download.save_as.side_effect = interrupted
            page.expect_download.return_value.__enter__.return_value = SimpleNamespace(value=download)
            item = document()
            with patch.object(browser, "_close_download_dialog"), self.assertRaises(PlaywrightTimeout):
                browser._download_row(page, item, Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])
            self.assertEqual(item.saved_paths, [])

    def test_multi_file_row_honors_remaining_file_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            page = MagicMock()
            controls = page.locator.return_value
            controls.count.return_value = 4
            controls.nth.return_value.inner_text.return_value = "a.pdf"
            download = MagicMock()
            download.failure.return_value = None
            download.suggested_filename = "a.pdf"
            download.save_as.side_effect = lambda path: Path(path).write_bytes(b"%PDF-good")
            page.expect_download.return_value.__enter__.return_value = SimpleNamespace(value=download)
            item = document()
            with patch.object(browser, "_close_download_dialog"):
                browser._download_row(page, item, Path(folder), max_files=2)
            self.assertEqual(page.expect_download.call_count, 2)
            self.assertEqual(len(item.saved_paths), 2)

    def test_recordings_use_save_list_ids_instead_of_date_column(self):
        page = MagicMock()
        rows = page.locator.return_value
        rows.count.return_value = 2
        first, second = MagicMock(), MagicMock()
        rows.nth.side_effect = [first, second]
        first.get_by_role.return_value.count.return_value = 1
        second.get_by_role.return_value.count.return_value = 1

        def row_text(row, selector):
            values = {
                first: {".fm_object_201 .text": "10/06/2026", ".fm_object_202 .text": "Part 1", ".fm_object_204 .text": "Public"},
                second: {".fm_object_201 .text": "10/06/2026", ".fm_object_202 .text": "Part 2", ".fm_object_204 .text": "Public"},
            }
            return values[row].get(selector, "")

        with patch.object(browser, "_row_text", side_effect=row_text), patch.object(
            browser, "_row_extension", return_value=""
        ):
            found = browser._visible_documents(page, 1, "Recordings", ["SF07694", "SF07695"])
        self.assertEqual([item.source_id for item in found], ["SF07694", "SF07695"])
        self.assertEqual([item.title for item in found], ["Part 1", "Part 2"])
        self.assertEqual([item.filed_date for item in found], ["10/06/2026", "10/06/2026"])

    def test_overlapping_recording_grid_keeps_existing_ids(self):
        page = MagicMock()
        rows = page.locator.return_value
        rows.count.return_value = 2
        first, second, third = MagicMock(), MagicMock(), MagicMock()
        for row in (first, second, third):
            row.get_by_role.return_value.count.return_value = 1
        rows.nth.side_effect = [first, second, second, third]
        titles = {first: "Part 1", second: "Part 2", third: "Part 3"}

        def row_text(row, selector):
            if selector == ".fm_object_202 .text":
                return titles[row]
            if selector == ".fm_object_201 .text":
                return "10/06/2026"
            if selector == ".fm_object_204 .text":
                return "Public"
            return ""

        ids = ["SF07694", "SF07695", "SF07696"]
        known = {}
        with patch.object(browser, "_row_text", side_effect=row_text), patch.object(
            browser, "_row_extension", return_value=""
        ):
            first_window = browser._visible_documents(page, 1, "Recordings", ids, known)
            next_window = browser._visible_documents(page, 3, "Recordings", ids, known)
        self.assertEqual([item.source_id for item in first_window], ids[:2])
        self.assertEqual([item.source_id for item in next_window], ids[1:])

    def test_recording_export_prepares_then_downloads_file(self):
        with tempfile.TemporaryDirectory() as folder:
            page = MagicMock()
            rows = MagicMock()
            matching_rows = MagicMock()
            matching_rows.count.return_value = 1
            matching_rows.filter.return_value = matching_rows
            rows.filter.return_value = matching_rows
            controls = MagicMock()
            controls.count.return_value = 1
            controls.nth.return_value.inner_text.return_value = "sound.mp3"
            page.locator.side_effect = lambda selector: (
                rows if selector == "tr.v-grid-row[role='row']" else controls
            )
            dialog = page.get_by_role.return_value
            dialog.locator.return_value.input_value.return_value = "sound.mp3"
            download = page.expect_download.return_value.__enter__.return_value.value
            download.failure.return_value = None
            download.suggested_filename = "sound.mp3"
            download.save_as.side_effect = lambda path: path.write_bytes(b"audio")
            order = []
            dialog.get_by_role.return_value.click.side_effect = lambda **kwargs: order.append("prepare")
            controls.nth.return_value.click.side_effect = lambda **kwargs: order.append("download")
            item = document("SF07694")
            item.category = "Recordings"
            with patch.object(browser, "_close_download_dialog"):
                browser._download_row(page, item, Path(folder))
            self.assertEqual(order, ["prepare", "download"])
            self.assertEqual(item.filenames, ["sound.mp3"])
            self.assertEqual(Path(item.saved_paths[0]).read_bytes(), b"audio")

    def test_recording_export_error_cancels_modal_before_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            page = MagicMock()
            rows = page.locator.return_value
            matches = rows.filter.return_value
            matches.filter.return_value = matches
            matches.count.return_value = 1
            dialog = page.get_by_role.return_value
            dialog.locator.return_value.input_value.return_value = ""
            dialog.is_visible.return_value = True
            item = document("SF07694")
            item.category = "Recordings"
            with patch.object(browser, "_close_download_dialog"):
                with self.assertRaisesRegex(browser.DownloadError, "no filename"):
                    browser._download_row(page, item, Path(folder))
            dialog.get_by_role.return_value.click.assert_called_once_with(timeout=10000)
            dialog.wait_for.assert_any_call(state="hidden", timeout=10000)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_incomplete_grid_raises_instead_of_returning_partial_success(self):
        result = matter(3)

        def save(page, item, folder, **kwargs):
            path = folder / "one.pdf"
            path.write_bytes(b"%PDF-good")
            item.saved_paths = [str(path)]
            item.status = "downloaded"

        with tempfile.TemporaryDirectory() as folder, patch.object(
            browser, "_visible_documents", return_value=[document("1")]
        ), patch.object(browser, "_download_row", side_effect=save), patch.object(
            browser, "_scroll_documents", return_value=False
        ):
            with self.assertRaisesRegex(browser.DownloadError, "showed 1 of 3"):
                browser._collect_documents(MagicMock(), result, Path(folder), 10)
        self.assertEqual(len(result.saved_files), 1)

    def test_collection_passes_remaining_file_count_to_multi_file_rows(self):
        result = matter(3)
        allowances = []

        def save(page, item, folder, *, max_files):
            allowances.append(max_files)
            item.status = "downloaded"
            for index in range(min(2, max_files)):
                path = folder / (item.source_id + str(index) + ".pdf")
                path.write_bytes(b"%PDF-good")
                item.saved_paths.append(str(path))

        with tempfile.TemporaryDirectory() as folder, patch.object(
            browser, "_visible_documents", return_value=[document("1"), document("2"), document("3")]
        ), patch.object(browser, "_download_row", side_effect=save):
            browser._collect_documents(MagicMock(), result, Path(folder), 3)
        self.assertEqual(allowances, [3, 1])
        self.assertEqual(len(result.saved_files), 3)
        self.assertEqual(len(result.documents), 2)

    def test_collection_deduplicates_and_skips_private_rows(self):
        result = matter()
        private = document("private", "Confidential")
        public = document("public")
        last = document("last")

        def save(page, item, folder, **kwargs):
            path = folder / (item.source_id + ".pdf")
            path.write_bytes(b"%PDF-good")
            item.saved_paths = [str(path)]
            item.filenames = [path.name]
            item.status = "downloaded"

        with tempfile.TemporaryDirectory() as folder, patch.object(
            browser, "_visible_documents", side_effect=[[private, public, public], [public, last]]
        ), patch.object(browser, "_scroll_documents", return_value=True), patch.object(
            browser, "_download_row", side_effect=save
        ) as download:
            browser._collect_documents(MagicMock(), result, Path(folder), 10)
        self.assertEqual([item.source_id for item in result.documents], ["private", "public", "last"])
        self.assertEqual([item.display_position for item in result.documents], [1, 2, 3])
        self.assertEqual(result.documents[0].status, "skipped")
        self.assertEqual(download.call_count, 2)

    def test_attachment_budget_removes_only_over_budget_row(self):
        result = matter(2)

        def save(page, item, folder, **kwargs):
            path = folder / (item.source_id + ".pdf")
            path.write_bytes(b"%PDF-good")
            item.saved_paths = [str(path)]
            item.filenames = [path.name]
            item.status = "downloaded"

        with tempfile.TemporaryDirectory() as folder, patch.object(
            browser, "_visible_documents", return_value=[document("1"), document("2")]
        ), patch.object(browser, "_download_row", side_effect=save), patch.object(
            browser, "MAX_SAVED_BYTES", 9
        ):
            browser._collect_documents(MagicMock(), result, Path(folder), 10)
            self.assertTrue((Path(folder) / "1.pdf").exists())
            self.assertFalse((Path(folder) / "2.pdf").exists())
        self.assertEqual(result.documents[1].status, "skipped")
        self.assertEqual(result.documents[1].saved_paths, [])
        self.assertIn("budget", result.documents[1].error)

    def test_failed_row_is_reported_once_and_following_row_continues(self):
        result = matter(2)

        def save(page, item, folder, **kwargs):
            if item.source_id == "bad":
                raise browser.DownloadError("unavailable")
            path = folder / "good.pdf"
            path.write_bytes(b"%PDF-good")
            item.saved_paths = [str(path)]
            item.status = "downloaded"

        with tempfile.TemporaryDirectory() as folder, patch.object(
            browser, "_visible_documents", return_value=[document("bad"), document("good")]
        ), patch.object(browser, "_download_row", side_effect=save) as download, patch.object(
            browser, "_capture_failure"
        ) as diagnostics:
            browser._collect_documents(MagicMock(), result, Path(folder), 10)
        self.assertEqual(download.call_count, 3)
        self.assertEqual([item.status for item in result.documents], ["failed", "downloaded"])
        diagnostics.assert_called_once()

    def test_success_limit_stops_without_downloading_later_rows(self):
        result = matter(20)

        def save(page, item, folder, **kwargs):
            item.status = "downloaded"
            item.saved_paths = [str(folder / (item.source_id + ".pdf"))]
            Path(item.saved_paths[0]).write_bytes(b"%PDF-good")

        with tempfile.TemporaryDirectory() as folder, patch.object(
            browser, "_visible_documents", return_value=[document(str(i)) for i in range(20)]
        ), patch.object(browser, "_download_row", side_effect=save) as download:
            browser._collect_documents(MagicMock(), result, Path(folder), 10)
        self.assertEqual(download.call_count, 10)
        self.assertEqual(len(result.documents), 10)

    def test_diagnostics_failure_does_not_replace_primary_error(self):
        page = MagicMock()
        page.screenshot.side_effect = RuntimeError("closed")
        page.content.side_effect = RuntimeError("closed")
        with tempfile.TemporaryDirectory() as folder:
            browser._capture_failure(page, Path(folder), "download-1", RuntimeError("original"))
            text = next((Path(folder) / "diagnostics").glob("*.txt")).read_text()
        self.assertEqual(text, "original")

    def test_zero_count_does_not_open_category_and_closes_session(self):
        playwright = MagicMock()
        chromium = playwright.chromium.launch.return_value
        context = MagicMock()
        page = MagicMock()
        with tempfile.TemporaryDirectory() as folder, patch.object(
            browser, "sync_playwright"
        ) as runtime, patch.object(browser, "_new_session", return_value=(context, page)), patch.object(
            browser, "_find_matter"
        ), patch.object(browser, "_read_header", return_value=matter(0)), patch.object(
            browser, "_open_category"
        ) as open_category:
            runtime.return_value.__enter__.return_value = playwright
            result = browser.scrape_matter("m12205", "Exhibits", Path(folder))
        self.assertEqual(result.documents, [])
        open_category.assert_not_called()
        context.close.assert_called_once()
        chromium.close.assert_called_once()

    def test_dialog_cleanup_uses_visible_text_fallback(self):
        page = MagicMock()
        page.get_by_role.return_value.count.return_value = 1
        page.get_by_role.return_value.nth.return_value.is_visible.return_value = False
        page.get_by_text.return_value.count.return_value = 1
        page.get_by_text.return_value.nth.return_value.is_visible.return_value = True
        browser._close_download_dialog(page)
        page.get_by_text.return_value.nth.return_value.click.assert_called_once()


if __name__ == "__main__":
    unittest.main()
