#!/usr/bin/env bash
# Download GGUF and HF artifacts for the REAP benchmark matrix.
set -euo pipefail

export HF_HOME="${HF_HOME:-/mnt/data/hf-cache}"
unset HF_HUB_ENABLE_HF_TRANSFER
GGUF_DIR="${GGUF_DIR:-/mnt/data/models/gguf}"
mkdir -p "${HF_HOME}" "${GGUF_DIR}"

# Fedora's /usr/bin/huggingface-cli runs `python3 -sP`, which ignores user site-packages
# and cannot see hf_xet. Use a normal interpreter so Xet-backed GGUF pulls are fast.
HF_PYTHON="${HF_PYTHON:-python3.14}"

hf_download() {
  "${HF_PYTHON}" -m huggingface_hub.cli download "$@"
}

download_hf() {
  local repo="$1"
  echo "== hf download ${repo} (${HF_PYTHON}, xet via hf_xet if installed)"
  hf_download "${repo}"
}

download_gguf_file() {
  local repo="$1"
  local file="$2"
  local dest_name="${3:-}"
  echo "== GGUF ${repo} :: ${file}"
  if [[ -n "${dest_name}" ]]; then
    hf_download "${repo}" "${file}" --local-dir "${GGUF_DIR}/${dest_name}"
  else
    hf_download "${repo}" "${file}" --local-dir "${GGUF_DIR}"
  fi
}

# --- Test A: GLM-4.7-Flash-REAP-23B (Unsloth UD-Q4_K_XL) ---
download_gguf_file \
  "unsloth/GLM-4.7-Flash-REAP-23B-A3B-GGUF" \
  "GLM-4.7-Flash-REAP-23B-A3B-UD-Q4_K_XL.gguf" \
  "glm47-flash-reap23-ud-q4kxl"

# Cerebras safetensors for vLLM promotion path
download_hf "cerebras/GLM-4.7-Flash-REAP-23B-A3B"

# --- Test B: Qwen3-Coder-REAP-25B (BF16 for AWQ; GGUF for smoke fallback) ---
download_hf "cerebras/Qwen3-Coder-REAP-25B-A3B"
download_gguf_file \
  "bartowski/cerebras_Qwen3-Coder-REAP-25B-A3B-GGUF" \
  "Qwen3-Coder-REAP-25B-A3B-Q4_K_M.gguf" \
  "qwen3-coder-reap25-q4km"

# --- Test C: GLM-4.5-Air-REAP-82B IQ4_XS (2 shards) ---
REAP_GLM45_DIR="${GGUF_DIR}/glm45air-reap82-iq4xs"
mkdir -p "${REAP_GLM45_DIR}"
for shard in \
  "GLM-4.5-Air-REAP-82B-A12B-IQ4_XS-00001-of-00002.gguf" \
  "GLM-4.5-Air-REAP-82B-A12B-IQ4_XS-00002-of-00002.gguf"; do
  download_gguf_file \
    "bartowski/cerebras_GLM-4.5-Air-REAP-82B-A12B-GGUF" \
    "${shard}" \
    "glm45air-reap82-iq4xs"
done

# --- Test E: Qwen3.6-28B-REAP (0xSero) ---
download_gguf_file \
  "0xSero/Qwen3.6-28B-REAP20-A3B-GGUF" \
  "Qwen3.6-28B-REAP20-A3B-Q4_K_M.gguf" \
  "qwen36-28b-reap-q4km"

# Baseline (if not already present)
download_hf "QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ" || true

echo "Downloads complete. GGUF layout:"
find "${GGUF_DIR}" -maxdepth 2 -name '*.gguf' 2>/dev/null | sort
