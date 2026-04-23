#!/usr/bin/env bash
# Generate a new agent SSH keypair and print the next manual/API bootstrap steps.

set -euo pipefail

ACCOUNT_NAME="${1:-}"
KEY_DIR="${2:-$HOME/.ssh/homelab-agents}"

if [[ -z "${ACCOUNT_NAME}" ]]; then
  echo "usage: $0 <agent-account-name> [key-dir]" >&2
  exit 1
fi

mkdir -p "${KEY_DIR}"
chmod 700 "${KEY_DIR}"

KEY_PATH="${KEY_DIR}/${ACCOUNT_NAME}"
if [[ -e "${KEY_PATH}" || -e "${KEY_PATH}.pub" ]]; then
  echo "Refusing to overwrite existing key: ${KEY_PATH}" >&2
  exit 1
fi

ssh-keygen -t ed25519 -C "${ACCOUNT_NAME}" -f "${KEY_PATH}" -N ""

cat <<EOF
Generated:
  private: ${KEY_PATH}
  public:  ${KEY_PATH}.pub

Next steps:
1. Create Forgejo user ${ACCOUNT_NAME} (or API token / machine user).
2. Add the public key to that user.
3. Store the private key in the machine secret manager under the matching runtime scope.
4. Map the account in config/memory/principals.yaml and repo ACLs.
EOF
