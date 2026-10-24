"""Join inbox polling, durable jobs, UARB retrieval, and email replies."""

from __future__ import annotations

import json
import logging
import time
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

from senpilot_agent.config import Settings
from senpilot_agent.email.agentmail import AgentMailClient
from senpilot_agent.jobs.store import JobStore
from senpilot_agent.models import MatterResult
from senpilot_agent.output.archive import create_zip
from senpilot_agent.output.response import format_empty, format_result
from senpilot_agent.requests.parser import parse_request
from senpilot_agent.uarb.browser import scrape_matter


LOG = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings: Settings) -> None:
        settings.require_email()
        self.settings = settings
        self.store = JobStore(settings.database_path)
        self.mail = AgentMailClient(
            settings.agentmail_api_key,
            settings.agentmail_inbox,
            settings.agentmail_base_url,
        )

    def close(self) -> None:
        self.store.close()

    def ingest_recent(self) -> int:
        inserted = 0
        for summary in self.mail.list_recent(after=self.settings.poll_after):
            message_id = summary.get("message_id")
            if not message_id:
                continue
            if self.store.has_message(self.settings.agentmail_inbox, message_id):
                continue
            sender = parseaddr(summary.get("from", ""))[1].lower()
            if sender == self.settings.agentmail_inbox.lower():
                continue
            labels = {str(label).lower() for label in summary.get("labels", [])}
            if "sent" in labels or "draft" in labels:
                continue
            message = self.mail.get_message(message_id)
            message["inbox_id"] = self.settings.agentmail_inbox
            parsed = parse_request(
                message.get("subject") or "",
                message.get("extracted_text") or message.get("text") or "",
            )
            if self.store.enqueue(message, parsed):
                inserted += 1
        return inserted

    def process_next(self) -> Optional[str]:
        job = self.store.claim_next()
        if job is None:
            return None
        job_id = job["id"]
        try:
            if job["parse_error"]:
                response = self.mail.reply(
                    job["message_id"],
                    "Hi,\n\n{}\n\nBest,\nRegulatory Agent".format(
                        job["parse_error"]
                    ),
                    idempotency_key=job_id,
                )
                self.store.update(
                    job_id,
                    status="completed",
                    outgoing_message_id=response["message_id"],
                    lease_until=None,
                    last_error=None,
                )
                return job_id

            folder = self.settings.runtime_dir / "jobs" / job_id
            downloads = folder / "downloads"
            if job["result_json"]:
                result = MatterResult.from_dict(json.loads(job["result_json"]))
            else:
                self.store.update(job_id, status="downloading")
                result = scrape_matter(
                    job["matter_number"],
                    job["requested_category"],
                    downloads,
                    max_documents=self.settings.max_documents,
                    headless=self.settings.headless,
                )
                self.store.update(
                    job_id,
                    result_json=json.dumps(result.to_dict()),
                    status="packaging",
                )

            zip_path = Path(job["zip_path"]) if job["zip_path"] else None
            if zip_path is None or not zip_path.is_file():
                zip_path = create_zip(
                    result,
                    folder / "{}-{}.zip".format(
                        result.matter_number,
                        result.requested_category.lower().replace(" ", "-"),
                    ),
                )
                self.store.update(job_id, zip_path=str(zip_path))
            self.store.update(job_id, status="sending")
            body = (
                format_result(result, len(result.successful_documents))
                if result.saved_files
                else format_empty(result)
            )
            response = self.mail.reply(
                job["message_id"],
                body,
                zip_path=zip_path,
                idempotency_key=job_id,
            )
            self.store.update(
                job_id,
                status="completed",
                outgoing_message_id=response["message_id"],
                lease_until=None,
                last_error=None,
            )
            return job_id
        except Exception as error:
            LOG.exception("Job %s failed", job_id)
            self.store.retry_or_fail(job, error)
            return job_id

    def run_forever(self) -> None:
        LOG.info("Worker started for inbox %s", self.settings.agentmail_inbox)
        while True:
            try:
                inserted = self.ingest_recent()
                if inserted:
                    LOG.info("Queued %s new request(s)", inserted)
                while self.process_next() is not None:
                    pass
            except Exception:
                LOG.exception("Worker poll failed")
            time.sleep(self.settings.poll_seconds)
