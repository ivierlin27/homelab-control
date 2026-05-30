#!/usr/bin/env bash
# Download Unsloth MTP GGUFs for speculative-decoding evaluation.
set -euo pipefail

GGUF_DIR="${GGUF_DIR:-/mnt/data/models/gguf}"
mkdir -p "${GGUF_DIR}"
unset HF_HUB_ENABLE_HF_TRANSFER

download_gguf() {
  local repo="$1"
  local file="$2"
  echo "== GGUF ${repo} :: ${file}"
  huggingface-cli download "${repo}" "${file}" \
    --local-dir "${GGUF_DIR}" \
    --local-dir-use-symlinks False
}

# Official Unsloth MTP builds (include prediction heads)
download_gguf "unsloth/Qwen3.6-27B-MTP-GGUF" "Qwen3.6-27B-Q4_K_M.gguf"
download_gguf "unsloth/Qwen3.6-35B-A3B-MTP-GGUF" "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"

echo "MTP downloads complete:"
ls -lh "${GGUF_DIR}"/Qwen3.6-*-Q4_K_M.gguf "${GGUF_DIR}"/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf 2>/dev/null || \
  ls -lh "${GGUF_DIR}"/*MTP* "${GGUF_DIR}"/Qwen3.6-* 2>/dev/null | tail -10
