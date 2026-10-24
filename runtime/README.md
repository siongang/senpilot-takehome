# Runtime data

The running application creates its SQLite database and one directory per job here by default. Each job directory holds downloaded files and its final ZIP. `SENPILOT_RUNTIME_DIR` sets the path for deployment, where it needs persistent storage until an email is accepted.

Only this README belongs in version control. Do not commit database files, downloaded documents, ZIPs, browser state, or secrets. After successful delivery, remove old artifacts according to a retention rule; do not delete files while a job is queued for a send retry.
