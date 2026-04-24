# Inventory Memory Sync

This is the first real implementation slice for turning the declared homelab
service inventory into query-friendly memory records.

## What it does

The operator now supports:

- `inventory-memory-export` to derive stable service records from Git-backed
  inventory files
- `inventory-memory-sync` to post changed records into the existing
  memory-engine ingest workflow

The source of truth remains:

- `inventory/services.yaml`
- `inventory/observability.yaml`

The memory system is a derived representation for later agent queries.

## Record shape

Each service becomes one derived record with:

- service id, host, type, role, and repo
- observability profile
- required and missing observability checks
- endpoints when declared
- provenance back to the source files
- a stable fingerprint for change detection

Each synced record is sent with:

- `principal=agent:homelab` by default
- `source=operator`
- `command_or_api=homelab_operator:inventory-memory-sync`
- a stable `record_key` such as `homelab.service.forgejo`

## Usage

Preview the derived records without posting anything:

```bash
python3 apps/homelab_operator/main.py inventory-memory-export --format json
```

Preview the sync payloads:

```bash
python3 apps/homelab_operator/main.py inventory-memory-sync --dry-run --format json
```

Run a real sync against the memory-engine ingest webhook:

```bash
export MEMORY_ENGINE_INGEST_URL="https://n8n.dev-path.org/webhook/ingest"
python3 apps/homelab_operator/main.py inventory-memory-sync
```

## Idempotence

The current memory-engine ingest workflow is append-oriented, so this first
slice keeps idempotence at the operator layer.

The operator stores the last synced fingerprint for each service in:

`~/.local/state/homelab-control/inventory-memory-sync-state.json`

When the inventory has not changed, the operator skips that service instead of
posting a duplicate record.

This keeps repeated sync runs useful today while leaving room for a later
memory-engine enhancement that can do deeper upsert or compaction behavior by
`record_key`.

## Verification

Useful checks:

```bash
python3 apps/homelab_operator/main.py inventory-memory-sync --dry-run
python3 -m unittest discover -s apps/homelab_operator -p "test_*.py"
```

The unit tests cover:

- record derivation from the inventory files
- idempotent skipping on a second sync run
