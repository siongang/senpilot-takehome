"""Bound browser work so a large transfer cannot hold the email queue."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from senpilot_agent.models import CATEGORIES, MatterResult
from senpilot_agent.uarb.browser import MAX_SAVED_BYTES, DownloadLimitExceeded, MatterNotFound


FILE_SECONDS = 30
SCRAPE_SECONDS = 300


def _download_bytes(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        if not path.is_file() or path.relative_to(folder).parts[0] not in CATEGORIES:
            continue
        if path.name.startswith(".") or path.name.startswith("scrape-result.json"):
            continue
        try:
            total += path.stat().st_size
        except FileNotFoundError:
            pass
    return total


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)


def supervised_scrape_matter(
    matter_number: str, requested_category: str, output_dir: Path,
    *, max_documents: int = 10, headless: bool = True,
) -> MatterResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "scrape-result.json"
    result_path.unlink(missing_ok=True)
    command = [
        sys.executable, "-m", "senpilot_agent.uarb.scrape_child",
        matter_number, requested_category, str(output_dir), str(max_documents),
        "1" if headless else "0",
    ]
    try:
        with (output_dir / "scrape-stderr.log").open("wb") as stderr:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=stderr, start_new_session=True,
            )
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if _download_bytes(output_dir) > MAX_SAVED_BYTES:
                        raise DownloadLimitExceeded(
                            "Requested files exceed the email attachment size limit"
                        )
                    now = time.monotonic()
                    for marker in output_dir.rglob(".active-download"):
                        try:
                            age = time.time() - marker.stat().st_mtime
                        except FileNotFoundError:
                            continue
                        if age > FILE_SECONDS:
                            raise DownloadLimitExceeded(
                                "A requested file took too long to download"
                            )
                    if now - started > SCRAPE_SECONDS:
                        raise DownloadLimitExceeded(
                            "The document request took too long to retrieve"
                        )
                    time.sleep(0.1)
                # Check once more after a fast child exits between polling cycles.
                if _download_bytes(output_dir) > MAX_SAVED_BYTES:
                    raise DownloadLimitExceeded(
                        "Requested files exceed the email attachment size limit"
                    )
            finally:
                _stop(process)
    except DownloadLimitExceeded:
        # These bytes cannot be sent, so do not retain a partial large video.
        for category in CATEGORIES:
            shutil.rmtree(output_dir / category, ignore_errors=True)
        raise
    if not result_path.is_file():
        raise RuntimeError("Browser process stopped without a result")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if payload["status"] == "ok":
        return MatterResult.from_dict(payload["result"])
    if payload["status"] == "missing":
        raise MatterNotFound(payload["error"])
    if payload["status"] == "limit":
        raise DownloadLimitExceeded(payload["error"])
    raise RuntimeError(payload.get("error") or "Browser process failed")
