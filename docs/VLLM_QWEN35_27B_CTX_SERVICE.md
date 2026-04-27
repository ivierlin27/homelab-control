# Optional vLLM: Qwen3.5-27B-AWQ (long-context lab)

This is an **opt-in** OpenAI-compatible `vllm` backend on **port 8002**. It is not
the default for `homelab-fast`, `homelab-strong`, or `./scripts/set-alienware-model-mode.sh`.

If you asked for “Qwen3.6 27B”: upstream publishes **Qwen3.5-27B** (dense) and a
community **AWQ** build `QuantTrio/Qwen3.5-27B-AWQ` that fits consumer VRAM. There
is no separate “3.6 / 27B” model id in common use; this stack targets that 27B
AWQ profile.

## Why a separate unit

- Keeps **8000** (fast) and **8001** (strong) unchanged for LiteLLM and scripts.
- Avoids editing `litellm.config.yaml` until you deliberately wire a new route.
- Same pinned image as the other Alienware units: `docker.io/vllm/vllm-openai:v0.19.1`.

## Context-oriented runtime choices (single RTX 3090, 24 GB)

- **AWQ weights** (`QuantTrio/Qwen3.5-27B-AWQ`) so most VRAM stays available for KV.
- **`fp8_e4m3` KV cache** to shrink KV versus `fp16` (same idea as the strong unit).
- **`--enable-prefix-caching`** and **`--enable-chunked-prefill`** (in the unit) for
  long prompts and repeated system/tool prefixes.
- **`--max-num-seqs 2`** in the default env: favors a longer static context window
  over concurrent sessions on one GPU.
- **`--max-num-batched-tokens 4096`**: moderate prefill batching; lower if you hit
  prefill OOM at very long prompts.

Optional extras live in **`VLLM_Q35_CTX_EXTRA_ARGS`**:

- **`--trust-remote-code`** for checkpoint chat templates.
- **`--enable-auto-tool-choice --tool-call-parser qwen3_coder`** for Qwen3-style
  tool XML (matches common vLLM recipes for this family).
- Optional: add **`--reasoning-parser qwen3`** if you want structured reasoning
  fields; on `v0.19.x` some Qwen3.5 checkpoints can emit tool markup inside
  thinking blocks, which breaks tool parsing—drop the reasoning parser if that
  happens.

After a clean boot, if the engine starts but you want **more** static context,
raise `VLLM_Q35_CTX_MAX_MODEL_LEN` in steps (for example `49152` → `65536` →
`81920`) until startup reports GPU OOM, then back off one step.

## Install (Alienware)

```bash
cd ~/git/homelab-control   # or your checkout path
./scripts/install-alienware-vllm-qwen35-27b-ctx.sh
```

Edit `~/.config/homelab-control/vllm-qwen35-27b-ctx.env` (at minimum the API key).

## Run (manual; stops other vLLM units first)

Only one heavy model should run on the 3090:

```bash
systemctl --user stop alienware-vllm-fast.service alienware-vllm-strong.service
systemctl --user start alienware-vllm-qwen35-27b-ctx.service
journalctl --user -u alienware-vllm-qwen35-27b-ctx.service -f
```

OpenAI base URL for local tests: `http://127.0.0.1:8002/v1`.

## Smoke tests and statistics

```bash
export VLLM_Q35_CTX_API_KEY="$(grep ^VLLM_Q35_CTX_API_KEY= ~/.config/homelab-control/vllm-qwen35-27b-ctx.env | cut -d= -f2-)"
python3 scripts/smoke_vllm_qwen35_27b_ctx.py
```

The script prints JSON per case: wall time, `prompt_tokens`, `completion_tokens`,
`total_tokens`, approximate decode tok/s, tool-call count, and `finish_reason`.

## Restore automations (fast model)

Stop the lab unit first so the 3090 VRAM is free (the mode switcher does **not**
stop this optional unit):

```bash
systemctl --user stop alienware-vllm-qwen35-27b-ctx.service
./scripts/set-alienware-model-mode.sh fast
```

`set-alienware-model-mode.sh fast` stops the strong unit and restarts the fast
unit so `homelab-fast` behavior matches your existing automation again.
