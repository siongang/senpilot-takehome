"""SQLite-backed queue and durable job progress."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from senpilot_agent.models import utc_now
from senpilot_agent.requests.parser import ParsedRequest


CLAIMABLE = ("queued", "parse_pending", "clarification_pending", "retry_pending")
ACTIVE = ("parsing", "scraping", "downloading", "packaging", "sending")
UPDATABLE = {
    "status", "result_json", "zip_path", "last_error", "outgoing_message_id",
    "next_attempt_at", "lease_until", "matter_number", "requested_category", "parse_error",
    "send_started_at", "reply_text",
}
LIVE_SQL = "('clarification_pending', 'parsing', 'scraping', 'downloading', 'packaging', 'sending')"
DUE_SQL = "('queued', 'parse_pending', 'clarification_pending', 'retry_pending')"


def _deadline(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class JobStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                inbox_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                thread_id TEXT,
                sender TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                matter_number TEXT,
                requested_category TEXT,
                parse_error TEXT,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                lease_until TEXT,
                result_json TEXT,
                zip_path TEXT,
                outgoing_message_id TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(inbox_id, message_id)
            )
            """
        )
        # Additive migration preserves existing databases. Serialize schema checks
        # so concurrent worker starts cannot both try to add the same column.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(jobs)")}
            for name, definition in {
                "send_started_at": "TEXT",
                "reply_text": "TEXT",
                "failure_notification_status": "TEXT",
                "failure_notification_started_at": "TEXT",
                "failure_notification_attempts": "INTEGER NOT NULL DEFAULT 0",
                "failure_notification_next_at": "TEXT",
                "failure_notification_lease_until": "TEXT",
                "failure_notification_message_id": "TEXT",
                "failure_notification_error": "TEXT",
            }.items():
                if name not in columns:
                    self.connection.execute("ALTER TABLE jobs ADD COLUMN {} {}".format(name, definition))
            self.connection.execute("CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, next_attempt_at)")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()

    def get(self, job_id: str) -> Optional[dict]:
        row = self.connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def has_message(self, inbox_id: str, message_id: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM jobs WHERE inbox_id=? AND message_id=?", (inbox_id, message_id)
        ).fetchone() is not None

    def enqueue_unparsed(self, message: dict) -> bool:
        """Persist intake before calling a remote parser, so parsing is retryable."""
        return self.enqueue(message, None)

    def enqueue(self, message: dict, parsed: Optional[ParsedRequest]) -> bool:
        now = utc_now()
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO jobs (
                id, inbox_id, message_id, thread_id, sender, subject, body,
                matter_number, requested_category, parse_error, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), message["inbox_id"], message["message_id"], message.get("thread_id"),
                message.get("from", ""), message.get("subject") or "",
                message.get("extracted_text") or message.get("text") or "",
                parsed.matter_number if parsed else None,
                parsed.document_type if parsed else None,
                parsed.error if parsed else None,
                ("queued" if parsed.valid else "clarification_pending") if parsed else "parse_pending",
                now, now,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def claim_next(self, max_attempts: int = 3, lease_seconds: int = 1200) -> Optional[dict]:
        """Claim one due job, counting crash recovery against its attempt budget."""
        if max_attempts < 1 or lease_seconds < 1:
            raise ValueError("Attempt limit and lease duration must be positive")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """UPDATE jobs SET status='failed', lease_until=NULL, next_attempt_at=NULL,
                    updated_at=?, last_error=COALESCE(last_error, 'Worker lease expired; attempt limit reached')
                    WHERE attempts >= ? AND (
                        (status IN {due} AND (lease_until IS NULL OR lease_until <= ?))
                        OR (status IN {live} AND lease_until <= ?))""".format(due=DUE_SQL, live=LIVE_SQL),
                (now, max_attempts, now, now),
            )
            row = self.connection.execute(
                """SELECT * FROM jobs WHERE attempts < ? AND (
                    (status IN {due} AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                     AND (lease_until IS NULL OR lease_until <= ?))
                    OR (status IN {live} AND lease_until IS NOT NULL AND lease_until <= ?))
                    ORDER BY created_at ASC, id ASC LIMIT 1""".format(due=DUE_SQL, live=LIVE_SQL),
                (max_attempts, now, now, now),
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            status = (
                "clarification_pending" if row["parse_error"]
                else "parsing" if not row["matter_number"] or not row["requested_category"]
                else "sending" if row["zip_path"] or row["result_json"]
                else "scraping"
            )
            self.connection.execute(
                """UPDATE jobs SET status=?, attempts=attempts+1, lease_until=?,
                    next_attempt_at=NULL, updated_at=? WHERE id=?""",
                (status, _deadline(lease_seconds), now, row["id"]),
            )
            claimed = self.get(row["id"])
            self.connection.commit()
            return claimed
        except Exception:
            self.connection.rollback()
            raise

    def renew_lease(self, job_id: str, attempts: int, lease_seconds: int = 1200) -> bool:
        """Extend a live claim. Open a separate JobStore in a heartbeat thread."""
        if lease_seconds < 1:
            raise ValueError("Lease duration must be positive")
        return self.update_claimed(job_id, attempts, lease_until=_deadline(lease_seconds))

    def save_parse_result(self, job_id: str, attempts: int, parsed: ParsedRequest) -> bool:
        """Persist extraction and keep the same live claim for retrieval or clarification."""
        return self.update_claimed(
            job_id, attempts, matter_number=parsed.matter_number,
            requested_category=parsed.document_type, parse_error=parsed.error,
            status="scraping" if parsed.valid else "clarification_pending",
        )

    def _update(self, job_id: str, fields: dict, attempts: Optional[int] = None) -> bool:
        invalid = set(fields) - UPDATABLE
        if invalid:
            raise ValueError("Unsupported job fields: " + ", ".join(sorted(invalid)))
        now = utc_now()
        fields["updated_at"] = now
        assignments = ", ".join("{}=?".format(key) for key in fields)
        condition = "id=?"
        parameters = (*fields.values(), job_id)
        if attempts is not None:
            condition += " AND attempts=? AND lease_until > ? AND status IN " + LIVE_SQL
            parameters += (attempts, now)
        cursor = self.connection.execute(
            "UPDATE jobs SET {} WHERE {}".format(assignments, condition), parameters
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def update_claimed(self, job_id: str, attempts: int, **fields: object) -> bool:
        """Return false if this attempt lost its lease or a newer claim owns the job."""
        return self._update(job_id, fields, attempts)

    def update(self, job_id: str, **fields: object) -> None:
        self._update(job_id, fields)

    def retry_or_fail(self, job: dict, error: Exception, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("Attempt limit must be positive")
        if job["attempts"] >= max_attempts:
            self.update_claimed(job["id"], job["attempts"], status="failed", lease_until=None,
                                next_attempt_at=None, last_error=str(error)[:1000])
        else:
            self.update_claimed(
                job["id"], job["attempts"], status="retry_pending", lease_until=None,
                next_attempt_at=_deadline(30 * 2 ** max(0, job["attempts"] - 1)),
                last_error=str(error)[:1000],
            )

    def claim_failure_notification(
        self, max_attempts: int = 3, lease_seconds: int = 1200
    ) -> Optional[dict]:
        """Claim a failed job's separate, bounded notification delivery."""
        if max_attempts < 1 or lease_seconds < 1:
            raise ValueError("Attempt limit and lease duration must be positive")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """UPDATE jobs SET failure_notification_status='exhausted',
                    failure_notification_lease_until=NULL, updated_at=?
                    WHERE status='failed' AND failure_notification_attempts >= ?
                        AND (failure_notification_status IS NULL OR failure_notification_status IN ('pending', 'sending'))
                        AND (failure_notification_lease_until IS NULL OR failure_notification_lease_until <= ?)""",
                (now, max_attempts, now),
            )
            row = self.connection.execute(
                """SELECT * FROM jobs WHERE status='failed' AND failure_notification_attempts < ?
                    AND (failure_notification_status IS NULL OR failure_notification_status IN ('pending', 'sending'))
                    AND (failure_notification_next_at IS NULL OR failure_notification_next_at <= ?)
                    AND (failure_notification_lease_until IS NULL OR failure_notification_lease_until <= ?)
                    ORDER BY created_at, id LIMIT 1""",
                (max_attempts, now, now),
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            self.connection.execute(
                """UPDATE jobs SET failure_notification_status='sending',
                    failure_notification_attempts=failure_notification_attempts+1,
                    failure_notification_started_at=COALESCE(failure_notification_started_at, ?),
                    failure_notification_lease_until=?, failure_notification_next_at=NULL,
                    updated_at=? WHERE id=?""",
                (now, _deadline(lease_seconds), now, row["id"]),
            )
            claimed = self.get(row["id"])
            self.connection.commit()
            return claimed
        except Exception:
            self.connection.rollback()
            raise

    def finish_failure_notification(
        self, job: dict, message_id: Optional[str] = None,
        error: Optional[Exception] = None, max_attempts: int = 3,
    ) -> bool:
        """Record notification delivery without changing the failed retrieval state."""
        if max_attempts < 1 or (error is None and not message_id):
            raise ValueError("Provide a message ID or delivery error and positive attempt limit")
        attempts = job["failure_notification_attempts"]
        status = "sent" if error is None else "exhausted" if attempts >= max_attempts else "pending"
        now = utc_now()
        cursor = self.connection.execute(
            """UPDATE jobs SET failure_notification_status=?, failure_notification_message_id=?,
                failure_notification_error=?, failure_notification_lease_until=NULL,
                failure_notification_next_at=?, updated_at=?
                WHERE id=? AND status='failed' AND failure_notification_status='sending'
                    AND failure_notification_attempts=? AND failure_notification_lease_until > ?""",
            (status, message_id, str(error)[:1000] if error else None,
             _deadline(30 * 2 ** max(0, attempts - 1)) if status == "pending" else None,
             now, job["id"], attempts, now),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def recent(self, limit: int = 20) -> list:
        return [dict(row) for row in self.connection.execute(
            """SELECT id, matter_number, requested_category, status, attempts,
                outgoing_message_id, last_error, created_at, updated_at
                FROM jobs ORDER BY created_at DESC LIMIT ?""", (limit,),
        )]
