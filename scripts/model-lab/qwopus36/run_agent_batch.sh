#!/usr/bin/env bash
# Run agent-task battery for each vLLM profile in order.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
LAB="${REPO}/scripts/model-lab/qwopus36"
export QWOPUS36_DATE="${QWOPUS36_DATE:-$(date +%Y-%m-%d)}"
export AGENT_OUT_DIR="${REPO}/docs/model-lab/${QWOPUS36_DATE}-qwopus36-agent-tasks"
mkdir -p "${AGENT_OUT_DIR}"

run_one() {
  local profile="$1"
  local key="$2"
  # shellcheck disable=SC1090
  source "${profile}"
  export LAB_PROFILE="${profile}"
  bash "${LAB}/launch_vllm_lab.sh" down 2>/dev/null || true
  bash "${LAB}/launch_vllm_lab.sh" up
  export AGENT_BASE_URL="http://127.0.0.1:8002/v1"
  export AGENT_API_KEY="${LAB_API_KEY}"
  export AGENT_MODEL="${LAB_SERVED_NAME}"
  export AGENT_MODEL_KEY="${key}"
  python3 "${LAB}/run_agent_tasks.py"
  bash "${LAB}/launch_vllm_lab.sh" down
}

for pair in \
  "${LAB}/profiles/qwopus36-35b-a3b-v1.env:qwopus36-35b-a3b-v1" \
  "${LAB}/profiles/qwopus36-27b-v2.env:qwopus36-27b-v2" \
  "${LAB}/profiles/qwen36-27b-awq.env:qwen36-27b-awq" \
  "${LAB}/profiles/qwen3-coder-30b-a3b-awq.env:qwen3-coder-30b-a3b-awq" \
  "${LAB}/profiles/qwen36-35b-a3b-awq.env:qwen36-35b-a3b-awq"; do
  IFS=: read -r prof key <<< "${pair}"
  echo "== agent tasks ${key}"
  run_one "${prof}" "${key}"
done
