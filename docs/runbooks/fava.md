# Fava (finance ledger UI) — F7

Read-only [Fava](https://beancount.github.io/fava/) over `/opt/finance/ledger` on
**CT 107** (`fava`, `192.168.1.74`, proxmox2). SSO at `https://fava.dev-path.org`
is Caddy `forward_auth` to Authentik, not this repo.

Moved off Alienware 2026-09-19. The fedora unit `alienware-fava.service` is
disabled. Do not start it — Caddy points at `.74`, not `.45:5002`.

## Layout

| | |
|---|---|
| Guest | CT 107, debian-13, unprivileged, nesting=1, 512 MB, onboot |
| Compose | `/opt/fava/compose.yml` (also `config/fava/compose.yml` in this repo) |
| Ledger | `/opt/finance/ledger/main.beancount` (`:ro` in the container) |
| Listen | `0.0.0.0:5002` → container `:5000` |
| Image | digest-pinned in compose / `config/inventory/services/fava.yaml` |

The fedora copy at `~/finance/ledger` is leftover rollback, not what Fava reads.
Edits belong on CT 107 (or rsync them there) or the UI will go stale.

## Smoke test (LAN, before SSO)

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.1.74:5002/
# expect 302 to /…/income_statement/
```

Public: `https://fava.dev-path.org/` → Authentik, then Fava.

## Ops

Debian `docker.io` on this guest has no compose plugin. Recreate with:

```bash
docker rm -f homelab-fava
docker run -d --name homelab-fava --restart unless-stopped \
  -p 0.0.0.0:5002:5000 \
  -v /opt/finance/ledger:/bean:ro \
  -e BEANCOUNT_FILE=/bean/main.beancount \
  docker.io/yegle/fava@sha256:d94c2011d7ed9f0adf2576c640b8f0164586fe23f32d982b5b80259f102e025e
```

| Task | Command |
|------|---------|
| Restart | `pct exec 107 -- docker restart homelab-fava` (from proxmox2) |
| Logs | `pct exec 107 -- docker logs homelab-fava` |
| Authentik | `docs/runbooks/fava-authentik.md` |

## Related

- Phase plan: `docs/plans/phase-1-finance.md` (F7 — shipped)
- Inventory: `config/inventory/services/fava.yaml`
- Agent finance: `docs/runbooks/agent-finance.md`
