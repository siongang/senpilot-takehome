# Acceptance checks

Run `.venv/bin/python -m unittest discover -s tests -v`. Tests mock OpenAI, browser interactions, and email delivery; they require no credentials or network access. The job and worker suites use real temporary SQLite databases.

- `test_core.py`: deduplication and valid empty ZIPs.
- `test_parser_openai.py`: structured extraction, ambiguity, local validation, API failure classification, and configuration.
- `test_scraper.py`: bounded retries, atomic download cleanup, public-only traversal, size/file limits, zero counts, and diagnostics.
- `test_supervisor.py`: isolated browser process results and early termination for oversized or stalled downloads.
- `test_lists.py`: real saved-list PDFs across multiple pages, category/matter/count validation, repeated document-number deduplication, complete inventories, legacy results, and report download cleanup. Public M12205 report fixtures are under `fixtures/uarb-lists/`.
- `test_jobs.py`: additive migration, reopen persistence, competing claims, lease fencing, crash recovery, retry exhaustion, and separate failure notifications.
- `test_worker.py`: durable intake/parsing, threaded lease renewal, clarification, missing-matter replies without attachments, independent matter outcomes, terminal failures, exact send retries, and expired-idempotency holds.
- `test_webhook.py`: real Svix signatures, duplicate/wrong-inbox delivery, request limits, persistence failures, canonical message fetching, HTML-only mail, and queue-only processing.

Live-site and AgentMail smoke runs are recorded in the root [README](../README.md#current-behavior-and-limits). A fresh hosted email acceptance test passed on October 24, 2026.

The most important verification is a live M12205 browser run that captures a PDF from the Download Files dialog, saves it, closes the dialog, and downloads another row. Check the five counts and matter metadata against the page at run time. Then inspect a ZIP and one PDF inside it.

After AgentMail is connected, send a request from an independent mailbox and verify the threaded reply, ZIP attachment, counts, and summary. Also check a category with fewer than ten documents, a zero-count category, duplicate inbound delivery, and a failed download or send. Prioritize these checks by the remaining assignment time.

See the [verification checklist](../BUILD_PLAN.md#verification-and-demo).
