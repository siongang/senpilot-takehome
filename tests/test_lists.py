"""Validate real Save List PDFs, report identity, and complete matter inventories."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from senpilot_agent.models import CATEGORIES, MatterResult
from senpilot_agent.uarb import browser
from senpilot_agent.uarb.lists import read_list_pdf


FIXTURES = Path(__file__).parent / "fixtures" / "uarb-lists"
COUNTS = dict(zip(CATEGORIES, (13, 6, 43, 0, 0)))


def matter():
    return MatterResult("M12205", "Key Documents", "Example", "Closed", "Water", "Capital",
                        "", "", "", "", dict(COUNTS))


class ListTests(unittest.TestCase):
    def test_real_multipage_reports_deduplicate_shared_documents(self):
        result = matter()
        inventory = {}
        for category in CATEGORIES:
            inventory[category] = (read_list_pdf(
                FIXTURES / (category.lower().replace(" ", "-") + ".pdf"),
                "M12205", category, COUNTS[category],
            ) if COUNTS[category] else [])
        self.assertIn("H-4(C)-III", inventory["Exhibits"])
        self.assertIn("H-5(C)-II", inventory["Exhibits"])
        self.assertEqual(set(inventory["Key Documents"]) - set(inventory["Other Documents"]), set())
        result.document_ids_by_category = inventory
        self.assertEqual(result.listing_total, 62)
        self.assertEqual(result.document_total, 56)
        restored = MatterResult.from_dict(result.to_dict())
        self.assertEqual(restored.document_total, 56)
        self.assertEqual(restored.document_ids_by_category, inventory)
        # Exported reports never become requested document attachments.
        result.list_pdf_paths = {"Key Documents": str(FIXTURES / "key-documents.pdf")}
        self.assertEqual(result.saved_files, [])

    def test_wrong_matter_wrong_category_and_truncated_lists_are_rejected(self):
        for matter_number, category, expected in (
            ("M99999", "Other Documents", 43),
            ("M12205", "Key Documents", 43),
            ("M12205", "Other Documents", 44),
        ):
            with self.subTest(matter=matter_number, category=category, expected=expected):
                with self.assertRaises(ValueError):
                    read_list_pdf(FIXTURES / "other-documents.pdf", matter_number, category, expected)

    def test_recordings_sound_files_report(self):
        path = FIXTURES / "recordings.pdf"
        self.assertEqual(read_list_pdf(path, "M12406", "Recordings", 5),
                         ["SF07694", "SF07695", "SF07696", "SF07697", "SF07699"])
        with self.assertRaises(ValueError):
            read_list_pdf(path, "M12406", "Exhibits", 5)
        with self.assertRaises(ValueError):
            read_list_pdf(path, "M12406", "Recordings", 4)

    def test_unrecognized_or_empty_report_does_not_become_zero(self):
        reader = MagicMock()
        page = MagicMock()
        page.extract_text.return_value = "Other Documents List\nM12205\nNo extractable table"
        for pages in ([], [page]):
            reader.pages = pages
            with patch("senpilot_agent.uarb.lists.PdfReader", return_value=reader):
                with self.assertRaises(ValueError):
                    read_list_pdf(Path("report.pdf"), "M12205", "Other Documents", 0)

    def test_partial_inventory_and_legacy_results_have_no_total(self):
        result = matter()
        self.assertIsNone(result.document_total)
        legacy = result.to_dict()
        legacy.pop("document_ids_by_category")
        legacy.pop("list_pdf_paths")
        self.assertIsNone(MatterResult.from_dict(legacy).document_total)
        result.document_ids_by_category = {"Key Documents": ["97289"]}
        self.assertIsNone(result.document_total)
        result.document_ids_by_category = {name: [] for name in CATEGORIES}
        self.assertIsNone(result.document_total)

    def test_inventory_reads_all_nonempty_tabs_even_when_requested_tab_is_empty(self):
        result = matter()
        result.requested_category = "Transcripts"
        page = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            browser, "_open_category"
        ) as open_category, patch.object(browser, "_download_list") as download:
            download.side_effect = lambda page, category, folder: FIXTURES / (
                category.lower().replace(" ", "-") + ".pdf")
            browser._inventory_matter(page, result, Path(directory))
        self.assertEqual([call.args[1] for call in open_category.call_args_list],
                         ["Exhibits", "Key Documents", "Other Documents"])
        self.assertEqual(result.document_total, 56)
        self.assertEqual(result.document_ids_by_category["Transcripts"], [])

    def test_inventory_failure_never_publishes_a_partial_total(self):
        result = matter()
        with patch.object(browser, "_open_category"), patch.object(
            browser, "_download_list", return_value=FIXTURES / "exhibits.pdf"
        ), patch.object(browser, "read_list_pdf", side_effect=[
            [str(i) for i in range(13)], ValueError("wrong category"), ValueError("wrong category"),
        ]):
            with self.assertRaises(browser.DownloadError):
                browser._inventory_matter(MagicMock(), result, Path("lists"))
        self.assertIsNone(result.document_total)
        self.assertEqual(result.list_pdf_paths, {})

    def test_download_list_saves_pdf_and_closes_dialog(self):
        page = MagicMock()
        controls = page.locator.return_value
        controls.count.return_value = 1
        controls.first.inner_text.return_value = "KeyDocList.pdf"
        download = page.expect_download.return_value.__enter__.return_value.value
        download.failure.return_value = None
        download.save_as.side_effect = lambda path: path.write_bytes(b"%PDF-report")
        with tempfile.TemporaryDirectory() as directory, patch.object(
            browser, "_close_download_dialog"
        ) as close:
            path = browser._download_list(page, "Key Documents", Path(directory))
            self.assertEqual(path.read_bytes(), b"%PDF-report")
            self.assertEqual(path.name, "key-documents.pdf")
        page.get_by_role.assert_called_with("button", name="Save List", exact=True)
        close.assert_called_once_with(page)

    def test_download_list_cleans_invalid_file_and_closes_dialog(self):
        page = MagicMock()
        controls = page.locator.return_value
        controls.count.return_value = 1
        controls.first.inner_text.return_value = "KeyDocList.pdf"
        download = page.expect_download.return_value.__enter__.return_value.value
        download.failure.return_value = None
        download.save_as.side_effect = lambda path: path.write_bytes(b"<html>Error</html>")
        with tempfile.TemporaryDirectory() as directory, patch.object(
            browser, "_close_download_dialog"
        ) as close:
            with self.assertRaises(browser.DownloadError):
                browser._download_list(page, "Key Documents", Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])
        close.assert_called_once_with(page)


if __name__ == "__main__":
    unittest.main()
