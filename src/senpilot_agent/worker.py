"""Join inbox polling, durable jobs, UARB retrieval, and email replies."""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

from senpilot_agent.config import Settings
from senpilot_agent.email.agentmail import AgentMailClient
from senpilot_agent.email.message import message_text
from senpilot_agent.jobs.store import JobStore
from senpilot_agent.models import MatterResult, split_categories, utc_now
from senpilot_agent.output.archive import create_zip
from senpilot_agent.output.response import (
    format_clarification, format_empty, format_failure, format_not_found, format_result,
)
from senpilot_agent.requests.parser import ClarificationContext, RequestParsingError, parse_request
from senpilot_agent.uarb.browser import MatterNotFound, scrape_matter


LOG = logging.getLogger(__name__)
LEASE_SECONDS = 1200
HEARTBEAT_SECONDS = 60


class LeaseLost(RuntimeError):
    """A newer attempt owns this job; this worker must stop."""


class DeliveryNeedsReview(RuntimeError):
    """The provider's 24-hour idempotency window can no longer protect a retry."""


class Worker:
    def __init__(self, settings: Settings) -> None:
        settings.require_email()
        self.settings = settings
        self.store = JobStore(settings.database_path)
        self.mail = AgentMailClient(
            settings.agentmail_api_key, settings.agentmail_inbox, settings.agentmail_base_url,
        )

    def close(self) -> None:
        self.store.close()

    def _ignore_message(self, message: dict) -> bool:
        sender = parseaddr(message.get("from", ""))[1].lower()
        labels = {str(label).lower() for label in message.get("labels", [])}
        headers = {str(key).lower(): str(value).lower()
                   for key, value in (message.get("headers") or {}).items()}
        return (
            sender == self.settings.agentmail_inbox.lower()
            or bool(labels & {"sent", "draft", "drafts"})
            or headers.get("auto-submitted", "no") != "no"
            or "x-autoreply" in headers or "x-autorespond" in headers
        )

    def ingest_recent(self) -> int:
        inserted = 0
        for summary in self.mail.list_recent(after=self.settings.poll_after):
            message_id = summary.get("message_id")
            if not message_id:
                continue
            try:
                if self.store.has_message(self.settings.agentmail_inbox, message_id):
                    continue
                if self._ignore_message(summary):
                    continue
                message = self.mail.get_message(message_id)
                if self._ignore_message(message):
                    continue
                message["inbox_id"] = self.settings.agentmail_inbox
                if self.store.enqueue_unparsed(message):
                    inserted += 1
            except Exception:
                # A bad message must not prevent intake of independent requests.
                LOG.exception("Could not ingest message %s", message_id)
        return inserted

    @contextmanager
    def _heartbeat(self, job: dict):
        stop, lost = threading.Event(), threading.Event()

        def renew():
            store = None
            try:
                # sqlite connections are owned by the thread that opens them.
                store = JobStore(self.settings.database_path)
                while not stop.wait(HEARTBEAT_SECONDS):
                    if not store.renew_lease(job["id"], job["attempts"], LEASE_SECONDS):
                        lost.set()
                        return
            except Exception:
                LOG.exception("Lease heartbeat failed for job %s", job["id"])
                lost.set()
            finally:
                if store is not None:
                    store.close()

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield lost
        finally:
            stop.set()
            thread.join(timeout=35)

    def _update(self, job: dict, **fields) -> None:
        if not self.store.update_claimed(job["id"], job["attempts"], **fields):
            raise LeaseLost(job["id"])
        job.update(fields)

    def _send(self, job: dict, body: str, zip_path: Optional[Path], lost) -> None:
        if lost.is_set():
            raise LeaseLost(job["id"])
        # Persist the exact text and first-send time before the irreversible call.
        # AgentMail retries require an identical payload and expire after 24 hours.
        started = job.get("send_started_at")
        if started and datetime.now(timezone.utc) - datetime.fromisoformat(started) >= timedelta(hours=23):
            raise DeliveryNeedsReview("Delivery outcome needs operator review: idempotency window nearly expired")
        if not started:
            self._update(job, reply_text=body, send_started_at=utc_now(), status="sending")
        else:
            body = job["reply_text"]
            self._update(job, status="sending")
        response = self.mail.reply(job["message_id"], body, zip_path=zip_path,
                                   idempotency_key=job["id"])
        self._update(job, status="completed", outgoing_message_id=response["message_id"],
                     lease_until=None, last_error=None)

    def process_next(self) -> Optional[str]:
        job = self.store.claim_next(lease_seconds=LEASE_SECONDS)
        if job is None:
            return None
        job_id = job["id"]
        try:
            with self._heartbeat(job) as lost:
                if job["status"] == "parsing":
                    if not job["message_loaded"]:
                        message = self.mail.get_message(job["message_id"])
                        if message.get("message_id") != job["message_id"]:
                            raise RuntimeError("Provider returned a different incoming message")
                        if message.get("inbox_id", job["inbox_id"]) != job["inbox_id"]:
                            raise RuntimeError("Provider returned a different inbox")
                        if self._ignore_message(message):
                            self._update(job, status="ignored", lease_until=None)
                            return job_id
                        self._update(
                            job, message_loaded=1, sender=message.get("from") or "",
                            subject=message.get("subject") or "", body=message_text(message),
                            thread_id=message.get("thread_id"),
                        )
                    prior = self.store.prior_clarification(job)
                    if prior:
                        parsed = parse_request(
                            job["subject"], job["body"],
                            context=ClarificationContext(
                                prior["subject"], prior["body"], prior["parse_error"],
                                prior["matter_number"] or "", prior["requested_category"] or "",
                            ),
                        )
                    else:
                        parsed = parse_request(job["subject"], job["body"])
                    if not self.store.save_parse_result(job_id, job["attempts"], parsed):
                        raise LeaseLost(job_id)
                    job = self.store.get(job_id)
                if job["parse_error"]:
                    self._send(job, format_clarification(job["parse_error"], job.get("sender") or ""), None, lost)
                    return job_id

                # Resume a saved text-only response without searching again or
                # changing the payload after an uncertain email delivery.
                if job.get("send_started_at") and not job["result_json"] and not job["zip_path"]:
                    self._send(job, job["reply_text"], None, lost)
                    return job_id

                folder = self.settings.runtime_dir / "jobs" / job_id
                if job["result_json"]:
                    result = MatterResult.from_dict(json.loads(job["result_json"]))
                else:
                    self._update(job, status="downloading")
                    try:
                        result = scrape_matter(
                            job["matter_number"], job["requested_category"], folder / ("downloads-attempt-{}".format(job["attempts"])),
                            max_documents=self.settings.max_documents, headless=self.settings.headless,
                        )
                    except MatterNotFound:
                        self._send(job, format_not_found(job["matter_number"], job.get("sender") or ""), None, lost)
                        return job_id
                    if not result.saved_files and any(item.status == "failed" for item in result.documents):
                        raise RuntimeError("No selected public documents could be downloaded")
                    self._update(job, result_json=json.dumps(result.to_dict()), status="packaging")

                sender = job.get("sender") or ""
                categories = split_categories(result.requested_category)
                if not categories:
                    self._send(job, format_result(
                        result, 0, sender, attachment_line="",
                        ask_for_documents=not bool(job.get("summary_only")),
                    ), None, lost)
                    return job_id
                zip_path = Path(job["zip_path"]) if job["zip_path"] else None
                if zip_path is None or not zip_path.is_file():
                    if job.get("send_started_at"):
                        raise DeliveryNeedsReview("Saved email attachment missing; delivery outcome needs operator review")
                    slug = "-".join(name.lower().replace(" ", "-") for name in categories)
                    zip_path = create_zip(result, folder / ("archive-attempt-{}".format(job["attempts"])) / "{}-{}.zip".format(
                        result.matter_number, slug))
                    self._update(job, zip_path=str(zip_path))
                body = (format_result(result, len(result.saved_files), sender)
                        if result.saved_files else format_empty(result, sender))
                self._send(job, body, zip_path, lost)
        except LeaseLost:
            LOG.warning("Stopped stale job attempt %s", job_id)
        except DeliveryNeedsReview as error:
            LOG.error("Job %s: %s", job_id, error)
            # Hold for manual reconciliation; do not send a misleading failure
            # reply when the original email might already have been delivered.
            try:
                self._update(job, status="delivery_review", lease_until=None, last_error=str(error))
            except LeaseLost:
                pass
        except Exception as error:
            LOG.exception("Job %s failed", job_id)
            if isinstance(error, RequestParsingError) and not error.retryable:
                try:
                    self._update(job, status="failed", lease_until=None, next_attempt_at=None, last_error=str(error))
                except LeaseLost:
                    pass
            else:
                self.store.retry_or_fail(job, error)
        return job_id

    def process_failure_notification(self) -> Optional[str]:
        job = self.store.claim_failure_notification()
        if job is None:
            return None
        try:
            # The timestamp is durable and also bounds crash recovery beyond the
            # provider idempotency window; never blindly resend an old notice.
            started = job.get("failure_notification_started_at")
            if started and datetime.now(timezone.utc) - datetime.fromisoformat(started) >= timedelta(hours=23):
                raise DeliveryNeedsReview("Failure notification needs operator review: idempotency window nearly expired")
            response = self.mail.reply(job["message_id"], format_failure(job),
                                       idempotency_key=job["id"] + "-failure")
            self.store.finish_failure_notification(job, message_id=response["message_id"])
        except Exception as error:
            LOG.exception("Failure notification for %s failed", job["id"])
            self.store.finish_failure_notification(job, error=error,
                max_attempts=job["failure_notification_attempts"] if isinstance(error, DeliveryNeedsReview) else 3)
        return job["id"]

    def run_forever(self, *, poll_inbox: bool = True) -> None:
        LOG.info("Worker started for inbox %s", self.settings.agentmail_inbox)
        while True:
            try:
                if poll_inbox:
                    try:
                        inserted = self.ingest_recent()
                        if inserted:
                            LOG.info("Queued %s new request(s)", inserted)
                    except Exception:
                        LOG.exception("Inbox poll failed; processing persisted jobs")
                while self.process_next() is not None:
                    pass
                while self.process_failure_notification() is not None:
                    pass
            except Exception:
                LOG.exception("Worker poll failed")
            time.sleep(self.settings.poll_seconds if poll_inbox else 2)
