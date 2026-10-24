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


class ScraperTests(unittest.TestCase):
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
        page = MagicMock()
        page.get_by_role.return_value.wait_for.side_effect = PlaywrightTimeout("slow")
        page.locator.return_value.inner_text.return_value = "Loading documents"
        with self.assertRaises(PlaywrightTimeout):
            browser._retry(lambda: browser._find_matter(page, "M12205"))
        self.assertEqual(page.goto.call_count, 2)

    def test_explicit_not_found_is_not_retried(self):
        page = MagicMock()
        page.get_by_role.return_value.wait_for.side_effect = PlaywrightTimeout("missing")
        page.locator.return_value.inner_text.return_value = "No Records Found"
        with self.assertRaises(browser.MatterNotFound):
            browser._retry(lambda: browser._find_matter(page, "M99999"))
        self.assertEqual(page.goto.call_count, 1)

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
