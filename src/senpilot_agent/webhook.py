"""Signed AgentMail intake; long-running work remains in the durable worker."""

import logging
import sqlite3
from typing import Optional

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool
from svix.webhooks import Webhook, WebhookVerificationError

from senpilot_agent.config import Settings, load_settings
from senpilot_agent.jobs.store import JobStore


LOG = logging.getLogger(__name__)
MAX_WEBHOOK_BYTES = 1_048_576


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    settings.require_webhook()
    verifier = Webhook(settings.agentmail_webhook_secret)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def persist(message: dict) -> None:
        store = JobStore(settings.database_path)
        try:
            # Unique (inbox_id, message_id) also deduplicates multiple webhook
            # registrations and a later manual polling reconciliation.
            store.enqueue_reference(message)
        finally:
            store.close()

    def check_database() -> None:
        store = JobStore(settings.database_path)
        try:
            store.connection.execute("SELECT 1").fetchone()
        finally:
            store.close()

    @app.get("/healthz")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def ready() -> Response:
        try:
            check_database()
        except (sqlite3.Error, OSError):
            LOG.exception("Webhook database unavailable")
            return Response(status_code=503)
        return Response(status_code=204)

    @app.post("/webhooks/agentmail")
    async def receive(request: Request) -> Response:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_WEBHOOK_BYTES:
                return Response(status_code=413)
        try:
            event = verifier.verify(bytes(body), dict(request.headers))
        except (WebhookVerificationError, ValueError, TypeError):
            return Response(status_code=400)
        if not isinstance(event, dict):
            return Response(status_code=400)
        if event.get("event_type") != "message.received":
            return Response(status_code=204)
        message = event.get("message")
        if not isinstance(message, dict):
            return Response(status_code=400)
        if message.get("inbox_id") != settings.agentmail_inbox:
            return Response(status_code=204)
        if not isinstance(message.get("message_id"), str) or not message["message_id"].strip():
            return Response(status_code=400)
        try:
            # Do not call OpenAI, fetch email, or launch a browser here. A 2xx is
            # returned only after durable persistence, including duplicate intake.
            await run_in_threadpool(persist, {
                "inbox_id": message["inbox_id"], "message_id": message["message_id"],
            })
        except (sqlite3.Error, OSError):
            LOG.exception("Could not persist webhook job")
            return Response(status_code=503)
        return Response(status_code=204)

    return app
