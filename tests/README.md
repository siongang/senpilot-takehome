# Acceptance checks

`test_core.py` checks ambiguous requests, duplicate incoming-message handling, and the empty ZIP required for a zero-count category. Run it with `.venv/bin/python -m unittest discover -s tests`. Live-site and AgentMail smoke runs are recorded in the root [README](../README.md#current-behavior-and-limits). Re-polling the same inbox produced no duplicate job or reply.

The most important verification is a live M12205 browser run that captures a PDF from the Download Files dialog, saves it, closes the dialog, and downloads another row. Check the five counts and matter metadata against the page at run time. Then inspect a ZIP and one PDF inside it.

After AgentMail is connected, send a request from an independent mailbox and verify the threaded reply, ZIP attachment, counts, and summary. Also check a category with fewer than ten documents, a zero-count category, duplicate inbound delivery, and a failed download or send. Prioritize these checks by the remaining assignment time.

See the [verification checklist](../BUILD_PLAN.md#verification-and-demo).
