#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
CONFIG_DIR="${HOME}/.config/homelab-control"

mkdir -p "${SYSTEMD_USER_DIR}" "${CONFIG_DIR}" "${HOME}/.cache/huggingface"

if [[ ! -f "${CONFIG_DIR}/vllm-fast.env" ]]; then
  cat > "${CONFIG_DIR}/vllm-fast.env" <<'EOF'
VLLM_FAST_MODEL=Qwen/Qwen2.5-7B-Instruct
VLLM_FAST_SERVED_MODEL_NAME=homelab-fast-vllm
VLLM_FAST_API_KEY=replace-me
VLLM_FAST_DTYPE=half
VLLM_FAST_MAX_MODEL_LEN=32768
VLLM_FAST_GPU_MEMORY_UTILIZATION=0.9
VLLM_FAST_KV_CACHE_DTYPE=auto
VLLM_FAST_MAX_NUM_BATCHED_TOKENS=8192
VLLM_FAST_MAX_NUM_SEQS=16
VLLM_FAST_SWAP_SPACE_GB=8
VLLM_FAST_EXTRA_ARGS=
EOF
  chmod 600 "${CONFIG_DIR}/vllm-fast.env"
fi

cp "${ROOT_DIR}/systemd/alienware-vllm-fast.service" "${SYSTEMD_USER_DIR}/alienware-vllm-fast.service"

systemctl --user daemon-reload
systemctl --user enable --now alienware-vllm-fast.service
systemctl --user status alienware-vllm-fast.service --no-pager
