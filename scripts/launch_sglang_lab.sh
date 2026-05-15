#!/usr/bin/env bash
# Reusable launcher for ad-hoc SGLang endpoints used by the bench harness.
#
# Reads a profile env file from $LAB_PROFILE and starts a podman container
# named "homelab-lab-sglang" on the chosen GPUs. Stops anything already named
# the same first. Health-checks /v1/models and prints the URL when ready.
#
# Profile env file (sourced) must export:
#   LAB_NAME            short tag (used in OUT dirs)
#   LAB_IMAGE           image ref (e.g. docker.io/lmsysorg/sglang:latest)
#   LAB_GPUS            "all" | "0" | "1" | "0,1"
#   LAB_PORT            host port (e.g. 8011)
#   LAB_API_KEY         bearer token
#   LAB_MODEL           HF model ref or local path (e.g. QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ)
#   LAB_SERVED_NAME     served-model-name
#   LAB_SGLANG_ARGS     extra `sglang.launch_server` args (string)
#   LAB_ENV_VARS        space-separated KEY=VAL pairs to set inside the container
#
# Usage:
#   LAB_PROFILE=~/.config/homelab-control/lab-sglang-qwen3-coder.env \
#     bash scripts/launch_sglang_lab.sh up
#   bash scripts/launch_sglang_lab.sh down

set -euo pipefail

CMD="${1:-up}"
NAME="homelab-lab-sglang"

if [[ "${CMD}" == "down" ]]; then
  podman rm -f "${NAME}" 2>/dev/null || true
  echo "down ${NAME}"
  exit 0
fi

if [[ -z "${LAB_PROFILE:-}" ]]; then
  echo "ERROR: set LAB_PROFILE to the env file" >&2
  exit 2
fi

# shellcheck disable=SC1090
. "${LAB_PROFILE}"

: "${LAB_NAME:?profile must set LAB_NAME}"
: "${LAB_IMAGE:?profile must set LAB_IMAGE}"
: "${LAB_GPUS:?profile must set LAB_GPUS}"
: "${LAB_PORT:?profile must set LAB_PORT}"
: "${LAB_API_KEY:?profile must set LAB_API_KEY}"
: "${LAB_MODEL:?profile must set LAB_MODEL}"
: "${LAB_SERVED_NAME:?profile must set LAB_SERVED_NAME}"
LAB_SGLANG_ARGS="${LAB_SGLANG_ARGS:-}"
LAB_ENV_VARS="${LAB_ENV_VARS:-}"

podman rm -f "${NAME}" 2>/dev/null || true
mkdir -p /mnt/data/hf-cache /mnt/data/tmp /mnt/data/models

ENV_FLAGS=()
for kv in ${LAB_ENV_VARS}; do
  ENV_FLAGS+=("-e" "${kv}")
done

# Internal container port is fixed at 30000 (sglang default).
CMD_LINE=(
  podman run -d --name "${NAME}" --replace
  --device "nvidia.com/gpu=${LAB_GPUS}"
  --security-opt=label=disable
  --ipc=host
  -p "0.0.0.0:${LAB_PORT}:30000"
  -v "/mnt/data/hf-cache:/root/.cache/huggingface:Z"
  -v "/mnt/data/models:/mnt/data/models:Z"
  -v "/mnt/data/tmp:/tmp:Z"
  -e "HF_HUB_ENABLE_HF_TRANSFER=1"
)
CMD_LINE+=("${ENV_FLAGS[@]}")
CMD_LINE+=(
  "${LAB_IMAGE}"
  python3 -m sglang.launch_server
  --host 0.0.0.0 --port 30000
  --model-path "${LAB_MODEL}"
  --served-model-name "${LAB_SERVED_NAME}"
  --api-key "${LAB_API_KEY}"
)
# shellcheck disable=SC2206
EXTRA=( ${LAB_SGLANG_ARGS} )
CMD_LINE+=("${EXTRA[@]}")

echo "+ ${CMD_LINE[*]}"
"${CMD_LINE[@]}"

echo "Waiting for endpoint http://127.0.0.1:${LAB_PORT}/v1/models ..."
deadline=$(( $(date +%s) + 900 ))
while [[ $(date +%s) -lt $deadline ]]; do
  if curl -fsS -m 3 "http://127.0.0.1:${LAB_PORT}/v1/models" \
        -H "Authorization: Bearer ${LAB_API_KEY}" >/dev/null 2>&1; then
    echo "READY: http://127.0.0.1:${LAB_PORT}/v1  (model=${LAB_SERVED_NAME})"
    podman ps --filter "name=${NAME}"
    exit 0
  fi
  sleep 3
done
echo "TIMEOUT waiting for endpoint; recent logs:" >&2
podman logs --tail 120 "${NAME}" >&2
exit 1
