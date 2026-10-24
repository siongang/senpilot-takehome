#!/usr/bin/env bash
# Installed root-owned at /usr/local/sbin/senpilot-release.
set -euo pipefail
umask 077
revision=${1:?Commit SHA required}
archive=${2:?Source archive required}
base=${3:-/opt/senpilot}
[[ $revision =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid revision' >&2; exit 1; }
test -f "$archive"
test -f "$base/.env"
install -d -m 700 "$base/releases" "$base/backups"
exec 9> "$base/deploy.lock"
flock -w 1200 9

release="$base/releases/$revision"
image="senpilot-agent:$revision"
previous="$base"
if [[ -L $base/current ]]; then
  previous=$(readlink -f "$base/current")
fi
previous_image=$(docker inspect --format '{{.Config.Image}}' senpilot-web-1)

# Extract into a fresh directory. Reject links, traversal, and live-data files
# before root extracts an archive supplied by the Actions runner.
if [[ ! -d $release ]]; then
  staging=$(mktemp -d "$base/releases/.staging.XXXXXX")
  trap 'rm -rf "${staging:-}"' EXIT
  python3 - "$archive" "$staging" <<'PY'
import pathlib
import sys
import tarfile
with tarfile.open(sys.argv[1], 'r:gz') as archive:
    for entry in archive.getmembers():
        path = pathlib.PurePosixPath(entry.name)
        if (path.is_absolute() or '..' in path.parts or not path.parts
                or path.parts[0] in {'.env', '.git', '.venv', 'runtime', 'backups'}
                or not (entry.isfile() or entry.isdir())):
            raise SystemExit('Unsafe release archive member')
    archive.extractall(sys.argv[2])
PY
  for required in deploy/compose.yaml deploy/Dockerfile pyproject.toml tests; do
    test -e "$staging/$required"
  done
  ln -s "$base/.env" "$staging/.env"
  mv "$staging" "$release"
  staging=''
  trap - EXIT
fi

export SENPILOT_IMAGE="$image"
docker compose --env-file "$base/.env" -f "$release/deploy/compose.yaml" config --quiet
docker build --label "org.opencontainers.image.revision=$revision" -t "$image" -f "$release/deploy/Dockerfile" "$release"
docker run --rm --mount "type=bind,source=$release/tests,target=/tests,readonly" --mount "type=bind,source=$release/deploy,target=/deploy,readonly" "$image" python -m unittest discover -s /tests -v

old_compose=(docker compose --env-file "$base/.env" -f "$previous/deploy/compose.yaml")
new_compose=(docker compose --env-file "$base/.env" -f "$release/deploy/compose.yaml")
stopped=false
successful=false
recover() {
  result=$?
  trap - EXIT
  if [[ $stopped == true && $successful == false ]]; then
    echo 'Deployment failed; restarting the previous application image. Runtime data is preserved.' >&2
    SENPILOT_IMAGE="$previous_image" "${old_compose[@]}" up -d --no-build --force-recreate web worker || true
  fi
  exit "$result"
}
trap recover EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

stopped=true
"${old_compose[@]}" stop web worker
backup="$base/backups/senpilot-$(date -u +%Y%m%dT%H%M%SZ)-$revision.tar.gz"
docker run --rm --mount type=volume,source=senpilot_runtime,target=/data,readonly alpine:3.22 tar -C /data -czf - . > "$backup"
test -s "$backup"
echo "Runtime backup: $backup"
"${new_compose[@]}" up -d --no-build --force-recreate --wait --wait-timeout 120 web worker
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/readyz
"${new_compose[@]}" exec -T worker python -c 'from senpilot_agent.config import load_settings; from senpilot_agent.jobs.store import JobStore; s=JobStore(load_settings().database_path); assert s.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"; s.close()'
ln -sfn "$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
printf '%s\n' "$revision" > "$base/deployed-revision"
successful=true
echo "Healthy deployment: $revision"
