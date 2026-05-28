#!/usr/bin/env bash
# Install Infisical CLI to ~/bin (no sudo). Safe to re-run.
set -euo pipefail

VERSION="${INFISICAL_CLI_VERSION:-0.43.87}"
BIN_DIR="${HOME}/bin"
ARCHIVE="cli_${VERSION}_linux_amd64.tar.gz"
URL="https://github.com/Infisical/cli/releases/download/v${VERSION}/${ARCHIVE}"

mkdir -p "${BIN_DIR}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

curl -fsSL -o "${tmp}/${ARCHIVE}" "${URL}"
tar -xzf "${tmp}/${ARCHIVE}" -C "${tmp}"
if [[ -f "${tmp}/infisical" ]]; then
  install -m 0755 "${tmp}/infisical" "${BIN_DIR}/infisical"
elif [[ -f "${tmp}/cli" ]]; then
  install -m 0755 "${tmp}/cli" "${BIN_DIR}/infisical"
else
  exe="$(find "${tmp}" -maxdepth 2 -type f -executable | head -1)"
  [[ -n "${exe}" ]] || { echo "bootstrap-infisical-cli: binary not found in archive" >&2; exit 1; }
  install -m 0755 "${exe}" "${BIN_DIR}/infisical"
fi

echo "Installed ${BIN_DIR}/infisical"
"${BIN_DIR}/infisical" --version
echo "Add to PATH if needed: export PATH=\"\${HOME}/bin:\${PATH}\""
