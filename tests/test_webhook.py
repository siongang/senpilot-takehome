"""Signed HTTP intake and durable processing, with no external API calls."""

import base64
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from svix.webhooks import Webhook

from senpilot_agent.config import Settings
from senpilot_agent.email.message import message_text
from senpilot_agent.jobs.store import JobStore
from senpilot_agent.requests.parser import ParsedRequest
from senpilot_agent.webhook import MAX_WEBHOOK_BYTES, create_app
from senpilot_agent.worker import Worker


SECRET = "whsec_" + base64.b64encode(b"test-signing-key-for-local-tests").decode()


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.settings = Settings(
            Path(self.folder.name), True, 10, "test", "agent@example.com",
            "https://example.com", 5, "", agentmail_webhook_secret=SECRET,
        )
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.event = {
            "event_type": "message.received", "event_id": "event-1",
            "message": {"inbox_id": "agent@example.com", "message_id": "incoming"},
        }

    def signed(self, event=None, timestamp=None):
        payload = json.dumps(event if event is not None else self.event)
        timestamp = timestamp or datetime.now(timezone.utc)
        return payload, {
            "svix-id": "delivery-1",
            "svix-timestamp": str(int(timestamp.timestamp())),
            "svix-signature": Webhook(SECRET).sign("delivery-1", timestamp, payload),
        }

    def post(self, event=None):
        payload, headers = self.signed(event)
        return self.client.post("/webhooks/agentmail", content=payload, headers=headers)

    def store(self):
        store = JobStore(self.settings.database_path)
        self.addCleanup(store.close)
        return store

    def test_signed_event_persists_before_ack_and_deduplicates(self):
        self.assertEqual(self.post().status_code, 204)
        # A duplicate from a separate registration may have a different event ID.
        self.event["event_id"] = "another-event"
        self.assertEqual(self.post().status_code, 204)
        store = self.store()
        self.assertEqual(len(store.recent()), 1)
        job = store.claim_next()
        self.assertEqual(job["status"], "parsing")
        self.assertEqual(job["message_loaded"], 0)
        self.assertEqual(job["message_id"], "incoming")

    def test_missing_tampered_and_expired_signatures_are_rejected(self):
        payload, headers = self.signed()
        self.assertEqual(self.client.post("/webhooks/agentmail", content=payload).status_code, 400)
        self.assertEqual(self.client.post("/webhooks/agentmail", content=payload + " ", headers=headers).status_code, 400)
        payload, headers = self.signed(timestamp=datetime.now(timezone.utc) - timedelta(hours=1))
        self.assertEqual(self.client.post("/webhooks/agentmail", content=payload, headers=headers).status_code, 400)
        self.assertEqual(self.store().recent(), [])

    def test_other_events_and_inboxes_do_not_create_jobs(self):
        self.event["event_type"] = "message.sent"
        self.assertEqual(self.post().status_code, 204)
        self.event["event_type"] = "message.received"
        self.event["message"]["inbox_id"] = "different@example.com"
        self.assertEqual(self.post().status_code, 204)
        self.assertEqual(self.store().recent(), [])

    def test_malformed_events_and_oversized_payloads_are_rejected(self):
        for event in ({"event_type": "message.received"},
                      {"event_type": "message.received", "message": {"inbox_id": "agent@example.com"}}, []):
            self.assertEqual(self.post(event).status_code, 400)
        self.assertEqual(self.client.post("/webhooks/agentmail", content=b"x" * (MAX_WEBHOOK_BYTES + 1)).status_code, 413)

    def test_database_failure_is_retryable_not_acknowledged(self):
        with patch("senpilot_agent.webhook.JobStore", side_effect=sqlite3.OperationalError("locked")), \
             patch("senpilot_agent.webhook.LOG"):
            self.assertEqual(self.post().status_code, 503)
            self.assertEqual(self.client.get("/readyz").status_code, 503)

    def test_health_and_fail_closed_configuration(self):
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/readyz").status_code, 204)
        with self.assertRaises(ValueError):
            create_app(replace(self.settings, agentmail_webhook_secret=""))

    def test_reference_is_hydrated_inside_worker_before_parsing(self):
        self.post()
        worker = Worker(self.settings)
        self.addCleanup(worker.close)
        worker.mail = Mock()
        worker.mail.get_message.return_value = {
            **self.event["message"], "from": "person@example.com", "thread_id": "thread-1",
            "subject": "Document request", "html": "<p>M12205 Key Documents</p>",
        }
        worker.mail.reply.return_value = {"message_id": "clarification"}
        with patch("senpilot_agent.worker.parse_request", return_value=ParsedRequest(None, None, "Clarify")) as parse:
            job_id = worker.process_next()
        parse.assert_called_once_with("Document request", "M12205 Key Documents")
        job = worker.store.get(job_id)
        self.assertEqual(job["message_loaded"], 1)
        self.assertEqual(job["thread_id"], "thread-1")
        self.assertEqual(job["status"], "completed")

    def test_message_fetch_failure_stays_durable_and_retryable(self):
        self.post()
        worker = Worker(self.settings)
        self.addCleanup(worker.close)
        worker.mail = Mock()
        worker.mail.get_message.side_effect = TimeoutError("provider unavailable")
        with patch("senpilot_agent.worker.parse_request") as parse, patch("senpilot_agent.worker.LOG"):
            job_id = worker.process_next()
        parse.assert_not_called()
        job = worker.store.get(job_id)
        self.assertEqual(job["message_loaded"], 0)
        self.assertEqual(job["status"], "retry_pending")

    def test_automatic_reply_is_ignored_after_canonical_fetch(self):
        self.post()
        worker = Worker(self.settings)
        self.addCleanup(worker.close)
        worker.mail = Mock()
        worker.mail.get_message.return_value = {
            **self.event["message"], "headers": {"Auto-Submitted": "auto-replied"},
        }
        with patch("senpilot_agent.worker.parse_request") as parse:
            job_id = worker.process_next()
        self.assertEqual(worker.store.get(job_id)["status"], "ignored")
        parse.assert_not_called()
        worker.mail.reply.assert_not_called()

    def test_job_only_loop_never_polls_provider(self):
        worker = Worker(self.settings)
        self.addCleanup(worker.close)
        with patch.object(worker, "ingest_recent") as poll, \
             patch.object(worker, "process_next", return_value=None), \
             patch.object(worker, "process_failure_notification", return_value=None), \
             patch("senpilot_agent.worker.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                worker.run_forever(poll_inbox=False)
        poll.assert_not_called()

    def test_html_current_reply_preferred_over_raw_history(self):
        self.assertEqual(message_text({"extracted_html": "<p>M12205</p><script>bad()</script>",
                                       "text": "old quoted request"}), "M12205")
