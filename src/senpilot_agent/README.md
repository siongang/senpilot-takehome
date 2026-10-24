# Agent package and worker

This is the application's composition layer. `cli.py` exposes direct scraping and email-worker commands; `worker.py` connects the inbox, SQLite queue, browser, ZIP builder, and reply adapter. `config.py` reads environment settings, while `models.py` defines the shared matter and document records.

## Responsibilities

- Define shared records for a request, matter snapshot, five category counts, selected documents, saved files, and job outcome.
- Validate required environment settings without printing secrets.
- Poll the configured AgentMail inbox, persist unseen inbound messages, and claim the next due job.
- Move a job through queued, scraping, downloading, packaging, sending, and completed. Record retry_pending or failed when needed.
- Expose a CLI path that runs the browser and packaging steps without email, using the same component interfaces as the worker.

The worker stores the scrape result and ZIP path so a failed email send can reuse its archive. A reply is complete after AgentMail accepts it and the outgoing message ID is stored. A live inbound request reached this state on October 24, 2026; recipient-side delivery confirmation is pending.

Component guides: [email](./email/README.md), [requests](./requests/README.md), [jobs](./jobs/README.md), [uarb](./uarb/README.md), and [output](./output/README.md).
