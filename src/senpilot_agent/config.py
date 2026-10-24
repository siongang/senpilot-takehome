"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    runtime_dir: Path
    headless: bool
    max_documents: int
    agentmail_api_key: str
    agentmail_inbox: str
    agentmail_base_url: str
    poll_seconds: int
    poll_after: str

    @property
    def database_path(self) -> Path:
        return self.runtime_dir / "jobs.sqlite3"

    def require_email(self) -> None:
        missing = []
        if not self.agentmail_api_key:
            missing.append("AGENTMAIL_API_KEY")
        if not self.agentmail_inbox:
            missing.append("AGENTMAIL_INBOX")
        if missing:
            raise ValueError("Missing email configuration: " + ", ".join(missing))


def load_settings() -> Settings:
    load_dotenv()
    runtime_dir = Path(os.getenv("SENPILOT_RUNTIME_DIR", "runtime")).expanduser().resolve()
    max_documents = int(os.getenv("SENPILOT_MAX_DOCUMENTS", "10"))
    if not 1 <= max_documents <= 10:
        raise ValueError("SENPILOT_MAX_DOCUMENTS must be between 1 and 10")
    poll_seconds = int(os.getenv("AGENTMAIL_POLL_SECONDS", "15"))
    if poll_seconds < 5:
        raise ValueError("AGENTMAIL_POLL_SECONDS must be at least 5")
    return Settings(
        runtime_dir=runtime_dir,
        headless=_boolean("SENPILOT_HEADLESS", True),
        max_documents=max_documents,
        agentmail_api_key=os.getenv("AGENTMAIL_API_KEY", ""),
        agentmail_inbox=os.getenv("AGENTMAIL_INBOX", ""),
        agentmail_base_url=os.getenv(
            "AGENTMAIL_BASE_URL", "https://api.agentmail.to/v0"
        ).rstrip("/"),
        poll_seconds=poll_seconds,
        poll_after=os.getenv("AGENTMAIL_POLL_AFTER", ""),
    )
