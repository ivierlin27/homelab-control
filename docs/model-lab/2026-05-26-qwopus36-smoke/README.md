# Phase 1 smoke results (2026-05-26)

All vLLM candidates passed short_ping, tool_call, and json_contract with `enable_thinking: false`.

| Model | smoke.json path |
|-------|-----------------|
| Qwopus 35B | `qwopus36-35b-a3b-v1/smoke.json` |
| Qwopus 27B | `qwopus36-27b-v2/smoke.json` |
| Qwen3.6 27B | `qwen36-27b-awq/smoke.json` |
| Qwen3-Coder (baseline) | `qwen3-coder-30b-a3b-awq/smoke.json` |

Downloads logged on host: `/mnt/data/bench-artifacts/qwopus36-download.log`
