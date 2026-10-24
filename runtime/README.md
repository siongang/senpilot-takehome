# Runtime data

The running application will create its SQLite database and one directory per job here by default. Each job directory will hold downloaded files and its final ZIP. The path must be configurable for deployment, where it should be backed by persistent storage until an email is accepted.

Only this README belongs in version control. Do not commit database files, downloaded documents, ZIPs, browser state, or secrets. After successful delivery, remove old artifacts according to a retention rule; do not delete files while a job is queued for a send retry.
