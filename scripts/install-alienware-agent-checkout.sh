#!/usr/bin/env bash
set -euo pipefail

TARGET_ROOT="${1:-${HOME}/git/homelab-control}"
REPO_URL="${2:-https://github.com/ivierlin27/homelab-control.git}"
FORGEJO_REMOTE_URL="${FORGEJO_REMOTE_URL:-ssh://git@192.168.1.70:2222/kevin/homelab-control.git}"

mkdir -p "$(dirname "${TARGET_ROOT}")"

if [[ ! -d "${TARGET_ROOT}/.git" ]]; then
  git clone "${REPO_URL}" "${TARGET_ROOT}"
else
  git -C "${TARGET_ROOT}" fetch origin
  git -C "${TARGET_ROOT}" checkout main
  git -C "${TARGET_ROOT}" pull --ff-only origin main
fi

if ! git -C "${TARGET_ROOT}" remote get-url forgejo >/dev/null 2>&1; then
  git -C "${TARGET_ROOT}" remote add forgejo "${FORGEJO_REMOTE_URL}"
else
  git -C "${TARGET_ROOT}" remote set-url forgejo "${FORGEJO_REMOTE_URL}"
fi

git -C "${TARGET_ROOT}" remote -v
git -C "${TARGET_ROOT}" rev-parse HEAD
