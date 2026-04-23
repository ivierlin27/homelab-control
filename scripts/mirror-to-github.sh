#!/usr/bin/env bash
# Mirror the current repo to a backup remote such as GitHub.

set -euo pipefail

REPO_DIR="${1:-$(pwd)}"
REMOTE_NAME="${REMOTE_NAME:-github-backup}"

cd "${REPO_DIR}"

if ! git remote get-url "${REMOTE_NAME}" >/dev/null 2>&1; then
  echo "Remote ${REMOTE_NAME} is not configured" >&2
  exit 1
fi

git push "${REMOTE_NAME}" --mirror
