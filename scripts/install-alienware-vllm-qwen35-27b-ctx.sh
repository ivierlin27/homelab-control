#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
CONFIG_DIR="${HOME}/.config/homelab-control"

mkdir -p "${SYSTEMD_USER_DIR}" "${CONFIG_DIR}" "${HOME}/.cache/huggingface"

if [[ ! -f "${CONFIG_DIR}/vllm-qwen35-27b-ctx.env" ]]; then
  cat > "${CONFIG_DIR}/vllm-qwen35-27b-ctx.env" <<'EOF'
# Optional long-context lab: Qwen3.5-27B AWQ on a single RTX 3090 (24 GB).
# Not wired into LiteLLM or set-alienware-model-mode by default.
# HF model id (QuantTrio AWQ; base weights are Qwen/Qwen3.5-27B — there is no public "Qwen3.6-27B" id).
VLLM_Q35_CTX_MODEL=QuantTrio/Qwen3.5-27B-AWQ
VLLM_Q35_CTX_SERVED_MODEL_NAME=homelab-qwen35-27b-ctx-vllm
VLLM_Q35_CTX_API_KEY=replace-me
VLLM_Q35_CTX_DTYPE=half
# Push context after first successful boot by raising this (49152 -> 65536 -> 81920) until VRAM OOM at load.
VLLM_Q35_CTX_MAX_MODEL_LEN=65536
VLLM_Q35_CTX_GPU_MEMORY_UTILIZATION=0.86
VLLM_Q35_CTX_KV_CACHE_DTYPE=fp8_e4m3
VLLM_Q35_CTX_MAX_NUM_BATCHED_TOKENS=4096
VLLM_Q35_CTX_MAX_NUM_SEQS=2
# Qwen3 tool XML: qwen3_coder. Omit --reasoning-parser unless you need split reasoning;
# v0.19.x can drop tool calls when reasoning and tools interact (see docs).
VLLM_Q35_CTX_EXTRA_ARGS=--trust-remote-code --enable-auto-tool-choice --tool-call-parser qwen3_coder
EOF
  chmod 600 "${CONFIG_DIR}/vllm-qwen35-27b-ctx.env"
fi

cp "${ROOT_DIR}/systemd/alienware-vllm-qwen35-27b-ctx.service" "${SYSTEMD_USER_DIR}/alienware-vllm-qwen35-27b-ctx.service"

systemctl --user daemon-reload
systemctl --user enable alienware-vllm-qwen35-27b-ctx.service
echo "Installed alienware-vllm-qwen35-27b-ctx.service (not started)."
echo "Edit ${CONFIG_DIR}/vllm-qwen35-27b-ctx.env (API key, optional MAX_MODEL_LEN), then:"
echo "  systemctl --user stop alienware-vllm-fast.service alienware-vllm-strong.service"
echo "  systemctl --user start alienware-vllm-qwen35-27b-ctx.service"
systemctl --user status alienware-vllm-qwen35-27b-ctx.service --no-pager || true
