#!/usr/bin/env bash
# Optional: AWQ-quantize cerebras/Qwen3-Coder-REAP-25B-A3B for Test B vLLM path.
# Requires llm-compressor / vLLM quant tooling on the Alienware (one 3090, ~24GB+ RAM).
set -euo pipefail

export HF_HOME="${HF_HOME:-/mnt/data/hf-cache}"
MODEL="cerebras/Qwen3-Coder-REAP-25B-A3B"
OUT_DIR="${HF_HOME}/hub/models--local--qwen3-coder-reap25-awq"

echo "This script is a stub — run AWQ when community quants are unavailable."
echo "Reference: https://huggingface.co/mtecnic/research-test-Qwen3-Coder-Next-REAP-AWQ"
echo ""
echo "Suggested flow (manual, on Alienware):"
echo "  1. pip install llmcompressor vllm"
echo "  2. Use AWQModifier W4A16 group_size=32 on ${MODEL}"
echo "  3. Point test-b-qwen3-coder-reap25-vllm.env LAB_MODEL at the output dir"
echo ""
echo "Until then, Phase 2/3 use test-b-qwen3-coder-reap25-llamacpp.env (GGUF Q4_K_M)."

if [[ "${REAP_AWQ_EXECUTE:-0}" != "1" ]]; then
  exit 0
fi

python3 - <<'PY' || exit 1
import sys
print("Set REAP_AWQ_EXECUTE=1 only after wiring llm-compressor recipe here.", file=sys.stderr)
sys.exit(2)
PY
