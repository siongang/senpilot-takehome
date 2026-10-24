"""Reply wording stays grammatical as file and listing counts change."""

import unittest

from senpilot_agent.models import CATEGORIES, Document, MatterResult
from senpilot_agent.output.response import format_empty, format_result


def _counts(**overrides):
    counts = {name: 0 for name in CATEGORIES}
    counts.update(overrides)
    return counts


def _matter(category, counts, documents, title="Example", received="06/26/2025", decided="06/26/2025"):
    return MatterResult(
        "M12345", category, title, "Closed", "Petroleum Product Pricing", "Pricing",
        received, "Decision Date", decided, "Directions given", counts, documents,
    )


def _downloaded(count, files_each=1):
    documents = []
    for index in range(count):
        names = ["{}-{}.pdf".format(index, file_index) for file_index in range(files_each)]
        documents.append(Document(
            str(index), "Doc", "06/26/2025", "Public", "pdf", index,
            status="downloaded", filenames=names, saved_paths=names,
        ))
    return documents


class ResponseTests(unittest.TestCase):
    def test_verified_inventory_total_is_separate_from_listing_and_attachment_counts(self):
        result = _matter("Key Documents", _counts(**{"Key Documents": 1, "Other Documents": 1}), _downloaded(1))
        result.document_ids_by_category = {name: [] for name in CATEGORIES}
        result.document_ids_by_category.update({"Key Documents": ["97289"], "Other Documents": ["97289"]})
        body = format_result(result, 1)
        self.assertIn("Total documents in this matter: 1.", body)
        self.assertIn("including confidential records", body)
        self.assertIn("may contain multiple files", body)
        self.assertIn("These five tabs show 2 listings.", body)
        self.assertIn("The ZIP contains 1 file", body)
        result.document_ids_by_category = None
        self.assertIn("Total documents in this matter: unavailable", format_result(result, 1))

    def test_one_file_uses_singular_wording(self):
        title = (
            "GAS: Gas Pricing effective June 27, 2025\n"
            "Prices Prescribed for Petroleum Products effective on and after 12:01 a.m. on June 27, 2025."
        )
        result = _matter("Key Documents", _counts(**{"Key Documents": 1, "Other Documents": 1}), _downloaded(1), title)
        body = format_result(result, 1)
        self.assertIn("Here are the results for M12345.", body)
        self.assertIn("M12345\nGAS: Gas Pricing effective June 27, 2025\n\nPrices Prescribed", body)
        self.assertIn("Date received: June 26, 2025", body)
        self.assertIn("Decision Date: June 26, 2025", body)
        self.assertIn("Key Documents: 1 (requested)", body)
        self.assertIn("Other Documents: 1", body)
        self.assertIn("These five tabs show 2 listings.", body)
        self.assertIn("The ZIP contains 1 file from Key Documents.", body)
        self.assertNotIn("1 files", body)
        self.assertNotIn("1 listings", body)

    def test_several_files_use_plural_wording(self):
        result = _matter("Key Documents", _counts(**{"Key Documents": 2, "Other Documents": 1}), _downloaded(2))
        body = format_result(result, 2)
        self.assertIn("The ZIP contains 2 files from Key Documents.", body)
        self.assertIn("These five tabs show 3 listings.", body)

    def test_partial_tab_states_both_counts(self):
        result = _matter(
            "Other Documents", _counts(**{"Other Documents": 43}), _downloaded(10),
            title="Halifax water", received="04/07/2025", decided="10/23/2025",
        )
        body = format_result(result, 10)
        self.assertIn("Date received: April 7, 2025", body)
        self.assertIn("Decision Date: October 23, 2025", body)
        self.assertIn("The ZIP contains 10 files from Other Documents (43 listings on that tab).", body)
        one = _matter("Other Documents", _counts(**{"Other Documents": 43}), _downloaded(1))
        self.assertIn(
            "The ZIP contains 1 file from Other Documents (43 listings on that tab).",
            format_result(one, 1),
        )

    def test_multiple_files_from_one_listing_are_counted_separately(self):
        result = _matter("Key Documents", _counts(**{"Key Documents": 1}), _downloaded(1, files_each=2))
        self.assertIn(
            "The ZIP contains 2 files from 1 listing in Key Documents.",
            format_result(result, 2),
        )
        wider = _matter("Key Documents", _counts(**{"Key Documents": 6}), _downloaded(1, files_each=2))
        self.assertIn(
            "The ZIP contains 2 files from 1 listing in Key Documents (6 listings on that tab).",
            format_result(wider, 2),
        )
        self.assertIn("These five tabs show 1 listing.", format_result(result, 2))

    def test_empty_replies_match_the_listing_count(self):
        none = _matter("Exhibits", _counts(), [])
        self.assertIn("This matter lists no Exhibits. The ZIP is empty.", format_empty(none))
        one = _matter("Exhibits", _counts(Exhibits=1), [])
        self.assertIn(
            "No files could be attached from the selected Exhibits listing. The ZIP is empty.",
            format_empty(one),
        )
        several = _matter("Exhibits", _counts(Exhibits=3), [])
        self.assertIn(
            "No files could be attached from the selected Exhibits listings. The ZIP is empty.",
            format_empty(several),
        )

    def test_unparsed_dates_stay_as_shown_on_the_site(self):
        result = _matter("Exhibits", _counts(Exhibits=1), _downloaded(1), received="Today", decided="Today")
        body = format_result(result, 1)
        self.assertIn("Date received: Today", body)
        self.assertIn("Decision Date: Today", body)
