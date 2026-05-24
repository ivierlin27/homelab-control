#!/usr/bin/env bash
# Push the current branch to Forgejo (canonical) then GitHub (backup mirror).
#
# Usage:
#   ./scripts/push-primary-remotes.sh [branch]
#
# Remotes:
#   forgejo — primary (agents, Alienware deploy, Forgejo Actions)
#   origin  — GitHub personal backup (picked up by mirror-sync on Forgejo host)

set -euo pipefail

REPO_DIR="${REPO_DIR:-$(git rev-parse --show-toplevel)}"
cd "${REPO_DIR}"

BRANCH="${1:-$(git branch --show-current)}"
PRIMARY_REMOTE="${PRIMARY_REMOTE:-forgejo}"
BACKUP_REMOTE="${BACKUP_REMOTE:-origin}"

if ! git remote get-url "${PRIMARY_REMOTE}" >/dev/null 2>&1; then
  echo "Primary remote ${PRIMARY_REMOTE} is not configured" >&2
  exit 1
fi

echo "Pushing ${BRANCH} -> ${PRIMARY_REMOTE}"
git push "${PRIMARY_REMOTE}" "HEAD:${BRANCH}"

if git remote get-url "${BACKUP_REMOTE}" >/dev/null 2>&1; then
  echo "Pushing ${BRANCH} -> ${BACKUP_REMOTE}"
  git push "${BACKUP_REMOTE}" "HEAD:${BRANCH}"
else
  echo "Backup remote ${BACKUP_REMOTE} not configured; skipped" >&2
fi

echo "Done. ${PRIMARY_REMOTE}/${BRANCH} is canonical; ${BACKUP_REMOTE} synced."
