"""Worker integration checks using real SQLite and mocked network boundaries."""

import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from senpilot_agent.config import Settings
from senpilot_agent.models import CATEGORIES, Document, MatterResult
from senpilot_agent.output.response import format_clarification, format_empty, format_failure, format_not_found, format_result
from senpilot_agent.requests.parser import ParsedRequest, RequestParsingError
from senpilot_agent.worker import Worker
from senpilot_agent.uarb.browser import MatterNotFound


PAST = "2000-01-01T00:00:00+00:00"


class WorkerTests(unittest.TestCase):
    def setUp(self):
        log_patch = patch("senpilot_agent.worker.LOG")
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.directory.name), True, 10, "test", "agent@example.com", "https://example.com", 5, "")
        self.worker = Worker(self.settings)
        self.worker.mail = Mock()
        self.worker.mail.reply.return_value = {"message_id": "outgoing"}
        self.message = {"inbox_id": "agent@example.com", "message_id": "incoming", "from": "person@example.com", "subject": "M12205 exhibits", "text": "Please send exhibits"}
        self.parse = patch("senpilot_agent.worker.parse_request", return_value=ParsedRequest("M12205", "Exhibits"))
        self.parser = self.parse.start()
        self.scrape = patch("senpilot_agent.worker.scrape_matter", side_effect=self.result)
        self.scraper = self.scrape.start()

    def tearDown(self):
        self.scrape.stop()
        self.parse.stop()
        self.worker.close()
        self.directory.cleanup()

    def result(self, matter, category, downloads, **kwargs):
        downloads.mkdir(parents=True, exist_ok=True)
        path = downloads / "document.pdf"
        path.write_bytes(b"test-pdf")
        return MatterResult(matter, category, "Example", "Closed", "Water", "Capital", "Today", "Decision", "Today", "Done", {name: int(name == category) for name in CATEGORIES}, [Document("1", "Test", "Today", "Public", "pdf", 1, status="downloaded", saved_paths=[str(path)], filenames=[path.name])])

    def enqueue(self):
        self.worker.store.enqueue_unparsed(self.message)

    def saved(self, job_id):
        return self.worker.store.get(job_id)

    def due(self, job_id):
        self.worker.store.update(job_id, next_attempt_at=PAST)

    def test_success_persists_parse_and_delivery(self):
        self.enqueue()
        job_id = self.worker.process_next()
        job = self.saved(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["matter_number"], "M12205")
        self.assertEqual(job["outgoing_message_id"], "outgoing")
        self.assertTrue(Path(job["zip_path"]).is_file())
        self.assertIn("The ZIP contains 1 file", job["reply_text"])
        self.assertIsNone(self.worker.process_next())

    def test_inventory_is_persisted_and_counted_in_email(self):
        result = self.result("M12205", "Exhibits", self.settings.runtime_dir / "inventory")
        result.category_counts = {name: int(name in ("Exhibits", "Key Documents", "Other Documents"))
                                  for name in CATEGORIES}
        result.document_ids_by_category = {name: [] for name in CATEGORIES}
        result.document_ids_by_category.update({
            "Exhibits": ["H-1"], "Key Documents": ["97289"], "Other Documents": ["97289"],
        })
        self.scraper.side_effect = None
        self.scraper.return_value = result
        self.enqueue()
        job = self.saved(self.worker.process_next())
        self.assertEqual(job["status"], "completed")
        self.assertIn('"document_ids_by_category"', job["result_json"])
        self.assertIn("Total documents in this matter: 2.", job["reply_text"])

    def multiple(self):
        self.message['subject'] = 'M12205 and M12345 exhibits'
        self.parser.return_value = ParsedRequest('M12205', 'Exhibits', matter_numbers=('M12205', 'M12345'))
        self.enqueue()

    def test_multiple_matters_send_distinct_zips_and_replies(self):
        import zipfile
        self.multiple()
        first = self.saved(self.worker.process_next())
        second = self.saved(self.worker.process_next())
        self.assertEqual([first['matter_number'], second['matter_number']], ['M12205', 'M12345'])
        self.assertEqual([first['status'], second['status']], ['completed', 'completed'])
        self.assertEqual(second['parent_job_id'], first['id'])
        self.assertNotEqual(first['zip_path'], second['zip_path'])
        for job in (first, second):
            with zipfile.ZipFile(job['zip_path']) as archive:
                self.assertEqual(len(archive.namelist()), 1)
            self.assertIn(job['matter_number'], job['reply_text'])
        calls = self.worker.mail.reply.call_args_list
        self.assertEqual([call.args[0] for call in calls], ['incoming', 'incoming'])
        self.assertNotEqual(calls[0].kwargs['idempotency_key'], calls[1].kwargs['idempotency_key'])
        self.parser.assert_called_once()
        self.assertFalse(self.worker.store.enqueue_unparsed(self.message))
        self.assertIsNone(self.worker.process_next())

    def test_first_matter_failure_does_not_block_second(self):
        self.multiple()
        self.scraper.side_effect = lambda matter, *args, **kwargs: (self.result(matter, *args, **kwargs)
            if matter == 'M12345' else (_ for _ in ()).throw(RuntimeError('Unavailable')))
        first = self.saved(self.worker.process_next())
        self.assertEqual(first['status'], 'retry_pending')
        second = self.saved(self.worker.process_next())
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(second['matter_number'], 'M12345')
        self.scraper.side_effect = self.result
        self.due(first['id'])
        self.worker.process_next()
        self.assertEqual(self.worker.mail.reply.call_count, 2)
        self.parser.assert_called_once()

    def test_second_matter_send_retry_does_not_resend_first(self):
        self.multiple()
        self.worker.mail.reply.side_effect = [{'message_id': 'first'}, TimeoutError('uncertain'), {'message_id': 'second'}]
        first = self.saved(self.worker.process_next())
        second = self.saved(self.worker.process_next())
        self.due(second['id'])
        self.worker.process_next()
        self.assertEqual(self.saved(first['id'])['outgoing_message_id'], 'first')
        self.assertEqual(self.saved(second['id'])['outgoing_message_id'], 'second')
        calls = self.worker.mail.reply.call_args_list
        self.assertEqual(calls[1], calls[2])
        self.assertEqual(self.scraper.call_count, 2)

    def test_clarification_does_not_scrape(self):
        self.parser.return_value = ParsedRequest(None, None, "Please specify a matter")
        self.enqueue()
        job = self.saved(self.worker.process_next())
        self.assertEqual(job["status"], "completed")
        self.scraper.assert_not_called()
        self.assertIn("Please specify", self.worker.mail.reply.call_args.args[1])

    def test_missing_matter_replies_without_zip_on_first_attempt(self):
        self.parser.return_value = ParsedRequest("M99999", "Key Documents")
        self.scraper.side_effect = MatterNotFound("No Records Found")
        self.enqueue()
        job = self.saved(self.worker.process_next())
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["attempts"], 1)
        self.assertIsNone(job["zip_path"])
        self.assertIn("M99999 was not found", job["reply_text"])
        self.worker.mail.reply.assert_called_once_with(
            "incoming", job["reply_text"], zip_path=None, idempotency_key=job["id"])
        self.assertIsNone(self.worker.process_failure_notification())

    def test_missing_matter_delivery_retry_reuses_saved_reply_without_search(self):
        self.scraper.side_effect = MatterNotFound("No Records Found")
        self.worker.mail.reply.side_effect = [TimeoutError("uncertain"), {"message_id": "accepted"}]
        self.enqueue()
        job_id = self.worker.process_next()
        self.assertEqual(self.saved(job_id)["status"], "retry_pending")
        self.due(job_id)
        self.worker.process_next()
        self.assertEqual(self.saved(job_id)["status"], "completed")
        self.scraper.assert_called_once()
        self.assertEqual(self.worker.mail.reply.call_args_list[0], self.worker.mail.reply.call_args_list[1])

    def test_missing_matter_does_not_block_other_requested_matter(self):
        self.multiple()
        self.scraper.side_effect = lambda matter, *args, **kwargs: (self.result(matter, *args, **kwargs)
            if matter == "M12345" else (_ for _ in ()).throw(MatterNotFound("No Records Found")))
        first = self.saved(self.worker.process_next())
        second = self.saved(self.worker.process_next())
        self.assertEqual([first["status"], second["status"]], ["completed", "completed"])
        self.assertIsNone(first["zip_path"])
        self.assertTrue(Path(second["zip_path"]).is_file())
        self.assertEqual(self.worker.mail.reply.call_count, 2)

    def test_transient_parser_failure_remains_durable(self):
        self.parser.side_effect = RequestParsingError("Temporary API failure")
        self.enqueue()
        job = self.saved(self.worker.process_next())
        self.assertEqual(job["status"], "retry_pending")
        self.assertIsNone(job["parse_error"])
        self.worker.mail.reply.assert_not_called()
        self.parser.side_effect = None
        self.due(job["id"])
        self.assertEqual(self.saved(self.worker.process_next())["status"], "completed")

    def test_nonretryable_parser_failure_is_terminal(self):
        self.parser.side_effect = RequestParsingError("Invalid configuration", retryable=False)
        self.enqueue()
        job = self.saved(self.worker.process_next())
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["attempts"], 1)
        self.scraper.assert_not_called()

    def test_scrape_failure_exhausts_and_notifies_separately(self):
        self.scraper.side_effect = RuntimeError("Navigation failed")
        self.enqueue()
        for _ in range(3):
            job_id = self.worker.process_next()
            self.due(job_id)
        self.assertEqual(self.saved(job_id)["status"], "failed")
        self.assertIsNone(self.worker.process_next())
        self.assertEqual(self.worker.process_failure_notification(), job_id)
        job = self.saved(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIsNone(job["outgoing_message_id"])
        self.assertEqual(job["failure_notification_message_id"], "outgoing")
        self.assertEqual(self.worker.mail.reply.call_args.kwargs["idempotency_key"], job_id + "-failure")

    def test_send_retry_reuses_exact_zip_and_text(self):
        self.worker.mail.reply.side_effect = [TimeoutError("uncertain send"), {"message_id": "accepted"}]
        self.enqueue()
        job_id = self.worker.process_next()
        first = self.saved(job_id)
        zip_bytes = Path(first["zip_path"]).read_bytes()
        self.due(job_id)
        self.worker.process_next()
        job = self.saved(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(self.scraper.call_count, 1)
        self.assertEqual(Path(job["zip_path"]).read_bytes(), zip_bytes)
        self.assertEqual(self.worker.mail.reply.call_args_list[0], self.worker.mail.reply.call_args_list[1])

    def test_expired_send_is_held_without_resending_or_failure_notice(self):
        self.worker.mail.reply.side_effect = TimeoutError("uncertain send")
        self.enqueue()
        job_id = self.worker.process_next()
        self.worker.store.update(job_id, send_started_at=PAST)
        self.due(job_id)
        self.worker.process_next()
        self.assertEqual(self.saved(job_id)["status"], "delivery_review")
        self.assertEqual(self.worker.mail.reply.call_count, 1)
        self.assertIsNone(self.worker.process_failure_notification())

    def test_fenced_update_prevents_send(self):
        self.enqueue()
        with patch.object(self.worker.store, "update_claimed", return_value=False):
            self.worker.process_next()
        self.worker.mail.reply.assert_not_called()

    def test_all_downloads_failed_retries_instead_of_empty_success(self):
        result = self.result("M12205", "Exhibits", self.settings.runtime_dir / "download")
        result.documents[0].status = "failed"
        self.scraper.side_effect = None
        self.scraper.return_value = result
        self.enqueue()
        job = self.saved(self.worker.process_next())
        self.assertEqual(job["status"], "retry_pending")
        self.assertIsNone(job["result_json"])
        self.worker.mail.reply.assert_not_called()

    def test_intake_isolates_bad_messages_and_does_not_parse(self):
        self.worker.mail.list_recent.return_value = [{"message_id": "broken"}, self.message, {"message_id": "auto"}, {"message_id": "own", "from": "agent@example.com"}]
        self.worker.mail.get_message.side_effect = [RuntimeError("bad message"), self.message, {"message_id": "auto", "headers": {"Auto-Submitted": "auto-replied"}}]
        self.assertEqual(self.worker.ingest_recent(), 1)
        self.parser.assert_not_called()
        self.assertTrue(self.worker.store.has_message("agent@example.com", "incoming"))

    def test_heartbeat_renews_using_a_separate_connection(self):
        self.enqueue()
        job = self.worker.store.claim_next()
        renewed = threading.Event()
        from senpilot_agent.jobs.store import JobStore
        original = JobStore.renew_lease

        def record(store, *args):
            self.assertIsNot(store.connection, self.worker.store.connection)
            success = original(store, *args)
            renewed.set()
            return success

        with patch("senpilot_agent.worker.HEARTBEAT_SECONDS", 0.005), patch.object(JobStore, "renew_lease", record):
            with self.worker._heartbeat(job) as lost:
                self.assertTrue(renewed.wait(timeout=5))
                self.assertFalse(lost.is_set())

    def test_failure_notification_retry_is_independent(self):
        self.parser.side_effect = RequestParsingError("Invalid configuration", retryable=False)
        self.enqueue()
        job_id = self.worker.process_next()
        self.worker.mail.reply.side_effect = [TimeoutError("mail unavailable"), {"message_id": "failure-delivered"}]
        self.worker.process_failure_notification()
        self.assertEqual(self.saved(job_id)["failure_notification_status"], "pending")
        self.worker.store.connection.execute("UPDATE jobs SET failure_notification_next_at=?", (PAST,))
        self.worker.store.connection.commit()
        self.worker.process_failure_notification()
        job = self.saved(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["failure_notification_message_id"], "failure-delivered")
        self.assertEqual(self.worker.mail.reply.call_args_list[0], self.worker.mail.reply.call_args_list[1])

    def test_expired_failure_notification_is_exhausted_without_send(self):
        self.parser.side_effect = RequestParsingError("Invalid configuration", retryable=False)
        self.enqueue()
        job_id = self.worker.process_next()
        self.worker.store.connection.execute("UPDATE jobs SET failure_notification_started_at=?", (PAST,))
        self.worker.store.connection.commit()
        self.worker.process_failure_notification()
        self.assertEqual(self.saved(job_id)["failure_notification_status"], "exhausted")
        self.worker.mail.reply.assert_not_called()

    def test_zero_count_differs_from_unavailable_files_and_partial_response(self):
        result = self.result("M12205", "Exhibits", self.settings.runtime_dir / "download")
        result.documents = []
        self.assertIn("No files could be attached", format_empty(result))
        result.category_counts["Exhibits"] = 0
        self.assertIn("lists no Exhibits", format_empty(result))
        result.documents = [Document("2", "Failed", "Today", "Public", "pdf", 2, status="failed")]
        self.assertIn("could not be downloaded", format_result(result, 0))

    def test_replies_greet_the_sender_by_name(self):
        named = "Sion Gang <person@example.com>"
        result = self.result("M12205", "Exhibits", self.settings.runtime_dir / "named")
        self.assertTrue(format_result(result, 1, named).startswith("Hi Sion Gang,"))
        self.assertTrue(format_empty(result, named).startswith("Hi Sion Gang,"))
        self.assertTrue(format_not_found("M99999", named).startswith("Hi Sion Gang,"))
        self.assertTrue(format_clarification("Please specify a matter", named).startswith("Hi Sion Gang,"))
        self.assertTrue(format_failure({
            "id": "job", "sender": '"Sion Gang" <person@example.com>',
            "matter_number": "M12205", "requested_category": "Exhibits",
        }).startswith("Hi Sion Gang,"))
        self.assertTrue(format_result(result, 1, "person@example.com").startswith("Hi,"))
        self.assertTrue(format_not_found("M12205", "Sion\r\nGang <person@example.com>").startswith("Hi Sion Gang,"))

        self.message["from"] = named
        self.enqueue()
        job = self.saved(self.worker.process_next())
        self.assertTrue(job["reply_text"].startswith("Hi Sion Gang,"))


if __name__ == "__main__":
    unittest.main()
