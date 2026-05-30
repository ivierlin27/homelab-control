#!/usr/bin/env bash
# Test D: run fast vLLM on GPU0 (port 8000) + strong llama.cpp on GPU1 (port 8002).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LAB_DIR="${REPO_ROOT}/scripts/model-lab/reap"
CMD="${1:-up}"

FAST_NAME="homelab-reap-dual-fast"
STRONG_NAME="homelab-reap-dual-strong"

down_one() {
  podman rm -f "${FAST_NAME}" "${STRONG_NAME}" 2>/dev/null || true
}

if [[ "${CMD}" == "down" ]]; then
  down_one
  echo "down dual labs"
  exit 0
fi

down_one

# --- Fast (GPU0) ---
# shellcheck disable=SC1090
source "${LAB_DIR}/profiles/dual-fast-gpu0.env"
set -f
FAST_EXTRA=( ${LAB_VLLM_ARGS} )
set +f
podman run -d --name "${FAST_NAME}" --replace \
  --device "nvidia.com/gpu=0" \
  --security-opt=label=disable --ipc=host \
  -p "0.0.0.0:${LAB_PORT}:8000" \
  -v "/mnt/data/hf-cache:/root/.cache/huggingface:Z" \
  -v "/mnt/data/tmp:/tmp:Z" \
  -e HF_HUB_ENABLE_HF_TRANSFER=1 \
  "${LAB_IMAGE}" \
  --host 0.0.0.0 --port 8000 \
  --model "${LAB_MODEL}" \
  --served-model-name "${LAB_SERVED_NAME}" \
  --api-key "${LAB_API_KEY}" \
  "${FAST_EXTRA[@]}"

# --- Strong (GPU1) ---
source "${LAB_DIR}/profiles/dual-strong-glm47-reap-gpu1.env"
MODEL_DIR="$(dirname "${LAB_MODEL_PATH}")"
MODEL_FILE="$(basename "${LAB_MODEL_PATH}")"
set -f
STRONG_EXTRA=( ${LAB_LLAMACPP_ARGS} )
set +f
podman run -d --name "${STRONG_NAME}" --replace \
  --device "nvidia.com/gpu=1" \
  --security-opt=label=disable \
  -p "0.0.0.0:${LAB_PORT}:8080" \
  -v "${MODEL_DIR}:/models:Z" \
  "${LAB_IMAGE}" \
  --host 0.0.0.0 --port 8080 \
  --model "/models/${MODEL_FILE}" \
  --alias "${LAB_SERVED_NAME}" \
  --api-key "${LAB_API_KEY}" \
  "${STRONG_EXTRA[@]}"

echo "Waiting for fast :8000 and strong :8002 ..."
deadline=$(( $(date +%s) + 600 ))
while [[ $(date +%s) -lt $deadline ]]; do
  fast_ok=0 strong_ok=0
  source "${LAB_DIR}/profiles/dual-fast-gpu0.env"
  curl -fsS -m 3 "http://127.0.0.1:8000/v1/models" \
    -H "Authorization: Bearer ${LAB_API_KEY}" >/dev/null 2>&1 && fast_ok=1
  source "${LAB_DIR}/profiles/dual-strong-glm47-reap-gpu1.env"
  curl -fsS -m 3 "http://127.0.0.1:8002/v1/models" \
    -H "Authorization: Bearer ${LAB_API_KEY}" >/dev/null 2>&1 && strong_ok=1
  if [[ $fast_ok -eq 1 && $strong_ok -eq 1 ]]; then
    echo "READY dual: fast=http://127.0.0.1:8000/v1 strong=http://127.0.0.1:8002/v1"
    podman ps --filter "name=homelab-reap-dual"
    exit 0
  fi
  sleep 3
done
echo "TIMEOUT dual launch" >&2
podman logs --tail 80 "${FAST_NAME}" 2>&1 || true
podman logs --tail 80 "${STRONG_NAME}" 2>&1 || true
exit 1
