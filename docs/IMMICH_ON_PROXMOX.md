# Immich on Proxmox

This is a first deployment scaffold for adding Immich to the homelab through the
agent PR workflow.

## What this scaffold includes

- a pinned `docker-compose.yml` for Immich server, machine learning, Valkey, and
  the recommended PostgreSQL image
- a `.env.example` with the paths and variables that need local customization
- guidance for deploying behind the existing reverse proxy after human review

This scaffold does **not** claim that Immich is already deployed. It is the repo
shape we want to review before turning it into a live service.

## Planned host shape

Recommended target:

- Proxmox LXC or VM with SSD-backed local storage
- `UPLOAD_LOCATION=/srv/immich/library`
- `DB_DATA_LOCATION=/srv/immich/postgres`
- reverse proxy route such as `immich.dev-path.org` -> `:2283`

## Deployment notes

1. Copy `compose/immich/.env.example` to `compose/immich/.env` on the target host.
2. Replace `DB_PASSWORD` with a generated secret from the machine-secrets store.
3. Make sure the database path is on local disk, not NFS/SMB.
4. Review Immich release notes before bumping `IMMICH_VERSION`.
5. After deployment, add Immich to the inventory and observability files so it can
   sync into the shared memory layer.

## Runbook starter

```bash
cd compose/immich
cp .env.example .env
$EDITOR .env
docker compose up -d
```

After the service is healthy, add the proxy route and verify that the web UI and
background jobs both come up cleanly.
