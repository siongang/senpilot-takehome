# Acceptance checks

Run `.venv/bin/python -m unittest discover -s tests -v`. Tests mock OpenAI, browser interactions, and email delivery; they require no credentials or network access. The job and worker suites use real temporary SQLite databases.

- `test_core.py`: deduplication and valid empty ZIPs.
- `test_parser_openai.py`: structured extraction, ambiguity, local validation, API failure classification, and configuration.
- `test_scraper.py`: bounded retries, atomic download cleanup, public-only traversal, size/file limits, zero counts, and diagnostics.
- `test_jobs.py`: additive migration, reopen persistence, competing claims, lease fencing, crash recovery, retry exhaustion, and separate failure notifications.
- `test_worker.py`: durable intake/parsing, threaded lease renewal, clarification, terminal failures, exact send retries, and expired-idempotency holds.

Live-site and AgentMail smoke runs are recorded in the root [README](../README.md#current-behavior-and-limits). The changed worker still needs a fresh external email acceptance test.

The most important verification is a live M12205 browser run that captures a PDF from the Download Files dialog, saves it, closes the dialog, and downloads another row. Check the five counts and matter metadata against the page at run time. Then inspect a ZIP and one PDF inside it.

After AgentMail is connected, send a request from an independent mailbox and verify the threaded reply, ZIP attachment, counts, and summary. Also check a category with fewer than ten documents, a zero-count category, duplicate inbound delivery, and a failed download or send. Prioritize these checks by the remaining assignment time.

See the [verification checklist](../BUILD_PLAN.md#verification-and-demo).
