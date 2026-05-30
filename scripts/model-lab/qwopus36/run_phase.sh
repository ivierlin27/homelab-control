#!/usr/bin/env bash
# Orchestrate Qwopus3.6 benchmark phases. Run on Alienware from homelab-control root.
set -euo pipefail

PHASE="${1:-}"
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LAB_DIR="${REPO_ROOT}/scripts/model-lab/qwopus36"
export QWOPUS36_DATE="${QWOPUS36_DATE:-$(date +%Y-%m-%d)}"
ARTIFACT_ROOT="/mnt/data/bench-artifacts"
DOCS_ARTIFACT="${REPO_ROOT}/docs/model-lab/bench-artifacts"
SMOKE_DOC="${REPO_ROOT}/docs/model-lab/${QWOPUS36_DATE}-qwopus36-smoke"
AGENT_DOC="${REPO_ROOT}/docs/model-lab/${QWOPUS36_DATE}-qwopus36-agent-tasks"

chmod +x "${LAB_DIR}"/*.sh 2>/dev/null || true

stop_prod() {
  systemctl --user stop alienware-vllm-strong-long.service 2>/dev/null || true
  podman rm -f homelab-vllm-strong-long homelab-lab-vllm homelab-lab-llamacpp 2>/dev/null || true
}

down_lab() {
  LAB_PROFILE= bash "${LAB_DIR}/launch_vllm_lab.sh" down 2>/dev/null || true
  LAB_PROFILE= bash "${LAB_DIR}/launch_llamacpp_lab.sh" down 2>/dev/null || true
}

smoke_vllm_profile() {
  local profile="$1"
  local key="$2"
  # shellcheck disable=SC1090
  source "${profile}"
  export LAB_PROFILE="${profile}"
  bash "${LAB_DIR}/launch_vllm_lab.sh" up
  export SMOKE_BASE_URL="http://127.0.0.1:8002/v1"
  export SMOKE_API_KEY="${LAB_API_KEY}"
  export SMOKE_MODEL="${LAB_SERVED_NAME}"
  export SMOKE_OUT_DIR="${SMOKE_DOC}/${key}"
  python3 "${LAB_DIR}/smoke_endpoint.py"
  down_lab
}

harness_vllm_profile() {
  local profile="$1"
  local key="$2"
  local cmds="${3:-micro serve-sweep ruler bfcl soak}"
  source "${profile}"
  export LAB_PROFILE="${profile}"
  bash "${LAB_DIR}/launch_vllm_lab.sh" up
  export BENCH_BASE_URL="http://127.0.0.1:8002/v1"
  export BENCH_API_KEY="${LAB_API_KEY}"
  export BENCH_MODEL="${LAB_SERVED_NAME}"
  export BENCH_MODEL_KEY="${key}"
  export OUT="${ARTIFACT_ROOT}/${QWOPUS36_DATE}-${key}"
  export BENCH_ENABLE_THINKING_FALSE=1
  export HARNESS_CMDS="${cmds}"
  bash "${LAB_DIR}/run_harness.sh"
  mkdir -p "${DOCS_ARTIFACT}"
  cp -a "${OUT}/summary.json" "${DOCS_ARTIFACT}/${QWOPUS36_DATE}-${key}-summary.json" 2>/dev/null || true
  down_lab
}

agent_tasks_profile() {
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
  python3 "${LAB_DIR}/run_agent_tasks.py"
  down_lab
}

case "${PHASE}" in
  1)
    stop_prod
    mkdir -p "${SMOKE_DOC}"
    echo "Phase 1: downloads (may take hours on first run)"
    bash "${LAB_DIR}/download_models.sh" | tee "${SMOKE_DOC}/download.log"
    for p in \
      "${LAB_DIR}/profiles/qwopus36-35b-a3b-v1.env" \
      "${LAB_DIR}/profiles/qwopus36-27b-v2.env" \
      "${LAB_DIR}/profiles/qwen36-27b-awq.env"; do
      key="$(basename "${p}" .env)"
      echo "== smoke ${key}"
      smoke_vllm_profile "${p}" "${key}" | tee "${SMOKE_DOC}/${key}.log" || true
    done
    echo "Phase 1 complete -> ${SMOKE_DOC}"
    ;;
  2)
    stop_prod
    harness_vllm_profile "${LAB_DIR}/profiles/qwopus36-35b-a3b-v1.env" "qwopus36-35b-a3b-v1"
    harness_vllm_profile "${LAB_DIR}/profiles/qwopus36-27b-v2.env" "qwopus36-27b-v2"
    harness_vllm_profile "${LAB_DIR}/profiles/qwen36-27b-awq.env" "qwen36-27b-awq"
    # MTP: llama.cpp partial harness
    if [[ -f /mnt/data/models/gguf/Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf ]] || \
       ls /mnt/data/models/gguf/*MTP*Q4_K_M*.gguf 1>/dev/null 2>&1; then
      export LAB_PROFILE="${LAB_DIR}/profiles/qwopus36-27b-v2-mtp.env"
      # resolve actual gguf filename
      gguf="$(ls /mnt/data/models/gguf/*MTP*Q4_K_M*.gguf 2>/dev/null | head -1)"
      if [[ -n "${gguf}" ]]; then
        echo "LAB_GGUF_PATH=${gguf}" >> "${LAB_DIR}/profiles/qwopus36-27b-v2-mtp.env.local"
        export LAB_GGUF_PATH="${gguf}"
      fi
      source "${LAB_DIR}/profiles/qwopus36-27b-v2-mtp.env"
      [[ -f "${LAB_DIR}/profiles/qwopus36-27b-v2-mtp.env.local" ]] && source "${LAB_DIR}/profiles/qwopus36-27b-v2-mtp.env.local"
      bash "${LAB_DIR}/launch_llamacpp_lab.sh" up
      export BENCH_BASE_URL="http://127.0.0.1:8002/v1"
      export BENCH_API_KEY="${LAB_API_KEY}"
      export BENCH_MODEL="${LAB_SERVED_NAME}"
      export BENCH_MODEL_KEY="qwopus36-27b-v2-mtp"
      export OUT="${ARTIFACT_ROOT}/${QWOPUS36_DATE}-qwopus36-27b-v2-mtp"
      export HARNESS_CMDS="micro bfcl ruler"
      bash "${LAB_DIR}/run_harness.sh"
      down_lab
    fi
    python3 "${LAB_DIR}/render_verdict.py" --aggregate-only || true
    ;;
  3)
    stop_prod
    mkdir -p "${AGENT_DOC}"
    # Baseline from production profile (optional snapshot — run against daily if still up)
    agent_tasks_profile "${LAB_DIR}/profiles/qwopus36-35b-a3b-v1.env" "qwopus36-35b-a3b-v1"
    agent_tasks_profile "${LAB_DIR}/profiles/qwopus36-27b-v2.env" "qwopus36-27b-v2"
    agent_tasks_profile "${LAB_DIR}/profiles/qwen36-27b-awq.env" "qwen36-27b-awq"
  # Current daily for comparison (re-enable prod briefly or dedicated profile)
    if [[ -f "${REPO_ROOT}/scripts/model-lab/qwopus36/profiles/qwen3-coder-30b-a3b-awq.env" ]]; then
      agent_tasks_profile "${LAB_DIR}/profiles/qwen3-coder-30b-a3b-awq.env" "qwen3-coder-30b-a3b-awq"
    fi
    echo "Phase 3 complete -> ${AGENT_DOC}"
    ;;
  4)
    stop_prod
    mkdir -p "${REPO_ROOT}/docs/model-lab/${QWOPUS36_DATE}-qwopus36-mxfp4-topologies"
    if ls /mnt/data/models/gguf/*MXFP4*.gguf 1>/dev/null 2>&1; then
      gguf="$(ls /mnt/data/models/gguf/*MXFP4*BF16*.gguf 2>/dev/null | head -1)"
      export LAB_GGUF_PATH="${gguf}"
      echo "LAB_GGUF_PATH=${gguf}" > "${LAB_DIR}/profiles/qwopus36-35b-mxfp4-gpu0.env.local"
      source "${LAB_DIR}/profiles/qwopus36-35b-mxfp4-gpu0.env"
      source "${LAB_DIR}/profiles/qwopus36-35b-mxfp4-gpu0.env.local"
      export LAB_PROFILE="${LAB_DIR}/profiles/qwopus36-35b-mxfp4-gpu0.env"
      bash "${LAB_DIR}/launch_llamacpp_lab.sh" up
      export BENCH_BASE_URL="http://127.0.0.1:8002/v1"
      export BENCH_API_KEY="${LAB_API_KEY}"
      export BENCH_MODEL="${LAB_SERVED_NAME}"
      export BENCH_MODEL_KEY="qwopus36-35b-mxfp4-gpu0"
      export OUT="${ARTIFACT_ROOT}/${QWOPUS36_DATE}-qwopus36-35b-mxfp4-gpu0"
      export HARNESS_CMDS="micro bfcl"
      bash "${LAB_DIR}/run_harness.sh"
      down_lab
    else
      echo "Skip Phase 4: no MXFP4 GGUF in /mnt/data/models/gguf"
    fi
    ;;
  5)
    stop_prod
    mkdir -p "${AGENT_DOC}"
    agent_tasks_profile "${LAB_DIR}/profiles/qwen36-35b-a3b-awq.env" "qwen36-35b-a3b-awq"
    echo "Phase 5 complete (35B base control agent tasks)"
    ;;
  6)
    python3 "${LAB_DIR}/render_verdict.py"
    ;;
  *)
    echo "Usage: QWOPUS36_DATE=YYYY-MM-DD $0 {1|2|3|4|5|6}" >&2
    exit 2
    ;;
esac
