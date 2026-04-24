#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
CONFIG_DIR="${HOME}/.config/homelab-control"

mkdir -p "${SYSTEMD_USER_DIR}" "${CONFIG_DIR}" "${HOME}/.cache/huggingface"

if [[ ! -f "${CONFIG_DIR}/vllm-strong.env" ]]; then
  cat > "${CONFIG_DIR}/vllm-strong.env" <<'EOF'
VLLM_STRONG_MODEL=Qwen/Qwen2.5-14B-Instruct-AWQ
VLLM_STRONG_SERVED_MODEL_NAME=homelab-strong-vllm
VLLM_STRONG_API_KEY=replace-me
VLLM_STRONG_DTYPE=half
VLLM_STRONG_MAX_MODEL_LEN=32768
VLLM_STRONG_GPU_MEMORY_UTILIZATION=0.92
VLLM_STRONG_KV_CACHE_DTYPE=fp8_e4m3
VLLM_STRONG_MAX_NUM_BATCHED_TOKENS=4096
VLLM_STRONG_MAX_NUM_SEQS=8
VLLM_STRONG_SWAP_SPACE_GB=8
VLLM_STRONG_EXTRA_ARGS=--calculate-kv-scales
EOF
  chmod 600 "${CONFIG_DIR}/vllm-strong.env"
fi

cp "${ROOT_DIR}/systemd/alienware-vllm-strong.service" "${SYSTEMD_USER_DIR}/alienware-vllm-strong.service"

systemctl --user daemon-reload
systemctl --user enable alienware-vllm-strong.service
systemctl --user status alienware-vllm-strong.service --no-pager || true
