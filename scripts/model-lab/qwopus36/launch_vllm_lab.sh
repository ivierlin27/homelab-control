#!/usr/bin/env bash
# Ad-hoc vLLM lab endpoint on port 8002 (same as strong-long; stop systemd first).
set -euo pipefail

CMD="${1:-up}"
NAME="homelab-lab-vllm"
LAB_PORT="${LAB_PORT:-8002}"

if [[ "${CMD}" == "down" ]]; then
  podman rm -f "${NAME}" 2>/dev/null || true
  echo "down ${NAME}"
  exit 0
fi

if [[ -z "${LAB_PROFILE:-}" ]]; then
  echo "ERROR: set LAB_PROFILE to a file under scripts/model-lab/qwopus36/profiles/" >&2
  exit 2
fi

# shellcheck disable=SC1090
source "${LAB_PROFILE}"

: "${LAB_NAME:?}"
: "${LAB_MODEL:?}"
: "${LAB_SERVED_NAME:?}"
: "${LAB_API_KEY:?}"
LAB_TP="${LAB_TP:-2}"
LAB_QUANT="${LAB_QUANTIZATION:-awq_marlin}"
LAB_DTYPE="${LAB_DTYPE:-bfloat16}"
LAB_KV="${LAB_KV_CACHE_DTYPE:-fp8}"
LAB_MAX_LEN="${LAB_MAX_MODEL_LEN:-131072}"
LAB_BATCH="${LAB_MAX_NUM_BATCHED_TOKENS:-8192}"
LAB_SEQS="${LAB_MAX_NUM_SEQS:-16}"
LAB_GPU_UTIL="${LAB_GPU_MEMORY_UTILIZATION:-0.92}"
LAB_TOOL_PARSER="${LAB_TOOL_PARSER:-qwen3_xml}"
LAB_EXTRA="${LAB_EXTRA_ARGS:-}"
LAB_IMAGE="${LAB_IMAGE:-docker.io/vllm/vllm-openai:latest}"
LAB_PREFIX_CACHING="${LAB_PREFIX_CACHING:-on}"
LAB_SPEC_CONFIG="${LAB_SPEC_CONFIG:-}"

PREFIX_FLAG=(--enable-prefix-caching)
if [[ "${LAB_PREFIX_CACHING}" == "off" ]]; then
  PREFIX_FLAG=(--no-enable-prefix-caching)
fi

SPEC_ARGS=()
if [[ -n "${LAB_SPEC_CONFIG}" ]]; then
  SPEC_ARGS=(--speculative-config "${LAB_SPEC_CONFIG}")
fi

podman rm -f "${NAME}" 2>/dev/null || true
mkdir -p /mnt/data/hf-cache /mnt/data/tmp

echo "+ starting vLLM lab: ${LAB_NAME} model=${LAB_MODEL} tp=${LAB_TP}"

podman run -d --name "${NAME}" --replace \
  --device "nvidia.com/gpu=all" \
  --security-opt=label=disable --ipc=host \
  -p "0.0.0.0:${LAB_PORT}:8002" \
  -v /mnt/data/hf-cache:/root/.cache/huggingface:Z \
  -v /mnt/data/tmp:/tmp:Z \
  -e HF_HUB_ENABLE_HF_TRANSFER=1 \
  -e VLLM_ATTENTION_BACKEND=FLASHINFER \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  "${LAB_IMAGE}" \
  --host 0.0.0.0 --port 8002 \
  --model "${LAB_MODEL}" \
  --served-model-name "${LAB_SERVED_NAME}" \
  --tensor-parallel-size "${LAB_TP}" \
  --quantization "${LAB_QUANT}" \
  --dtype "${LAB_DTYPE}" \
  --kv-cache-dtype "${LAB_KV}" \
  --max-model-len "${LAB_MAX_LEN}" \
  --max-num-batched-tokens "${LAB_BATCH}" \
  --max-num-seqs "${LAB_SEQS}" \
  --gpu-memory-utilization "${LAB_GPU_UTIL}" \
  "${PREFIX_FLAG[@]}" --enable-chunked-prefill \
  --enable-auto-tool-choice --tool-call-parser "${LAB_TOOL_PARSER}" \
  --trust-remote-code --disable-custom-all-reduce \
  --api-key "${LAB_API_KEY}" \
  "${SPEC_ARGS[@]}" \
  ${LAB_EXTRA:-}

deadline=$(( $(date +%s) + 1200 ))
while [[ $(date +%s) -lt $deadline ]]; do
  if curl -fsS -m 3 "http://127.0.0.1:${LAB_PORT}/v1/models" \
        -H "Authorization: Bearer ${LAB_API_KEY}" >/dev/null 2>&1; then
    echo "READY: http://127.0.0.1:${LAB_PORT}/v1  served=${LAB_SERVED_NAME}"
    podman ps --filter "name=${NAME}"
    exit 0
  fi
  sleep 5
done
echo "TIMEOUT; logs:" >&2
podman logs --tail 80 "${NAME}" >&2
exit 1
