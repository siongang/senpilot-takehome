"""Focused checks for request ambiguity, duplicate intake, and empty archives."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from senpilot_agent.jobs.store import JobStore
from senpilot_agent.models import MatterResult
from senpilot_agent.output.archive import create_zip
from senpilot_agent.requests.parser import ParsedRequest


class CoreTests(unittest.TestCase):
    def test_message_id_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3")
            try:
                message = {
                    "inbox_id": "agent@example.com",
                    "message_id": "message-1",
                    "from": "requester@example.com",
                    "subject": "M12205 Key Documents",
                    "text": "Send Key Documents from M12205",
                }
                parsed = ParsedRequest("M12205", "Key Documents")
                self.assertTrue(store.enqueue(message, parsed))
                self.assertFalse(store.enqueue(message, parsed))
                self.assertTrue(store.has_message("agent@example.com", "message-1"))
            finally:
                store.close()

    def test_zero_count_result_still_has_a_valid_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            result = MatterResult(
                matter_number="M12205",
                requested_category="Transcripts",
                title="Example",
                status="Closed",
                matter_type="Water",
                category="Capital Expenditure Approvals",
                date_received="04/07/2025",
                second_date_label="Decision Date",
                second_date="10/23/2025",
                outcome="Directions given",
                category_counts={
                    "Exhibits": 13,
                    "Key Documents": 6,
                    "Other Documents": 43,
                    "Transcripts": 0,
                    "Recordings": 0,
                },
            )
            path = create_zip(result, Path(directory) / "empty.zip")
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), [])
                self.assertIsNone(archive.testzip())


if __name__ == "__main__":
    unittest.main()
