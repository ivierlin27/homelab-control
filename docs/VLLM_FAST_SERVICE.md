# vLLM Fast Service

The Alienware host runs `vllm` as the default backend for the stable
`homelab-fast` route.

## Why `vllm`

- Fedora 43 + rootless Podman + the RTX 3090 can run `vllm`
- the current LM Studio library is GGUF-based, which `vllm` cannot reuse directly
- `vllm` gives the fast route a dedicated OpenAI-compatible backend

## Install

On the Alienware host:

```bash
./scripts/install-alienware-vllm-fast.sh
```

This creates:

- `~/.config/systemd/user/alienware-vllm-fast.service`
- `~/.config/homelab-control/vllm-fast.env`

The service unit currently pins `docker.io/vllm/vllm-openai:v0.19.1` and runs
with `--ipc=host`, matching the current container guidance from the `vllm`
deployment docs.

## Default model

The installer defaults to:

- `Qwen/Qwen2.5-7B-Instruct`

This is the current `homelab-fast` backend.

## Gateway wiring

The LiteLLM config now points:

- `homelab-fast` -> `vllm`
- `homelab-fast-vllm` -> the same backend as an explicit alias

Set these in `~/.config/homelab-control/model-gateway.env` on Alienware:

```bash
HOMELAB_FAST_VLLM_API_BASE=http://host.containers.internal:8000/v1
HOMELAB_FAST_VLLM_API_KEY=<same value as VLLM_FAST_API_KEY>
```

## Context-efficiency defaults

The fast service uses the same safe efficiency baseline as the strong route:

- native `32768` token context rather than an artificially short cap
- `--ipc=host` so the container has the shared memory that `vllm` expects
- `--enable-prefix-caching` to reuse repeated system prompts and shared prefixes
- `--enable-chunked-prefill` so longer prompts do not monopolize admission
- bounded scheduler settings (`8192` batched tokens, `16` sequences)

The KV cache dtype remains configurable through `VLLM_FAST_KV_CACHE_DTYPE`. It
defaults to `auto` so the service stays conservative until we benchmark
model-specific FP8 KV cache behavior on this hardware.

`vllm` `v0.19.1` also exposes explicit CPU and KV offload settings, but the fast
route leaves those disabled by default. This keeps latency predictable and
avoids spending PCIe bandwidth on a model that already fits comfortably.

## One-model mode

The RTX 3090 in the Alienware host still runs in one-model mode. `homelab-fast`
and `homelab-strong` now both use `vllm`, but only one backend should be active
at a time.

Use the provided mode switcher on Alienware:

```bash
./scripts/set-alienware-model-mode.sh fast
./scripts/set-alienware-model-mode.sh strong
./scripts/set-alienware-model-mode.sh status
```

- `fast` starts the `alienware-vllm-fast.service`
- `strong` starts the `alienware-vllm-strong.service`
- `status` prints service state and current GPU memory usage

## Operations

Check status:

```bash
systemctl --user status alienware-vllm-fast.service
```

Tail logs:

```bash
journalctl --user -u alienware-vllm-fast.service -f
```
