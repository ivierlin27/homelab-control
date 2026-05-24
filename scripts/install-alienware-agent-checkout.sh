#!/usr/bin/env bash
set -euo pipefail

TARGET_ROOT="${1:-${HOME}/git/homelab-control}"
FORGEJO_REMOTE_URL="${FORGEJO_REMOTE_URL:-ssh://git@192.168.1.70:2222/kevin/homelab-control.git}"
# Forgejo is canonical; GitHub is the backup mirror (see scripts/push-primary-remotes.sh).
REPO_URL="${2:-${FORGEJO_REMOTE_URL}}"

mkdir -p "$(dirname "${TARGET_ROOT}")"

if [[ ! -d "${TARGET_ROOT}/.git" ]]; then
  git clone "${REPO_URL}" "${TARGET_ROOT}"
else
  BRANCH="$(git -C "${TARGET_ROOT}" branch --show-current 2>/dev/null || echo main)"
  git -C "${TARGET_ROOT}" fetch forgejo 2>/dev/null || true
  git -C "${TARGET_ROOT}" fetch origin 2>/dev/null || true
  if git -C "${TARGET_ROOT}" show-ref --verify --quiet "refs/remotes/forgejo/${BRANCH}"; then
    git -C "${TARGET_ROOT}" pull --ff-only forgejo "${BRANCH}"
  else
    git -C "${TARGET_ROOT}" pull --ff-only origin "${BRANCH}"
  fi
fi

if ! git -C "${TARGET_ROOT}" remote get-url forgejo >/dev/null 2>&1; then
  git -C "${TARGET_ROOT}" remote add forgejo "${FORGEJO_REMOTE_URL}"
else
  git -C "${TARGET_ROOT}" remote set-url forgejo "${FORGEJO_REMOTE_URL}"
fi

git -C "${TARGET_ROOT}" remote -v
git -C "${TARGET_ROOT}" rev-parse HEAD
