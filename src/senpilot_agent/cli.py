"""Command-line entry point for direct retrieval and the email worker."""

from __future__ import annotations

import argparse
import json
import logging
import uuid

from senpilot_agent.config import load_settings
from senpilot_agent.jobs.store import JobStore
from senpilot_agent.models import CATEGORIES
from senpilot_agent.output.archive import create_zip
from senpilot_agent.uarb.supervisor import supervised_scrape_matter
from senpilot_agent.worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Senpilot regulatory document agent")
    commands = parser.add_subparsers(dest="command", required=True)
    scrape = commands.add_parser("scrape", help="Fetch and ZIP one matter without email")
    scrape.add_argument("matter_number")
    scrape.add_argument("document_type", choices=CATEGORIES)
    scrape.add_argument("--headed", action="store_true", help="Show Chromium")
    commands.add_parser("once", help="Poll AgentMail and process one queued job")
    commands.add_parser("run", help="Continuously poll and process jobs")
    commands.add_parser("work", help="Process persisted jobs without polling AgentMail")
    serve = commands.add_parser("serve", help="Receive signed AgentMail webhooks")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("status", help="Show recent job states")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()

    if args.command == "serve":
        import uvicorn

        settings.require_webhook()
        uvicorn.run("senpilot_agent.webhook:create_app", factory=True,
                    host=args.host, port=args.port, workers=1)
        return

    if args.command == "scrape":
        folder = settings.runtime_dir / "manual" / str(uuid.uuid4())
        result = supervised_scrape_matter(
            args.matter_number,
            args.document_type,
            folder / "downloads",
            max_documents=settings.max_documents,
            headless=False if args.headed else settings.headless,
        )
        folder.mkdir(parents=True, exist_ok=True)
        snapshot = folder / "matter.json"
        snapshot.write_text(
            json.dumps(result.to_dict(), indent=2), encoding="utf-8"
        )
        print("Matter snapshot:", snapshot)
        print("Downloaded files:", len(result.saved_files))
        if result.saved_files:
            archive = create_zip(
                result, folder / "{}-documents.zip".format(result.matter_number)
            )
            print("ZIP:", archive)
        return

    if args.command == "status":
        store = JobStore(settings.database_path)
        try:
            print(json.dumps(store.recent(), indent=2))
        finally:
            store.close()
        return

    settings.require_openai()
    worker = Worker(settings)
    try:
        if args.command == "once":
            inserted = worker.ingest_recent()
            print("Queued new requests:", inserted)
            job_id = worker.process_next()
            print("Processed job:", job_id or "none")
        else:
            worker.run_forever(poll_inbox=args.command == "run")
    finally:
        worker.close()


if __name__ == "__main__":
    main()
