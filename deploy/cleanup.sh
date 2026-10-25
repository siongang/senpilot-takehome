#!/usr/bin/env bash
# Installed root-owned at /usr/local/sbin/senpilot-cleanup.
# Removes saved UARB downloads and ZIPs for finished jobs. Leaves the database,
# releases, images, and backups alone, and keeps files a job may still send.
set -euo pipefail
umask 077

base=${SENPILOT_BASE:-/opt/senpilot}
job_days=${SENPILOT_JOB_FILE_DAYS:-7}
dry=false
if [[ ${1:-} == --dry-run ]]; then
  dry=true
elif [[ $# -gt 0 ]]; then
  echo 'Usage: senpilot-cleanup [--dry-run]' >&2
  exit 1
fi
[[ $job_days =~ ^[0-9]+$ && $job_days -ge 1 ]] || {
  echo 'SENPILOT_JOB_FILE_DAYS must be at least 1' >&2
  exit 1
}

[[ -d $base ]] || { echo 'Refusing to clean: deployment directory is missing' >&2; exit 1; }
exec 9>"$base/deploy.lock"
if ! flock -n 9; then
  echo 'Cleanup skipped: a deployment holds the lock'
  exit 0
fi

current=''
if [[ -f $base/deployed-revision ]]; then
  current=$(tr -d '[:space:]' < "$base/deployed-revision")
fi
[[ $current =~ ^[0-9a-f]{40}$ ]] || {
  echo 'Refusing to clean: deployed revision is not a commit SHA' >&2
  exit 1
}
image="senpilot-agent:$current"
image_id=$(docker image inspect --format '{{.Id}}' "$image")
for service in senpilot-web-1 senpilot-worker-1; do
  running_image=$(docker inspect --format '{{.Config.Image}}' "$service")
  running_id=$(docker inspect --format '{{.Image}}' "$service")
  [[ $running_image == "$image" && $running_id == "$image_id" ]] || {
    echo "Refusing to clean: $service is not running $image" >&2
    exit 1
  }
done
docker volume inspect senpilot_runtime >/dev/null

echo "Cleanup started $(date -u +%Y-%m-%dT%H:%M:%SZ) dry_run=$dry"
echo "Removing downloads and ZIPs older than $job_days days for finished jobs"
if [[ $dry == true ]]; then
  dry_flag=1
else
  dry_flag=0
fi

docker run --rm -i --network none \
  --mount type=volume,source=senpilot_runtime,target=/data \
  --entrypoint python "$image" - "$job_days" "$dry_flag" /data <<'PY'
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

days = int(sys.argv[1])
dry = sys.argv[2] == "1"
cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
data = Path(sys.argv[3])
database = data / "jobs.sqlite3"
jobs = data / "jobs"
job_id = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
saved = re.compile(r"(downloads|archive)-attempt-\d+")
if not database.is_file():
    raise SystemExit("Refusing to clean: jobs database is missing")
if jobs.is_symlink():
    raise SystemExit("Refusing to clean: jobs directory is a symlink")
if not jobs.is_dir():
    raise SystemExit(0)
connection = sqlite3.connect("file:{}?mode=ro".format(database), uri=True)
connection.row_factory = sqlite3.Row
columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
rows = {row["id"]: row for row in connection.execute("SELECT * FROM jobs")}

def finished(row):
    if (row["updated_at"] or "") >= cutoff:
        return False
    if row["status"] in {"completed", "ignored"}:
        return True
    if row["status"] != "failed" or "failure_notification_status" not in columns:
        return False
    return row["failure_notification_status"] in {"sent", "exhausted"}

for child in jobs.iterdir():
    if child.is_symlink() or not child.is_dir() or job_id.fullmatch(child.name) is None:
        continue
    job_dir = child.resolve()
    if job_dir.parent != jobs.resolve():
        continue
    row = rows.get(child.name)
    if row is None:
        continue
    if finished(row):
        reason = row["status"]
    else:
        continue
    for saved_dir in job_dir.iterdir():
        if saved_dir.is_symlink() or not saved_dir.is_dir() or saved.fullmatch(saved_dir.name) is None:
            continue
        target = saved_dir.resolve()
        if target.parent != job_dir:
            continue
        print("{} {}/{} ({})".format(
            "would remove" if dry else "removing", child.name, saved_dir.name, reason,
        ))
        if not dry:
            shutil.rmtree(target)
PY

echo "Cleanup finished $(date -u +%Y-%m-%dT%H:%M:%SZ)"
