#!/usr/bin/env bash
# Speculative decoding / MTP evaluation phases. Run on Alienware from homelab-control root.
set -euo pipefail

PHASE="${1:-}"
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LAB_DIR="${REPO_ROOT}/scripts/model-lab/qwopus36"
export MTP_DATE="${MTP_DATE:-$(date +%Y-%m-%d)}"
ARTIFACT_ROOT="/mnt/data/bench-artifacts"
DOCS_ARTIFACT="${REPO_ROOT}/docs/model-lab/bench-artifacts"
AGENT_DOC="${REPO_ROOT}/docs/model-lab/${MTP_DATE}-mtp-agent-tasks"
SMOKE_DOC="${REPO_ROOT}/docs/model-lab/${MTP_DATE}-mtp-smoke"

chmod +x "${LAB_DIR}"/*.sh 2>/dev/null || true

stop_prod() {
  systemctl --user stop alienware-vllm-strong-long.service 2>/dev/null || true
  podman rm -f homelab-vllm-strong-long homelab-lab-vllm homelab-lab-llamacpp 2>/dev/null || true
}

down_lab() {
  LAB_PROFILE= bash "${LAB_DIR}/launch_vllm_lab.sh" down 2>/dev/null || true
  LAB_PROFILE= bash "${LAB_DIR}/launch_llamacpp_lab.sh" down 2>/dev/null || true
  LAB_PROFILE= bash "${LAB_DIR}/launch_llamacpp_native.sh" down 2>/dev/null || true
}

llama_up() {
  if [[ "${LAB_USE_NATIVE:-1}" == "1" ]] && [[ -x "${LLAMA_BIN:-/mnt/data/src/llama.cpp/build/bin/llama-server}" ]]; then
    bash "${LAB_DIR}/launch_llamacpp_native.sh" up
  else
    bash "${LAB_DIR}/launch_llamacpp_lab.sh" up
  fi
}

llama_down() {
  LAB_PROFILE= bash "${LAB_DIR}/launch_llamacpp_native.sh" down 2>/dev/null || true
  LAB_PROFILE= bash "${LAB_DIR}/launch_llamacpp_lab.sh" down 2>/dev/null || true
}

harness_llamacpp_profile() {
  local profile="$1"
  local key="$2"
  local cmds="${3:-micro bfcl}"
  # shellcheck disable=SC1090
  source "${profile}"
  export LAB_PROFILE="${profile}"
  llama_up
  export BENCH_BASE_URL="http://127.0.0.1:8002/v1"
  export BENCH_API_KEY="${LAB_API_KEY}"
  export BENCH_MODEL="${LAB_SERVED_NAME}"
  export BENCH_MODEL_KEY="${key}"
  export OUT="${ARTIFACT_ROOT}/${MTP_DATE}-${key}"
  export BENCH_ENABLE_THINKING_FALSE=1
  export HARNESS_CMDS="${cmds}"
  bash "${LAB_DIR}/run_harness.sh"
  mkdir -p "${DOCS_ARTIFACT}"
  cp -a "${OUT}/summary.json" "${DOCS_ARTIFACT}/${MTP_DATE}-${key}-summary.json" 2>/dev/null || true
  llama_down
}

harness_vllm_profile() {
  local profile="$1"
  local key="$2"
  local cmds="${3:-micro bfcl}"
  source "${profile}"
  export LAB_PROFILE="${profile}"
  bash "${LAB_DIR}/launch_vllm_lab.sh" up
  export BENCH_BASE_URL="http://127.0.0.1:8002/v1"
  export BENCH_API_KEY="${LAB_API_KEY}"
  export BENCH_MODEL="${LAB_SERVED_NAME}"
  export BENCH_MODEL_KEY="${key}"
  export OUT="${ARTIFACT_ROOT}/${MTP_DATE}-${key}"
  export BENCH_ENABLE_THINKING_FALSE=1
  export HARNESS_CMDS="${cmds}"
  bash "${LAB_DIR}/run_harness.sh"
  mkdir -p "${DOCS_ARTIFACT}"
  cp -a "${OUT}/summary.json" "${DOCS_ARTIFACT}/${MTP_DATE}-${key}-summary.json" 2>/dev/null || true
  down_lab
}

agent_tasks_llamacpp() {
  local profile="$1"
  local key="$2"
  source "${profile}"
  export LAB_PROFILE="${profile}"
  llama_up
  export AGENT_BASE_URL="http://127.0.0.1:8002/v1"
  export AGENT_API_KEY="${LAB_API_KEY}"
  export AGENT_MODEL="${LAB_SERVED_NAME}"
  export AGENT_MODEL_KEY="${key}"
  export AGENT_OUT_DIR="${AGENT_DOC}"
  export AGENT_TASK_FILTER="${AGENT_TASK_FILTER:-tool_chain,json,pr_review}"
  python3 "${LAB_DIR}/run_agent_tasks.py"
  down_lab
}

agent_tasks_vllm() {
  local profile="$1"
  local key="$2"
  source "${profile}"
  export LAB_PROFILE="${profile}"
  bash "${LAB_DIR}/launch_vllm_lab.sh" up
  export AGENT_BASE_URL="http://127.0.0.1:8002/v1"
  export AGENT_API_KEY="${LAB_API_KEY}"
  export AGENT_MODEL="${LAB_SERVED_NAME}"
  export AGENT_MODEL_KEY="${key}"
  export AGENT_OUT_DIR="${AGENT_DOC}"
  export AGENT_TASK_FILTER="${AGENT_TASK_FILTER:-tool_chain,json,pr_review}"
  python3 "${LAB_DIR}/run_agent_tasks.py"
  down_lab
}

smoke_llamacpp() {
  local profile="$1"
  local key="$2"
  source "${profile}"
  export LAB_PROFILE="${profile}"
  llama_up
  export SMOKE_BASE_URL="http://127.0.0.1:8002/v1"
  export SMOKE_API_KEY="${LAB_API_KEY}"
  export SMOKE_MODEL="${LAB_SERVED_NAME}"
  export SMOKE_OUT_DIR="${SMOKE_DOC}/${key}"
  python3 "${LAB_DIR}/smoke_endpoint.py"
  down_lab
}

case "${PHASE}" in
  1)
    stop_prod
    mkdir -p "${SMOKE_DOC}"
    echo "Phase 1: download Unsloth MTP GGUFs"
    bash "${LAB_DIR}/download_mtp_models.sh" | tee "${SMOKE_DOC}/download.log"
    echo "Phase 1b: build llama.cpp with MTP support"
    bash "${LAB_DIR}/build_llamacpp_mtp.sh" | tee "${SMOKE_DOC}/build-llama.log"
    echo "Phase 1 complete"
    ;;
  2)
    stop_prod
    mkdir -p "${AGENT_DOC}"
    # llama.cpp MTP matrix
    for p in \
      "${LAB_DIR}/profiles/qwen36-27b-mtp-llamacpp-baseline.env" \
      "${LAB_DIR}/profiles/qwen36-27b-mtp-llamacpp-d2.env" \
      "${LAB_DIR}/profiles/qwen36-27b-mtp-llamacpp-d3.env" \
      "${LAB_DIR}/profiles/qwen36-35b-mtp-llamacpp-d2.env"; do
      key="$(basename "${p}" .env)"
      echo "== harness llama.cpp ${key}"
      smoke_llamacpp "${p}" "${key}" || echo "WARN: smoke failed ${key}" >&2
      harness_llamacpp_profile "${p}" "${key}" "micro bfcl" || echo "WARN: harness failed ${key}" >&2
      agent_tasks_llamacpp "${p}" "${key}" || echo "WARN: agent failed ${key}" >&2
    done
    echo "Phase 2 complete"
    ;;
  3)
    stop_prod
    mkdir -p "${AGENT_DOC}"
    # vLLM TP=1 MTP vs baseline
    for p in \
      "${LAB_DIR}/profiles/qwen36-27b-awq-tp1-baseline.env" \
      "${LAB_DIR}/profiles/qwen36-27b-mtp-vllm-tp1-k1.env" \
      "${LAB_DIR}/profiles/qwen36-27b-mtp-vllm-tp1-k2.env"; do
      key="$(basename "${p}" .env)"
      echo "== harness vLLM ${key}"
      harness_vllm_profile "${p}" "${key}" "micro bfcl" || echo "WARN: harness failed ${key}" >&2
      agent_tasks_vllm "${p}" "${key}" || echo "WARN: agent failed ${key}" >&2
    done
    echo "Phase 3 complete"
    ;;
  4)
    python3 "${LAB_DIR}/render_mtp_verdict.py"
    ;;
  *)
    echo "Usage: MTP_DATE=YYYY-MM-DD $0 {1|2|3|4}" >&2
    echo "  1 = download MTP GGUFs" >&2
    echo "  2 = llama.cpp MTP benchmarks" >&2
    echo "  3 = vLLM TP=1 MTP benchmarks" >&2
    echo "  4 = render verdict" >&2
    exit 2
    ;;
esac
