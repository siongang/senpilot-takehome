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
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 20.0
    openai_max_retries: int = 2
    agentmail_webhook_secret: str = ""

    @property
    def database_path(self) -> Path:
        return self.runtime_dir / "jobs.sqlite3"

    def require_openai(self) -> None:
        if not self.openai_api_key:
            raise ValueError("Missing parser configuration: OPENAI_API_KEY")

    def require_webhook(self) -> None:
        if not self.agentmail_inbox or not self.agentmail_webhook_secret:
            raise ValueError("Webhook mode requires AGENTMAIL_INBOX and AGENTMAIL_WEBHOOK_SECRET")

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
    openai_timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))
    if not 1 <= openai_timeout <= 60:
        raise ValueError("OPENAI_TIMEOUT_SECONDS must be between 1 and 60")
    openai_retries = int(os.getenv("OPENAI_MAX_RETRIES", "2"))
    if not 0 <= openai_retries <= 3:
        raise ValueError("OPENAI_MAX_RETRIES must be between 0 and 3")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    if not openai_model:
        raise ValueError("OPENAI_MODEL must not be empty")
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
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=openai_model,
        openai_timeout_seconds=openai_timeout,
        openai_max_retries=openai_retries,
        agentmail_webhook_secret=os.getenv("AGENTMAIL_WEBHOOK_SECRET", "").strip(),
    )
