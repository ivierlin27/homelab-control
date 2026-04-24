# vLLM Strong Service

The Alienware host runs a dedicated `vllm` backend for the stable
`homelab-strong` route.

## Default model

The installer defaults to:

- `Qwen/Qwen2.5-14B-Instruct-AWQ`

This is the current best-fit strong profile for a single RTX 3090 on the
existing stack:

- official AWQ model from Qwen with mature `vllm` support
- materially stronger than the fast route without depending on a 32B-class KV
  cache budget
- small enough to keep a practical 32K context target on a 24 GB card once the
  runtime is tuned correctly

The previous 32B AWQ attempt remains worth revisiting after the `vllm` runtime
upgrade, but it did not start reliably on the current build even after tighter
KV cache settings.

## Install

On the Alienware host:

```bash
./scripts/install-alienware-vllm-strong.sh
```

This creates:

- `~/.config/systemd/user/alienware-vllm-strong.service`
- `~/.config/homelab-control/vllm-strong.env`

## Gateway wiring

Set these in `~/.config/homelab-control/model-gateway.env` on Alienware:

```bash
HOMELAB_STRONG_API_BASE=http://host.containers.internal:8001/v1
HOMELAB_STRONG_API_KEY=<same value as VLLM_STRONG_API_KEY>
```

The LiteLLM config points:

- `homelab-strong` -> `homelab-strong-vllm`

## Context-efficiency defaults

The strong service is configured around the practical limits of a 24 GB 3090:

- AWQ 4-bit model weights so the strong route stays inside consumer-GPU memory
  bounds
- native `32768` token context as the target working window for coding,
  troubleshooting, and multi-file planning
- FP8 KV cache (`VLLM_STRONG_KV_CACHE_DTYPE=fp8_e4m3`) plus
  `--calculate-kv-scales` to stretch context capacity without immediately
  falling back to a shorter static context cap
- `--enable-prefix-caching` to avoid recomputing repeated prompt prefixes
- `--enable-chunked-prefill` so long prompts can be admitted incrementally
- bounded batch sizes (`4096` batched tokens, `8` sequences) to reduce KV cache
  pressure on a single GPU

If the active runtime still fails to stabilize with the 32K target, reduce
`VLLM_STRONG_MAX_MODEL_LEN` before changing the model. The current priority is a
reliable strong route with predictable latency, not a theoretical maximum
context number.

## One-model mode

Only one `vllm` route should be active at a time on this GPU. Use:

```bash
./scripts/set-alienware-model-mode.sh fast
./scripts/set-alienware-model-mode.sh strong
./scripts/set-alienware-model-mode.sh status
```

## Operations

Check status:

```bash
systemctl --user status alienware-vllm-strong.service
```

Tail logs:

```bash
journalctl --user -u alienware-vllm-strong.service -f
```
