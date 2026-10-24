"""Small AgentMail REST adapter for inbox polling and threaded replies."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


INLINE_REQUEST_LIMIT = 5_500_000  # Leave headroom under AgentMail's 6 MB limit.


class AgentMailError(RuntimeError):
    pass


class AttachmentTooLarge(AgentMailError):
    pass


class AgentMailClient:
    def __init__(self, api_key: str, inbox_id: str, base_url: str) -> None:
        self.api_key = api_key
        self.inbox_id = inbox_id
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[dict] = None,
        payload: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            # Documented for send/reply; identical-payload retries are safe for 24h.
            # Worker persists text/ZIP and refuses automatic retries after 23h.
            headers["Idempotency-Key"] = idempotency_key
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=40) as response:
                body = response.read()
        except HTTPError as error:
            detail = error.read(500).decode("utf-8", "replace")
            raise AgentMailError(
                "AgentMail returned HTTP {}: {}".format(error.code, detail)
            ) from error
        except URLError as error:
            raise AgentMailError("AgentMail connection failed: " + str(error.reason)) from error
        return json.loads(body) if body else {}

    @property
    def _messages_path(self) -> str:
        return "/inboxes/{}/messages".format(quote(self.inbox_id, safe=""))

    def list_recent(self, *, after: str = "", pages: int = 3) -> List[dict]:
        messages = []
        page_token = ""
        for _ in range(pages):
            query: Dict[str, object] = {"limit": 100}
            if after:
                query["after"] = after
            if page_token:
                query["page_token"] = page_token
            response = self._request("GET", self._messages_path, query=query)
            messages.extend(response.get("messages", []))
            page_token = response.get("next_page_token") or ""
            if not page_token:
                break
        return sorted(messages, key=lambda item: item.get("timestamp", ""))

    def get_message(self, message_id: str) -> dict:
        return self._request(
            "GET",
            self._messages_path + "/" + quote(message_id, safe=""),
        )

    def reply(
        self,
        message_id: str,
        text: str,
        *,
        zip_path: Optional[Path] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        payload: dict = {"text": text, "reply_all": False}
        if zip_path is not None:
            if not zip_path.is_file():
                raise FileNotFoundError(zip_path)
            payload["attachments"] = [
                {
                    "content": base64.b64encode(zip_path.read_bytes()).decode("ascii"),
                    "filename": zip_path.name,
                    "content_type": "application/zip",
                }
            ]
        if len(json.dumps(payload).encode("utf-8")) > INLINE_REQUEST_LIMIT:
            raise AttachmentTooLarge(
                "ZIP exceeds the safe inline AgentMail attachment limit"
            )
        return self._request(
            "POST",
            self._messages_path
            + "/"
            + quote(message_id, safe="")
            + "/reply",
            payload=payload,
            idempotency_key=idempotency_key,
        )
