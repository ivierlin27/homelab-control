# Bakeoff Task: Inventory to Memory Sync

## Objective

Design the next small homelab project:

Take the declared homelab service inventory and make it available in the shared
memory system in a form that agents can query and update safely.

## Why this is a good bakeoff task

This task exercises the exact things a strong model should be good at:

- reading several related repo files
- understanding an existing architecture
- proposing an incremental implementation
- respecting agent boundaries and safety rules
- turning structured YAML into durable memory records

## Repo context

The current repo already has:

- a declared service inventory in `inventory/services.yaml`
- observability expectations in `inventory/observability.yaml`
- memory principal rules in `config/memory/principals.yaml`
- operator utilities in `apps/homelab_operator/main.py`

The missing piece is a safe path to store or refresh that service inventory in
the memory system so future agents can use it without reparsing repo files every
time.

## Expected model task

Given the repo context above, the model should propose:

1. a practical implementation plan
2. the memory shape or schema for service records
3. how refreshes should work when inventory files change
4. how principals and ownership should be handled
5. what tests or verification would prove the feature works

## Strong answer characteristics

A strong answer should:

- keep Git as the source of truth for declared inventory
- treat the memory system as a derived, query-friendly representation
- avoid hand-wavy "just sync it" language
- define how existing services, observability facts, and endpoints map into
  memory records
- explain who is allowed to refresh or overwrite that memory
- propose an incremental change instead of a full architecture rewrite

## Weak answer characteristics

A weak answer will usually:

- ignore the existing inventory files
- assume memory should become the new source of truth
- skip access-control or principal concerns
- propose broad new infrastructure without justification
- ignore refresh behavior, deduplication, or provenance

## Acceptance criteria for the real project

If we implement this project for real, the minimum acceptance bar should be:

- a repeatable command or workflow can read `inventory/services.yaml`
- each service becomes a structured memory record with provenance
- observability information is included or linked
- re-running the sync updates records idempotently
- the principal that writes these records is clearly defined
- the resulting memory entries are useful for later agent queries

## Suggested output format for bakeoff runs

Ask each candidate model to produce:

1. a one-paragraph summary
2. a numbered implementation plan
3. a proposed memory record shape
4. risks and edge cases
5. a verification plan

## Suggested evaluator prompt

Use this as the human evaluator question after each run:

"Would I trust this response as the starting plan for a real PR to add service
inventory records into the shared memory system?"
