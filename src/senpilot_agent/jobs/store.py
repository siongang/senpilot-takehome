"""SQLite-backed queue and durable job progress."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from senpilot_agent.models import utc_now
from senpilot_agent.requests.parser import ParsedRequest


CLAIMABLE = ("queued", "clarification_pending", "retry_pending")
ACTIVE = ("scraping", "downloading", "packaging", "sending")
UPDATABLE = {
    "status",
    "result_json",
    "zip_path",
    "last_error",
    "outgoing_message_id",
    "next_attempt_at",
    "lease_until",
}


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
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, next_attempt_at)"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def has_message(self, inbox_id: str, message_id: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM jobs WHERE inbox_id=? AND message_id=?",
            (inbox_id, message_id),
        ).fetchone() is not None

    def enqueue(self, message: dict, parsed: ParsedRequest) -> bool:
        now = utc_now()
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO jobs (
                id, inbox_id, message_id, thread_id, sender, subject, body,
                matter_number, requested_category, parse_error, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                message["inbox_id"],
                message["message_id"],
                message.get("thread_id"),
                message.get("from", ""),
                message.get("subject") or "",
                message.get("extracted_text") or message.get("text") or "",
                parsed.matter_number,
                parsed.document_type,
                parsed.error,
                "queued" if parsed.valid else "clarification_pending",
                now,
                now,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def claim_next(self) -> Optional[dict]:
        now = utc_now()
        lease = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT * FROM jobs
                WHERE (
                    status IN ('queued', 'clarification_pending', 'retry_pending')
                    AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ) OR (
                    status IN ('scraping', 'downloading', 'packaging', 'sending')
                    AND lease_until IS NOT NULL AND lease_until <= ?
                )
                ORDER BY created_at ASC LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            status = (
                "clarification_pending"
                if row["parse_error"]
                else "sending"
                if row["zip_path"] or row["result_json"]
                else "scraping"
            )
            self.connection.execute(
                """
                UPDATE jobs SET status=?, attempts=attempts+1,
                    lease_until=?, updated_at=? WHERE id=?
                """,
                (status, lease, now, row["id"]),
            )
            self.connection.commit()
            return dict(
                self.connection.execute(
                    "SELECT * FROM jobs WHERE id=?", (row["id"],)
                ).fetchone()
            )
        except Exception:
            self.connection.rollback()
            raise

    def update(self, job_id: str, **fields: object) -> None:
        invalid = set(fields) - UPDATABLE
        if invalid:
            raise ValueError("Unsupported job fields: " + ", ".join(sorted(invalid)))
        fields["updated_at"] = utc_now()
        assignments = ", ".join("{}=?".format(key) for key in fields)
        self.connection.execute(
            "UPDATE jobs SET {} WHERE id=?".format(assignments),
            (*fields.values(), job_id),
        )
        self.connection.commit()

    def retry_or_fail(self, job: dict, error: Exception, max_attempts: int = 3) -> None:
        if job["attempts"] >= max_attempts:
            self.update(
                job["id"],
                status="failed",
                lease_until=None,
                last_error=str(error)[:1000],
            )
            return
        retry_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=30 * 2 ** (job["attempts"] - 1))
        ).isoformat()
        self.update(
            job["id"],
            status="retry_pending",
            lease_until=None,
            next_attempt_at=retry_at,
            last_error=str(error)[:1000],
        )

    def recent(self, limit: int = 20) -> list:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT id, matter_number, requested_category, status, attempts,
                    outgoing_message_id, last_error, created_at, updated_at
                FROM jobs ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
        ]
