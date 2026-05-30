#!/usr/bin/env bash
# Pull HF weights / GGUF files needed for the Qwopus36 benchmark matrix.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
export HF_HOME="${HF_HOME:-/mnt/data/hf-cache}"
# Avoid hf_transfer unless explicitly requested (CLI python may differ from python3).
unset HF_HUB_ENABLE_HF_TRANSFER
GGUF_DIR="${GGUF_DIR:-/mnt/data/models/gguf}"
mkdir -p "${HF_HOME}" "${GGUF_DIR}"

download_hf() {
  local repo="$1"
  echo "== huggingface-cli download ${repo}"
  huggingface-cli download "${repo}" --local-dir-use-symlinks False
}

download_gguf() {
  local repo="$1"
  local file="$2"
  echo "== GGUF ${repo} :: ${file}"
  huggingface-cli download "${repo}" "${file}" --local-dir "${GGUF_DIR}" --local-dir-use-symlinks False
}

# vLLM checkpoints
download_hf "Avesed/Qwopus3.6-35B-A3B-v1-int4-mixed"
download_hf "QuantTrio/Qwen3.6-35B-A3B-AWQ"
download_hf "mconcat/Qwopus3.6-27B-v2-AWQ-4bit"
download_hf "QuantTrio/Qwen3.6-27B-AWQ"
download_hf "QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ"

# GGUF (Phase 4 / 2d)
download_gguf "Jackrong/Qwopus3.6-27B-v2-MTP-GGUF" "Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf" || \
  download_gguf "Jackrong/Qwopus3.6-27B-v2-MTP-GGUF" "*Q4_K_M*.gguf" || true
download_gguf "noctrex/Qwopus3.6-35B-A3B-v1-MXFP4_MOE-GGUF" "Qwopus3.6-35B-A3B-v1-MXFP4_MOE_BF16.gguf" || true

echo "Downloads complete. GGUF dir:"
ls -lh "${GGUF_DIR}" 2>/dev/null | tail -20
