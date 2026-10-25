# Runtime data

The running application creates its SQLite database and one directory per job here by default. Each job directory holds downloaded files and its final ZIP. `SENPILOT_RUNTIME_DIR` sets the path for deployment, where it needs persistent storage until an email is accepted.

Only this README belongs in version control. Do not commit database files, downloaded documents, ZIPs, browser state, or secrets. The Droplet cleanup leaves files in place while a job can still send, including send retries and `delivery_review`, and removes `downloads-attempt-*` and `archive-attempt-*` directories for finished jobs after seven days.
