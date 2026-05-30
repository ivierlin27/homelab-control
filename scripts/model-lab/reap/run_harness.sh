#!/usr/bin/env bash
# Run scripts/bench v2 against the lab endpoint (port 8002 by default).
set -euo pipefail

: "${BENCH_MODEL:?}"
: "${BENCH_MODEL_KEY:?}"
: "${BENCH_API_KEY:?}"
: "${OUT:?}"

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "${REPO_ROOT}"

export BENCH_BASE_URL="${BENCH_BASE_URL:-http://127.0.0.1:8002/v1}"
export BENCH_REPEATS="${BENCH_REPEATS:-10}"
export BENCH_WARMUP="${BENCH_WARMUP:-2}"
export BENCH_SOAK_MINUTES="${BENCH_SOAK_MINUTES:-10}"
export BENCH_ENABLE_THINKING_FALSE="${BENCH_ENABLE_THINKING_FALSE:-1}"

# GLM-4.7 uses glm47 tool parser in vLLM; llama.cpp uses --jinja from the GGUF.
export BENCH_RULER_LENGTHS="${BENCH_RULER_LENGTHS:-32768,65536,131072}"

HARNESS_CMDS="${HARNESS_CMDS:-micro serve-sweep ruler bfcl soak}"

mkdir -p "${OUT}"
echo "REAP harness OUT=${OUT} model=${BENCH_MODEL}"

for cmd in ${HARNESS_CMDS}; do
  echo "== bench ${cmd}"
  python3 -m scripts.bench "${cmd}" || {
    echo "WARN: ${cmd} failed for ${BENCH_MODEL_KEY}" >&2
  }
  if [[ -f "${OUT}/summary.json" && "${cmd}" != "soak" ]]; then
    cp "${OUT}/summary.json" "${OUT}/summary-${cmd}.json"
  fi
done

python3 -m scripts.bench aggregate "${OUT%/*}" 2>/dev/null || true
echo "Done: ${OUT}"
