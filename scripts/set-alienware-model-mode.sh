#!/usr/bin/env bash
set -euo pipefail

FAST_SERVICE="alienware-vllm-fast.service"
STRONG_SERVICE="alienware-vllm-strong.service"
MODE="${1:-status}"

usage() {
  cat <<'EOF'
Usage: ./scripts/set-alienware-model-mode.sh <fast|strong|status>

  fast    Start the vLLM-backed homelab-fast route.
  strong  Start the vLLM-backed homelab-strong route.
  status  Show vLLM service state and current GPU memory usage.
EOF
}

show_status() {
  local fast_state strong_state
  fast_state="$(systemctl --user is-active "${FAST_SERVICE}" 2>/dev/null || true)"
  strong_state="$(systemctl --user is-active "${STRONG_SERVICE}" 2>/dev/null || true)"
  echo "vllm_fast_service=${fast_state:-unknown}"
  echo "vllm_strong_service=${strong_state:-unknown}"

  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader
  else
    echo "nvidia-smi not found"
  fi
}

case "${MODE}" in
  fast)
    systemctl --user stop "${STRONG_SERVICE}" || true
    systemctl --user restart "${FAST_SERVICE}"
    echo "Restarted ${FAST_SERVICE}. homelab-fast now points at vLLM."
    echo "Stopped ${STRONG_SERVICE} first to keep the 3090 in single-model mode."
    show_status
    ;;
  strong)
    systemctl --user stop "${FAST_SERVICE}"
    systemctl --user restart "${STRONG_SERVICE}"
    echo "Restarted ${STRONG_SERVICE}. homelab-strong now points at vLLM."
    echo "Stopped ${FAST_SERVICE} first to keep the 3090 in single-model mode."
    show_status
    ;;
  status)
    show_status
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac
