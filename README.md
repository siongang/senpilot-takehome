# Senpilot regulatory document agent

Email the agent one or more UARB matter numbers (`M` plus five digits) and any of these document types: Exhibits, Key Documents, Other Documents, Transcripts, or Recordings. The worker creates a separate job for each matter and sends a separate reply. Requested types are downloaded into separate folders in one ZIP, up to ten files per type. A question asking only for matter facts or counts gets a summary without files; a bare matter number also gets a summary and a question asking which document types to retrieve. Types can be shared, as in `Send Key Documents and Exhibits for M12205 and M12345`, or assigned per matter, as in `Send Exhibits for M12205 and Transcripts for M12345`. An explicit request to send all documents selects all five supported types. The [build plan](./BUILD_PLAN.md), [architecture](./ARCHITECTURE.md), and [live-site notes](./UARB_SITE_NOTES.md) explain the scope and observed website behavior.

## Run locally

Requires Python 3.9+ and Playwright Chromium.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m playwright install chromium
cp .env.example .env
```

Set `AGENTMAIL_API_KEY`, `AGENTMAIL_INBOX`, and `OPENAI_API_KEY` in `.env` for email mode. Request parsing uses OpenAI structured outputs; `OPENAI_MODEL` defaults to `gpt-4o-mini`. Timeout and SDK retry limits are configurable in `.env.example`. Keep `.env` private. A direct browser check needs no email or OpenAI credentials:

```sh
.venv/bin/senpilot-agent scrape M12205 'Key Documents'
```

The command prints a path under `runtime/manual/` containing `matter.json`, downloaded files, and a verified ZIP. Set `SENPILOT_MAX_DOCUMENTS` from 1 to 10 to limit a local run. The email worker has two commands:

```sh
.venv/bin/senpilot-agent once
.venv/bin/senpilot-agent run
```

`once` polls the inbox and processes one due job; `run` keeps polling. `senpilot-agent status` shows recent SQLite job states. Set `AGENTMAIL_POLL_AFTER` to a recent UTC timestamp when connecting an existing inbox so older mail is not treated as new requests.

The development process is the worker above; no HTTP server or port is required for polling. Stop it with Ctrl-C. Run automated checks with `.venv/bin/python -m unittest discover -s tests -v`.

If UARB explicitly reports that a matter was not found, the agent replies with that matter number and asks you to check it, without attaching a ZIP. Site loading errors and timeouts are retried instead of being reported as missing matters.

For webhook intake, configure `AGENTMAIL_WEBHOOK_SECRET` and run the receiver and worker in separate terminals using the same runtime directory:

```sh
.venv/bin/senpilot-agent serve --port 8000
.venv/bin/senpilot-agent work
```

Register `POST /webhooks/agentmail` for AgentMail's `message.received` event. The receiver verifies the raw body's Svix signature and durably saves the message identity before returning 204. The worker fetches canonical content and processes the job without polling the inbox. `/healthz` reports HTTP liveness and `/readyz` checks database access. See [DigitalOcean deployment](./deploy/README.md) for Docker Compose, persistent storage, temporary development HTTPS, stable HTTPS, and backups.

GitHub Actions runs tests on pull requests and automatically deploys successful pushes to `main` to the Droplet. Deployment builds and tests a revision-specific image, backs up runtime data, recreates only the web/worker services, and checks readiness. The API keys and persistent database stay on the server. See [deployment setup and recovery](./deploy/README.md#automatic-deployment-from-github).

## Request processing and recovery

Incoming email is persisted before OpenAI parsing. The model extracts requested matter numbers and supported categories for each matter, including all five types for an explicit “all documents” request. Local validation rejects malformed or fabricated matter numbers and incomplete per-matter selections. Missing or ambiguous requests receive clarification; temporary API failures stay in the durable retry queue. OpenAI interprets the email; Playwright performs website navigation and downloads, and the response uses scraped facts.

Expansion into one job per distinct matter is atomic. Duplicate webhook deliveries do not repeat expansion; each job has its own files, attachment budget, retry state, and email idempotency key. All replies stay in the original thread, and a failed matter does not prevent other matters from completing.

SQLite retains job state, bounded attempts, leases, parsed requests, scrape results, and ZIP paths. The scraper separates lookup, metadata extraction, category navigation, row traversal, and validated downloading, with bounded browser/download retries and failure diagnostics under each download directory. A retry during scraping starts that scrape again; individual downloaded rows are not checkpointed across process crashes.

Worker heartbeats renew leases and stale attempts cannot overwrite newer job state. Terminal failures have separate, bounded notification delivery. Send retries reuse the persisted reply text and ZIP with AgentMail's idempotency key. A send whose first attempt is more than 23 hours old is held as `delivery_review` for operator reconciliation rather than resent beyond the provider's documented protection window. Inspect the original thread before manually resolving such a job.

## Current behavior and limits

The browser reads metadata and counts from the UARB matter page, then exports **Save List** PDFs for every nonempty document tab. The PDF handler verifies each report's matter number, category, and row count before deduplicating document/exhibit numbers across the five lists. The email reports this matter-wide document total, including confidential listings. An incomplete or mismatched export fails retrieval and enters the existing retry flow; it never becomes a fabricated total. The reports stay with the job as evidence and are excluded from the requested-document ZIP.

The requested files still follow each row's **GO GET IT → filename → Close** flow. Confidential rows are recorded, but only Public files are downloaded. FileMaker changes generated field IDs between layouts, so the browser module contains the observed alternatives.

AgentMail limits inline attachment requests to 6 MB. The email worker supervises browser retrieval with a conservative 4 MB saved-file budget, a 30-second limit per active file transfer, and a five-minute limit for the complete scrape. If any limit is crossed, it stops the browser and replies without a ZIP, explaining the limit. Other queued emails can then proceed. The sum of the five tab counts remains labeled **category listings**. Save List reports inventory document records, not every binary attachment within a record: M12205 has 62 category listings and 56 distinct document/exhibit numbers in the October 24 exports. Aliases using different numbers cannot be identified as the same underlying file from these lists alone.

On October 24, 2026 the updated application was deployed to a DigitalOcean Droplet with signed AgentMail webhook intake, OpenAI parsing, and a persistent SQLite/files volume. All 66 tests passed inside the server's Docker image. A hosted ten-file Other Documents scrape for `M12205` produced a verified 2.4 MB ZIP. A fresh external Key Documents email completed on its first attempt in approximately 12 seconds and delivered six valid PDFs in a 987 KB ZIP; the recipient confirmed it arrived and opened. Signed duplicate delivery created no additional job or reply. Files survived container replacement, and the completed email job and ZIP survived a service restart. A backup was downloaded off the server.

The multi-matter extension is also deployed. All 81 automated tests pass locally and inside the server image, including independent matter replies, missing-matter responses, send retries, atomic expansion, and migration rollback. A migration rehearsal preserved every field in the three existing hosted jobs; the migrated database passed its integrity check. Live OpenAI checks covered multiple matters, repeated matter numbers, alternatives, and mixed document types. Recipient confirmation of a multi-matter email is still pending.

The hosted development endpoint currently uses a temporary Cloudflare Quick Tunnel. Its URL can change when the tunnel restarts; a stable HTTPS address is still required before final evaluation. Whole-host reboot recovery has not yet been verified. Publishing the latest code, checking evaluator access, and recording the Loom demo also remain.

## Project map

- [src](./src/README.md): installable Python package and component guides.
- [scripts](./scripts/README.md): optional operational helpers.
- [tests](./tests/README.md): acceptance checks.
- [deploy](./deploy/README.md): persistent worker hosting.
- [runtime](./runtime/README.md): ignored local database and per-job files.
