Alert: `alienware-model-gateway.service` failed health check at 2026-05-26T14:02Z.

Recent logs:
```
ERROR: LiteLLM proxy: Connection error calling homelab-strong-long-vllm
httpx.ConnectError: [Errno 111] Connection refused 192.168.1.45:8002
```

inventory/services.yaml excerpt:
```yaml
- name: model-gateway
  host: alienware
  port: 4000
- name: vllm-strong-long
  host: alienware
  port: 8002
  systemd_unit: alienware-vllm-strong-long.service
```

What is the most likely root cause and what should the operator check first?
