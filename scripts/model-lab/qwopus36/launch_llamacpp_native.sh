#!/usr/bin/env bash
# Run llama-server natively (built from main) for Qwen3.6 MTP GGUFs.
set -euo pipefail

CMD="${1:-up}"
NAME="homelab-lab-llamacpp-native"
LAB_PORT="${LAB_PORT:-8002}"
PID_FILE="/tmp/${NAME}.pid"
LOG_FILE="/tmp/${NAME}.log"
LLAMA_BIN="${LLAMA_BIN:-/mnt/data/src/llama.cpp/build/bin/llama-server}"

if [[ "${CMD}" == "down" ]]; then
  if [[ -f "${PID_FILE}" ]]; then
    kill "$(cat "${PID_FILE}")" 2>/dev/null || true
    rm -f "${PID_FILE}"
  fi
  pkill -f "${LLAMA_BIN}.*--port ${LAB_PORT}" 2>/dev/null || true
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
LAB_PARALLEL="${LAB_PARALLEL:-1}"
LAB_SPEC_TYPE="${LAB_SPEC_TYPE:-}"
LAB_SPEC_DRAFT_N_MAX="${LAB_SPEC_DRAFT_N_MAX:-}"
LAB_CACHE_TYPE_K="${LAB_CACHE_TYPE_K:-}"
LAB_CACHE_TYPE_V="${LAB_CACHE_TYPE_V:-}"

if [[ ! -x "${LLAMA_BIN}" ]]; then
  echo "ERROR: ${LLAMA_BIN} not found; run build_llamacpp_mtp.sh first" >&2
  exit 2
fi

bash "$(dirname "$0")/launch_llamacpp_native.sh" down 2>/dev/null || true

IFS=',' read -r -a _gpus <<< "${LAB_GPUS}"
TS=""
for _ in "${_gpus[@]}"; do
  TS="${TS},1"
done
TS="${TS#,}"

export CUDA_VISIBLE_DEVICES="${LAB_GPUS}"

EXTRA=()
if [[ -n "${LAB_SPEC_TYPE}" ]]; then
  EXTRA+=(--spec-type "${LAB_SPEC_TYPE}")
fi
if [[ -n "${LAB_SPEC_DRAFT_N_MAX}" ]]; then
  EXTRA+=(--spec-draft-n-max "${LAB_SPEC_DRAFT_N_MAX}")
fi
if [[ -n "${LAB_CACHE_TYPE_K}" ]]; then
  EXTRA+=(--cache-type-k "${LAB_CACHE_TYPE_K}")
fi
if [[ -n "${LAB_CACHE_TYPE_V}" ]]; then
  EXTRA+=(--cache-type-v "${LAB_CACHE_TYPE_V}")
fi

echo "+ native llama.cpp: ${LAB_NAME} gguf=${LAB_GGUF_PATH} gpus=${LAB_GPUS}"

nohup "${LLAMA_BIN}" \
  -m "${LAB_GGUF_PATH}" \
  --host 0.0.0.0 --port "${LAB_PORT}" \
  -c "${LAB_CTX}" --parallel "${LAB_PARALLEL}" \
  -ngl 99 -ts "${TS}" -fa on --jinja \
  "${EXTRA[@]}" \
  --api-key "${LAB_API_KEY}" \
  >"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"

deadline=$(( $(date +%s) + 1200 ))
while [[ $(date +%s) -lt $deadline ]]; do
  if curl -fsS -m 3 "http://127.0.0.1:${LAB_PORT}/health" >/dev/null 2>&1 || \
     curl -fsS -m 3 "http://127.0.0.1:${LAB_PORT}/v1/models" \
        -H "Authorization: Bearer ${LAB_API_KEY}" >/dev/null 2>&1; then
    echo "READY: http://127.0.0.1:${LAB_PORT}/v1"
    exit 0
  fi
  if ! kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    echo "Process exited; log:" >&2
    tail -40 "${LOG_FILE}" >&2
    exit 1
  fi
  sleep 3
done
tail -40 "${LOG_FILE}" >&2
exit 1
