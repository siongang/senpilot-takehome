#!/usr/bin/env bash
# Installed root-owned at /usr/local/sbin/senpilot-actions-ssh.
# A dedicated authorized_keys entry forces every Actions connection here.
set -euo pipefail
umask 077

if [[ ! ${SSH_ORIGINAL_COMMAND:-} =~ ^(upload|deploy)\ ([0-9a-f]{40})$ ]]; then
  echo 'Only upload <commit> and deploy <commit> are allowed.' >&2
  exit 1
fi
operation=${BASH_REMATCH[1]}
revision=${BASH_REMATCH[2]}
base=/opt/senpilot
install -d -m 700 "$base/incoming"

if [[ $operation == upload ]]; then
  temporary=$(mktemp "$base/incoming/.upload.XXXXXX")
  trap 'rm -f "$temporary"' EXIT
  # This source-only repository is small; reject oversized uploads.
  head -c 52428801 > "$temporary"
  test "$(stat -c %s "$temporary")" -le 52428800
  test -s "$temporary"
  mv "$temporary" "$base/incoming/$revision.tar.gz"
else
  # flock inside release.sh also prevents overlapping manual deployments.
  exec /usr/local/sbin/senpilot-release "$revision" "$base/incoming/$revision.tar.gz"
fi
