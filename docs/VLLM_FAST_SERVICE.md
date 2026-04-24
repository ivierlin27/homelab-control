# vLLM Fast Service

The Alienware host can run a parallel `vllm` backend for fast-route experiments
without replacing the current LM Studio setup.

## Why parallel

- Fedora 43 + rootless Podman + the RTX 3090 can run `vllm`
- the current LM Studio library is GGUF-based, which `vllm` cannot reuse directly
- a separate `vllm` service lets us test Hugging Face-compatible models safely

## Install

On the Alienware host:

```bash
./scripts/install-alienware-vllm-fast.sh
```

This creates:

- `~/.config/systemd/user/alienware-vllm-fast.service`
- `~/.config/homelab-control/vllm-fast.env`

## Default model

The installer defaults to:

- `Qwen/Qwen2.5-7B-Instruct`

This is intended as a first fast-route candidate for the 3090.

## Gateway wiring

The LiteLLM config exposes a parallel alias:

- `homelab-fast-vllm`

Set these in `~/.config/homelab-control/model-gateway.env` on Alienware:

```bash
HOMELAB_FAST_VLLM_API_BASE=http://host.containers.internal:8000/v1
HOMELAB_FAST_VLLM_API_KEY=<same value as VLLM_FAST_API_KEY>
```

This keeps `homelab-fast` on LM Studio while enabling `homelab-fast-vllm` for
comparison.

## Operations

Check status:

```bash
systemctl --user status alienware-vllm-fast.service
```

Tail logs:

```bash
journalctl --user -u alienware-vllm-fast.service -f
```
