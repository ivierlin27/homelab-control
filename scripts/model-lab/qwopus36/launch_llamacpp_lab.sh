#!/usr/bin/env bash
# Ad-hoc llama.cpp server for GGUF candidates (MTP, MXFP4).
set -euo pipefail

CMD="${1:-up}"
NAME="homelab-lab-llamacpp"
LAB_PORT="${LAB_PORT:-8002}"
GGUF_DIR="${GGUF_DIR:-/mnt/data/models/gguf}"

if [[ "${CMD}" == "down" ]]; then
  podman rm -f "${NAME}" 2>/dev/null || true
  echo "down ${NAME}"
  exit 0
fi

if [[ -z "${LAB_PROFILE:-}" ]]; then
  echo "ERROR: set LAB_PROFILE" >&2
  exit 2
fi
# shellcheck disable=SC1090
source "${LAB_PROFILE}"

: "${LAB_NAME:?}"
: "${LAB_GGUF_PATH:?}"
: "${LAB_SERVED_NAME:?}"
: "${LAB_API_KEY:?}"
LAB_GPUS="${LAB_GPUS:-0,1}"
LAB_CTX="${LAB_CTX:-32768}"
LAB_PARALLEL="${LAB_PARALLEL:-4}"
LAB_IMAGE="${LAB_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-cuda}"
LAB_SPEC_TYPE="${LAB_SPEC_TYPE:-}"
LAB_SPEC_DRAFT_N_MAX="${LAB_SPEC_DRAFT_N_MAX:-}"
LAB_CACHE_TYPE_K="${LAB_CACHE_TYPE_K:-}"
LAB_CACHE_TYPE_V="${LAB_CACHE_TYPE_V:-}"

mkdir -p "${GGUF_DIR}"

LLAMA_EXTRA=()
if [[ -n "${LAB_SPEC_TYPE}" ]]; then
  LLAMA_EXTRA+=(--spec-type "${LAB_SPEC_TYPE}")
fi
if [[ -n "${LAB_SPEC_DRAFT_N_MAX}" ]]; then
  LLAMA_EXTRA+=(--spec-draft-n-max "${LAB_SPEC_DRAFT_N_MAX}")
fi
if [[ -n "${LAB_CACHE_TYPE_K}" ]]; then
  LLAMA_EXTRA+=(--cache-type-k "${LAB_CACHE_TYPE_K}")
fi
if [[ -n "${LAB_CACHE_TYPE_V}" ]]; then
  LLAMA_EXTRA+=(--cache-type-v "${LAB_CACHE_TYPE_V}")
fi

podman rm -f "${NAME}" 2>/dev/null || true

# -ts splits layers across GPUs (e.g. 1,1 for two 3090s).
IFS=',' read -r -a _gpus <<< "${LAB_GPUS}"
TS=""
for _ in "${_gpus[@]}"; do
  TS="${TS},1"
done
TS="${TS#,}"
GPU_DEVICE="nvidia.com/gpu=all"
if [[ "${#_gpus[@]}" -eq 1 ]]; then
  GPU_DEVICE="nvidia.com/gpu=${_gpus[0]}"
fi

echo "+ llama.cpp lab: ${LAB_NAME} gguf=${LAB_GGUF_PATH} gpus=${LAB_GPUS} ts=${TS}"

podman run -d --name "${NAME}" --replace \
  --device "${GPU_DEVICE}" \
  --security-opt=label=disable --ipc=host \
  -p "0.0.0.0:${LAB_PORT}:8080" \
  -v "${GGUF_DIR}:/models:Z" \
  -v /mnt/data/tmp:/tmp:Z \
  "${LAB_IMAGE}" \
  -m "/models/$(basename "${LAB_GGUF_PATH}")" \
  --host 0.0.0.0 --port 8080 \
  -c "${LAB_CTX}" --parallel "${LAB_PARALLEL}" \
  -ngl 99 -ts "${TS}" -fa on --jinja \
  "${LLAMA_EXTRA[@]}" \
  --api-key "${LAB_API_KEY}"

deadline=$(( $(date +%s) + 900 ))
while [[ $(date +%s) -lt $deadline ]]; do
  if curl -fsS -m 3 "http://127.0.0.1:${LAB_PORT}/health" >/dev/null 2>&1 || \
     curl -fsS -m 3 "http://127.0.0.1:${LAB_PORT}/v1/models" \
        -H "Authorization: Bearer ${LAB_API_KEY}" >/dev/null 2>&1; then
    echo "READY: http://127.0.0.1:${LAB_PORT}/v1"
    exit 0
  fi
  sleep 3
done
podman logs --tail 60 "${NAME}" >&2
exit 1
