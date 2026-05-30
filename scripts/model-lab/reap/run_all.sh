#!/usr/bin/env bash
# Run full REAP benchmark pipeline (phases 1-5). On Alienware only.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${DIR}/../../.." && pwd)"
export REAP_DATE="${REAP_DATE:-$(date +%Y-%m-%d)}"
for p in 1 2 3; do
  bash "${DIR}/run_reap_phase.sh" "${p}"
done
# Test D only when Test A smoke passed
SMOKE="${REPO_ROOT}/docs/model-lab/${REAP_DATE}-reap-smoke/test-a-llamacpp/smoke.json"
if [[ -f "${SMOKE}" ]] && python3 -c "import json,sys; d=json.load(open('${SMOKE}')); sys.exit(0 if d.get('passed') else 1)" 2>/dev/null; then
  bash "${DIR}/run_reap_phase.sh" 4 || true
fi
bash "${DIR}/run_reap_phase.sh" 5
