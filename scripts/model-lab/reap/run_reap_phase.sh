#!/usr/bin/env bash
# REAP benchmark phases. Run on Alienware from homelab-control root.
set -euo pipefail

PHASE="${1:-}"
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LAB_DIR="${REPO_ROOT}/scripts/model-lab/reap"
export REAP_DATE="${REAP_DATE:-$(date +%Y-%m-%d)}"
ARTIFACT_ROOT="/mnt/data/bench-artifacts"
DOCS_ARTIFACT="${REPO_ROOT}/docs/model-lab/bench-artifacts"
SMOKE_DOC="${REPO_ROOT}/docs/model-lab/${REAP_DATE}-reap-smoke"
VERDICT_DOC="${REPO_ROOT}/docs/model-lab/${REAP_DATE}-reap-verdict.md"

chmod +x "${LAB_DIR}"/*.sh 2>/dev/null || true
chmod +x "${REPO_ROOT}/scripts/launch_llamacpp_lab.sh" "${REPO_ROOT}/scripts/launch_vllm_lab.sh" 2>/dev/null || true

stop_prod() {
  systemctl --user stop alienware-vllm-strong-long.service 2>/dev/null || true
  systemctl --user stop alienware-vllm-fast.service 2>/dev/null || true
  systemctl --user stop alienware-vllm-strong.service 2>/dev/null || true
  podman rm -f homelab-vllm-strong-long homelab-vllm-fast homelab-vllm-strong \
    homelab-lab-vllm homelab-lab-llamacpp 2>/dev/null || true
}

down_labs() {
  LAB_PROFILE= bash "${REPO_ROOT}/scripts/launch_vllm_lab.sh" down 2>/dev/null || true
  LAB_PROFILE= bash "${REPO_ROOT}/scripts/launch_llamacpp_lab.sh" down 2>/dev/null || true
}

smoke_llamacpp() {
  local profile="$1"
  local key="$2"
  local thinking="${3:-1}"
  # shellcheck disable=SC1090
  source "${profile}"
  export LAB_PROFILE="${profile}"
  bash "${REPO_ROOT}/scripts/launch_llamacpp_lab.sh" up
  export SMOKE_BASE_URL="http://127.0.0.1:${LAB_PORT}/v1"
  export SMOKE_API_KEY="${LAB_API_KEY}"
  export SMOKE_MODEL="${LAB_SERVED_NAME}"
  export SMOKE_OUT_DIR="${SMOKE_DOC}/${key}"
  export SMOKE_ENABLE_THINKING_FALSE="${thinking}"
  python3 "${REPO_ROOT}/scripts/model-lab/qwopus36/smoke_endpoint.py"
  down_labs
}

smoke_vllm() {
  local profile="$1"
  local key="$2"
  source "${profile}"
  export LAB_PROFILE="${profile}"
  bash "${REPO_ROOT}/scripts/launch_vllm_lab.sh" up
  export SMOKE_BASE_URL="http://127.0.0.1:${LAB_PORT}/v1"
  export SMOKE_API_KEY="${LAB_API_KEY}"
  export SMOKE_MODEL="${LAB_SERVED_NAME}"
  export SMOKE_OUT_DIR="${SMOKE_DOC}/${key}"
  export SMOKE_ENABLE_THINKING_FALSE=1
  python3 "${REPO_ROOT}/scripts/model-lab/qwopus36/smoke_endpoint.py"
  down_labs
}

harness_llamacpp() {
  local profile="$1"
  local key="$2"
  local cmds="${3:-micro serve-sweep ruler bfcl}"
  source "${profile}"
  export LAB_PROFILE="${profile}"
  bash "${REPO_ROOT}/scripts/launch_llamacpp_lab.sh" up
  export BENCH_BASE_URL="http://127.0.0.1:${LAB_PORT}/v1"
  export BENCH_API_KEY="${LAB_API_KEY}"
  export BENCH_MODEL="${LAB_SERVED_NAME}"
  export BENCH_MODEL_KEY="${key}"
  export OUT="${ARTIFACT_ROOT}/${REAP_DATE}-${key}"
  export BENCH_ENABLE_THINKING_FALSE=1
  export HARNESS_CMDS="${cmds}"
  bash "${LAB_DIR}/run_harness.sh"
  mkdir -p "${DOCS_ARTIFACT}"
  for f in summary.json summary-*.json; do
    [[ -f "${OUT}/${f}" ]] && cp -a "${OUT}/${f}" "${DOCS_ARTIFACT}/${REAP_DATE}-${key}-${f}" 2>/dev/null || true
  done
  down_labs
}

harness_vllm() {
  local profile="$1"
  local key="$2"
  local cmds="${3:-micro serve-sweep ruler bfcl soak}"
  source "${profile}"
  export LAB_PROFILE="${profile}"
  bash "${REPO_ROOT}/scripts/launch_vllm_lab.sh" up
  export BENCH_BASE_URL="http://127.0.0.1:${LAB_PORT}/v1"
  export BENCH_API_KEY="${LAB_API_KEY}"
  export BENCH_MODEL="${LAB_SERVED_NAME}"
  export BENCH_MODEL_KEY="${key}"
  export OUT="${ARTIFACT_ROOT}/${REAP_DATE}-${key}"
  export BENCH_ENABLE_THINKING_FALSE=1
  export HARNESS_CMDS="${cmds}"
  bash "${LAB_DIR}/run_harness.sh"
  mkdir -p "${DOCS_ARTIFACT}"
  for f in summary.json summary-*.json; do
    [[ -f "${OUT}/${f}" ]] && cp -a "${OUT}/${f}" "${DOCS_ARTIFACT}/${REAP_DATE}-${key}-${f}" 2>/dev/null || true
  done
  down_labs
}

restart_prod() {
  if [[ "${REAP_LEAVE_PROD_STOPPED:-0}" == "1" ]]; then
    echo "Skipping prod restart (REAP_LEAVE_PROD_STOPPED=1)"
    return 0
  fi
  systemctl --user enable --now alienware-vllm-strong-long.service 2>/dev/null || true
}

case "${PHASE}" in
  1)
    stop_prod
    mkdir -p "${SMOKE_DOC}"
    bash "${LAB_DIR}/download_models.sh" | tee "${SMOKE_DOC}/download.log"
    if [[ "${REAP_RUN_AWQ:-0}" == "1" ]]; then
      bash "${LAB_DIR}/quantize_qwen3_coder_reap25.sh" | tee "${SMOKE_DOC}/awq.log" || true
    fi
    echo "Phase 1 complete"
    ;;
  2)
    stop_prod
    mkdir -p "${SMOKE_DOC}"
    echo "== baseline"
    smoke_vllm "${LAB_DIR}/profiles/baseline-qwen3-coder-30b-awq.env" "baseline" \
      | tee "${SMOKE_DOC}/baseline.log" || true
    echo "== Test A glm47 REAP llama.cpp"
    smoke_llamacpp "${LAB_DIR}/profiles/test-a-glm47-flash-reap23-llamacpp.env" "test-a-llamacpp" \
      | tee "${SMOKE_DOC}/test-a-llamacpp.log" || true
    echo "== Test B qwen3 coder REAP (vLLM BF16 or GGUF fallback)"
    if [[ -f /mnt/data/models/gguf/qwen3-coder-reap25-q4km/Qwen3-Coder-REAP-25B-A3B-Q4_K_M.gguf ]]; then
      smoke_llamacpp "${LAB_DIR}/profiles/test-b-qwen3-coder-reap25-llamacpp.env" "test-b-llamacpp" \
        | tee "${SMOKE_DOC}/test-b-llamacpp.log" || true
    else
      smoke_vllm "${LAB_DIR}/profiles/test-b-qwen3-coder-reap25-vllm.env" "test-b-vllm" \
        | tee "${SMOKE_DOC}/test-b-vllm.log" || true
    fi
    echo "== Test C glm45air REAP82"
    smoke_llamacpp "${LAB_DIR}/profiles/test-c-glm45air-reap82-llamacpp.env" "test-c" \
      | tee "${SMOKE_DOC}/test-c.log" || true
    echo "== Test E qwen36 REAP"
    smoke_llamacpp "${LAB_DIR}/profiles/test-e-qwen36-28b-reap-llamacpp.env" "test-e" \
      | tee "${SMOKE_DOC}/test-e.log" || true
    echo "Phase 2 smoke -> ${SMOKE_DOC}"
    ;;
  3)
    stop_prod
    harness_vllm "${LAB_DIR}/profiles/baseline-qwen3-coder-30b-awq.env" "baseline"
    harness_llamacpp "${LAB_DIR}/profiles/test-a-glm47-flash-reap23-llamacpp.env" "test-a-llamacpp"
    if [[ -f "${SMOKE_DOC}/test-a-llamacpp/smoke.json" ]] && \
       python3 -c "import json,sys; d=json.load(open('${SMOKE_DOC}/test-a-llamacpp/smoke.json')); sys.exit(0 if d.get('passed') else 1)" 2>/dev/null; then
      echo "Test A smoke passed — optional vLLM promotion harness"
      harness_vllm "${LAB_DIR}/profiles/test-a-glm47-flash-reap23-vllm.env" "test-a-vllm" "micro bfcl ruler" || true
    fi
    if [[ -f /mnt/data/models/gguf/qwen3-coder-reap25-q4km/Qwen3-Coder-REAP-25B-A3B-Q4_K_M.gguf ]]; then
      harness_llamacpp "${LAB_DIR}/profiles/test-b-qwen3-coder-reap25-llamacpp.env" "test-b-llamacpp" "micro bfcl ruler"
    else
      harness_vllm "${LAB_DIR}/profiles/test-b-qwen3-coder-reap25-vllm.env" "test-b-vllm" "micro bfcl ruler"
    fi
    harness_llamacpp "${LAB_DIR}/profiles/test-c-glm45air-reap82-llamacpp.env" "test-c" "micro bfcl ruler"
    harness_llamacpp "${LAB_DIR}/profiles/test-e-qwen36-28b-reap-llamacpp.env" "test-e" "micro bfcl ruler"
    python3 "${LAB_DIR}/render_verdict.py" --partial
    echo "Phase 3 harness complete"
    ;;
  4)
    stop_prod
    mkdir -p "${SMOKE_DOC}/dual"
    bash "${LAB_DIR}/launch_dual.sh" up
    export BENCH_BASE_URL="http://127.0.0.1:8002/v1"
    source "${LAB_DIR}/profiles/dual-strong-glm47-reap-gpu1.env"
    export BENCH_API_KEY="${LAB_API_KEY}"
    export BENCH_MODEL="${LAB_SERVED_NAME}"
    export BENCH_MODEL_KEY="dual-strong-glm47"
    export OUT="${ARTIFACT_ROOT}/${REAP_DATE}-dual-strong"
    export HARNESS_CMDS="micro"
    bash "${LAB_DIR}/run_harness.sh"
    export BENCH_BASE_URL="http://127.0.0.1:8000/v1"
    source "${LAB_DIR}/profiles/dual-fast-gpu0.env"
    export BENCH_API_KEY="${LAB_API_KEY}"
    export BENCH_MODEL="${LAB_SERVED_NAME}"
    export BENCH_MODEL_KEY="dual-fast-7b"
    export OUT="${ARTIFACT_ROOT}/${REAP_DATE}-dual-fast"
    bash "${LAB_DIR}/run_harness.sh"
    bash "${LAB_DIR}/launch_dual.sh" down
    echo "Phase 4 dual harness complete"
    ;;
  5)
    python3 "${LAB_DIR}/render_verdict.py"
    restart_prod
    echo "Verdict: ${VERDICT_DOC}"
    ;;
  prod)
    restart_prod
    ;;
  *)
    echo "Usage: REAP_DATE=YYYY-MM-DD $0 {1|2|3|4|5|prod}" >&2
    echo "  1=download  2=smoke  3=harness  4=dual(experimental)  5=verdict+restart prod" >&2
    exit 2
    ;;
esac
