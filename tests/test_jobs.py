"""Durable queue ownership, crash recovery, and bounded delivery checks."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from senpilot_agent.jobs.store import JobStore
from senpilot_agent.requests.parser import ParsedRequest


PAST = "2000-01-01T00:00:00+00:00"
FUTURE = "2999-01-01T00:00:00+00:00"


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "jobs.sqlite3"
        self.store = JobStore(self.path)
        self.message = {
            "inbox_id": "agent@example.com", "message_id": "message-1",
            "from": "requester@example.com", "subject": "Request", "text": "M12205 exhibits",
        }
        self.parsed = ParsedRequest("M12205", "Exhibits")

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def enqueue(self, parsed=None):
        self.assertTrue(self.store.enqueue(self.message, parsed or self.parsed))

    def expire(self, job):
        self.store.update(job["id"], lease_until=PAST)

    def test_deduplication_and_reopen_preserve_result(self):
        self.enqueue()
        self.assertFalse(self.store.enqueue(self.message, self.parsed))
        job = self.store.claim_next()
        self.store.update_claimed(job["id"], job["attempts"], result_json='{"saved": true}', zip_path="saved.zip")
        self.store.close()
        self.store = JobStore(self.path)
        self.assertTrue(self.store.has_message(self.message["inbox_id"], self.message["message_id"]))
        restored = self.store.get(job["id"])
        self.assertEqual(restored["result_json"], '{"saved": true}')
        self.expire(restored)
        claimed = self.store.claim_next()
        self.assertEqual(claimed["status"], "sending")
        self.assertEqual(claimed["zip_path"], "saved.zip")

    def test_competing_connections_only_claim_once(self):
        self.enqueue()
        barrier = Barrier(2)

        def claim():
            store = JobStore(self.path)
            try:
                barrier.wait(timeout=10)
                return store.claim_next()
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            jobs = list(executor.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(job is not None for job in jobs), 1)
        self.assertEqual(next(job for job in jobs if job)["attempts"], 1)

    def test_clarification_claim_cannot_be_claimed_twice(self):
        self.enqueue(ParsedRequest(None, None, "Please specify a matter"))
        job = self.store.claim_next()
        self.assertEqual(job["status"], "clarification_pending")
        self.assertIsNone(self.store.claim_next())
        self.expire(job)
        self.assertEqual(self.store.claim_next()["attempts"], 2)

    def test_lease_renewal_and_stale_generation_fencing(self):
        self.enqueue()
        first = self.store.claim_next(lease_seconds=1)
        self.assertTrue(self.store.renew_lease(first["id"], first["attempts"], lease_seconds=1200))
        self.assertGreater(self.store.get(first["id"])["lease_until"], first["lease_until"])
        self.expire(first)
        self.assertFalse(self.store.renew_lease(first["id"], first["attempts"]))
        second = self.store.claim_next()
        self.assertFalse(self.store.update_claimed(first["id"], first["attempts"], status="completed"))
        self.store.retry_or_fail(first, RuntimeError("stale failure"))
        self.assertEqual(self.store.get(second["id"])["status"], "scraping")
        self.assertTrue(self.store.update_claimed(second["id"], second["attempts"], status="completed", lease_until=None))
        self.assertFalse(self.store.renew_lease(second["id"], second["attempts"]))
        self.assertIsNone(self.store.claim_next())

    def test_crashes_exhaust_budget(self):
        self.enqueue()
        for attempt in range(1, 4):
            job = self.store.claim_next()
            self.assertEqual(job["attempts"], attempt)
            self.expire(job)
        self.assertIsNone(self.store.claim_next())
        failed = self.store.get(job["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["attempts"], 3)
        self.assertIsNone(failed["lease_until"])
        self.assertIsNone(self.store.claim_next())

    def test_retry_backoff_then_exhaustion(self):
        self.enqueue()
        for attempt in range(1, 4):
            job = self.store.claim_next()
            self.store.retry_or_fail(job, RuntimeError("download failed"))
            saved = self.store.get(job["id"])
            self.assertIsNone(saved["lease_until"])
            self.assertEqual(saved["last_error"], "download failed")
            if attempt < 3:
                self.assertEqual(saved["status"], "retry_pending")
                self.assertIsNone(self.store.claim_next())
                self.store.update(job["id"], next_attempt_at=PAST)
            else:
                self.assertEqual(saved["status"], "failed")
                self.assertIsNone(saved["next_attempt_at"])

    def test_completed_job_never_reclaimed_even_with_expired_lease(self):
        self.enqueue()
        job = self.store.claim_next()
        self.store.update(job["id"], status="completed", lease_until=PAST)
        self.assertIsNone(self.store.claim_next())
        self.assertEqual(self.store.get(job["id"])["status"], "completed")

    def test_unparsed_intake_retries_and_persists_extraction(self):
        self.assertTrue(self.store.enqueue_unparsed(self.message))
        self.assertFalse(self.store.enqueue_unparsed(self.message))
        job = self.store.claim_next()
        self.assertEqual(job["status"], "parsing")
        self.store.retry_or_fail(job, RuntimeError("API unavailable"))
        self.store.update(job["id"], next_attempt_at=PAST)
        job = self.store.claim_next()
        self.assertEqual(job["status"], "parsing")
        self.assertTrue(self.store.save_parse_result(job["id"], job["attempts"], self.parsed))
        saved = self.store.get(job["id"])
        self.assertEqual(saved["matter_number"], "M12205")
        self.assertEqual(saved["status"], "scraping")
        self.assertEqual(saved["attempts"], 2)
        self.assertIsNone(self.store.claim_next())

    def batch(self):
        return ParsedRequest('M12205', 'Exhibits', matter_numbers=('M12205', 'M12345', 'M12205'))

    def test_direct_parsed_enqueue_fans_out_only_once(self):
        self.assertTrue(self.store.enqueue(self.message, self.batch()))
        self.assertFalse(self.store.enqueue(self.message, self.batch()))
        self.assertEqual(len(self.store.recent()), 2)

    def test_atomic_fanout_survives_reopen_and_stale_parse(self):
        self.store.enqueue_unparsed(self.message)
        job = self.store.claim_next()
        self.assertTrue(self.store.save_parse_result(job['id'], job['attempts'], self.batch()))
        self.assertFalse(self.store.save_parse_result(job['id'], job['attempts'], self.batch()))
        self.assertFalse(self.store.enqueue_unparsed(self.message))
        self.store.close()
        self.store = JobStore(self.path)
        rows = list(self.store.connection.execute('SELECT * FROM jobs ORDER BY request_index'))
        self.assertEqual([r['matter_number'] for r in rows], ['M12205', 'M12345'])
        self.assertEqual(rows[1]['parent_job_id'], job['id'])
        self.assertEqual(self.store.claim_next()['matter_number'], 'M12345')

    def test_expired_parse_cannot_expand(self):
        self.store.enqueue_unparsed(self.message)
        job = self.store.claim_next()
        self.expire(job)
        self.assertFalse(self.store.save_parse_result(job['id'], job['attempts'], self.batch()))
        self.assertEqual(len(self.store.recent()), 1)

    def test_fanout_insert_failure_rolls_back_parent_and_children(self):
        self.store.enqueue_unparsed(self.message)
        job = self.store.claim_next()
        self.store.connection.execute("""CREATE TRIGGER fail_child BEFORE INSERT ON jobs
            WHEN NEW.request_index > 0 BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END""")
        self.store.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_parse_result(job['id'], job['attempts'], self.batch())
        self.assertEqual(self.store.get(job['id'])['status'], 'parsing')
        self.assertIsNone(self.store.get(job['id'])['matter_number'])
        self.assertEqual(len(self.store.recent()), 1)

    def test_real_single_message_unique_constraint_migrates_without_data_loss(self):
        legacy = Path(self.directory.name) / 'unique-legacy.sqlite3'
        connection = sqlite3.connect(legacy)
        schema = self.store.connection.execute("SELECT sql FROM sqlite_master WHERE name='jobs'").fetchone()[0]
        schema = schema.replace('UNIQUE(inbox_id, message_id, request_index)', 'UNIQUE(inbox_id, message_id)')
        connection.execute(schema)
        self.enqueue()
        row = self.store.connection.execute('SELECT * FROM jobs').fetchone()
        connection.execute('INSERT INTO jobs VALUES ({})'.format(','.join('?' for _ in row)), tuple(row))
        connection.execute('CREATE INDEX jobs_due ON jobs(status, next_attempt_at)')
        connection.commit()
        connection.close()
        for _ in range(2):
            store = JobStore(legacy)
            try:
                self.assertEqual(store.get(row['id'])['body'], self.message['text'])
                self.assertFalse(store.enqueue(self.message, self.parsed))
                if _ == 0:
                    store.update(row['id'], matter_number=None, requested_category=None, status='parse_pending')
                    job = store.claim_next()
                    self.assertTrue(store.save_parse_result(job['id'], job['attempts'], self.batch()))
                self.assertEqual(len(store.recent()), 2)
                self.assertEqual(store.connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            finally:
                store.close()

    def test_failure_delivery_separate_from_retrieval_and_reopen(self):
        self.enqueue()
        job = self.store.claim_next()
        self.store.retry_or_fail(job, RuntimeError("broken"), max_attempts=1)
        notification = self.store.claim_failure_notification()
        self.assertEqual(notification["failure_notification_attempts"], 1)
        self.assertIsNotNone(notification["failure_notification_started_at"])
        self.assertIsNone(self.store.claim_failure_notification())
        self.assertTrue(self.store.finish_failure_notification(notification, message_id="failure-mail"))
        self.store.close()
        self.store = JobStore(self.path)
        saved = self.store.get(job["id"])
        self.assertEqual(saved["status"], "failed")
        self.assertIsNone(saved["outgoing_message_id"])
        self.assertEqual(saved["failure_notification_message_id"], "failure-mail")
        self.assertEqual(saved["failure_notification_started_at"], notification["failure_notification_started_at"])
        self.assertIsNone(self.store.claim_failure_notification())

    def test_failure_delivery_retry_and_crashes_are_bounded(self):
        self.enqueue()
        job = self.store.claim_next()
        self.store.retry_or_fail(job, RuntimeError("broken"), max_attempts=1)
        first = self.store.claim_failure_notification(max_attempts=2)
        self.assertTrue(self.store.finish_failure_notification(first, error=RuntimeError("mail down"), max_attempts=2))
        self.assertIsNone(self.store.claim_failure_notification(max_attempts=2))
        self.store.connection.execute("UPDATE jobs SET failure_notification_next_at=?", (PAST,))
        self.store.connection.commit()
        self.store.close()
        self.store = JobStore(self.path)
        second = self.store.claim_failure_notification(max_attempts=2)
        self.assertEqual(second["failure_notification_started_at"], first["failure_notification_started_at"])
        self.assertFalse(self.store.finish_failure_notification(first, message_id="stale"))
        self.store.connection.execute("UPDATE jobs SET failure_notification_lease_until=?", (PAST,))
        self.store.connection.commit()
        self.assertIsNone(self.store.claim_failure_notification(max_attempts=2))
        saved = self.store.get(second["id"])
        self.assertEqual(saved["failure_notification_status"], "exhausted")
        self.assertEqual(saved["failure_notification_attempts"], 2)

    def test_migration_preserves_legacy_rows_and_is_repeatable(self):
        # A new DB with precisely the pre-notification schema.
        legacy = Path(self.directory.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy)
        original_columns = [row for row in self.store.connection.execute("PRAGMA table_info(jobs)")
                            if not row["name"].startswith("failure_notification_")
                            and row["name"] not in {"send_started_at", "reply_text", "message_loaded"}]
        definitions = [row["name"] + " " + row["type"] for row in original_columns]
        connection.execute("CREATE TABLE jobs ({})".format(", ".join(definitions)))
        connection.execute("INSERT INTO jobs (id, status, attempts) VALUES ('legacy', 'completed', 2)")
        connection.commit()
        connection.close()
        for _ in range(2):
            store = JobStore(legacy)
            try:
                self.assertEqual(store.get("legacy")["status"], "completed")
                self.assertEqual(store.get("legacy")["failure_notification_attempts"], 0)
                self.assertEqual(store.get("legacy")["message_loaded"], 1)
                if _ == 0:
                    self.assertIsNone(store.get("legacy")["send_started_at"])
                    self.assertIsNone(store.get("legacy")["reply_text"])
                    store.update("legacy", send_started_at=PAST, reply_text="Exact reply\nwith unicode: café")
                else:
                    self.assertEqual(store.get("legacy")["send_started_at"], PAST)
                    self.assertEqual(store.get("legacy")["reply_text"], "Exact reply\nwith unicode: café")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
