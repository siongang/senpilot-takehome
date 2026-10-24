#!/usr/bin/env bash
# Run on the Droplet. Includes SQLite WAL and files from the same stopped state.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p backups
backup_path="backups/senpilot-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
compose=(docker compose --env-file .env -f deploy/compose.yaml)

"${compose[@]}" stop web worker
trap '"${compose[@]}" start web worker' EXIT
docker run --rm --mount type=volume,source=senpilot_runtime,target=/data,readonly \
  alpine:3.22 tar -C /data -czf - . > "$backup_path"
test -s "$backup_path"
echo "Backup saved: $backup_path"
echo "Copy this file off the Droplet; a local backup does not protect against server loss."
