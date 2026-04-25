# homelab-control

GitOps home for the Proxmox-based control plane around:

- self-hosted Git (`Forgejo`)
- human credentials (`Vaultwarden`)
- machine secrets (`Infisical`-class store)
- routed model gateway (`LiteLLM`)
- Planka orchestration
- homelab author / review agents
- future specialized agents

## Design rules

1. **No agent writes directly to `main`.**
2. **Every task starts on Planka.**
3. **Every agent has its own Git identity, secret scope, and memory principal.**
4. **Human passwords live in Vaultwarden; machine secrets do not.**
5. **A stable OpenAI-compatible gateway hides model/provider churn.**

## Repo layout

- `compose/` — deployable stack definitions by service group
- `config/` — policies, board templates, memory principals, gateway config
- `inventory/` — declared hardware, services, observability checks, goals
- `apps/` — author/review/operator CLIs and service code
- `scripts/` — bootstrap helpers and runtime secret rendering

## Intended rollout

1. Bootstrap secrets.
2. Bring up Forgejo.
3. Bring up Planka + board automation hooks.
4. Bring up LiteLLM gateway.
5. Run operator + author/review agent services against this repo.

## Secrets

Nothing in this repo should contain live credentials. Compose files expect
runtime env files rendered from the machine secret store into `/run/...`.

Examples:

- `/run/homelab-control/forgejo.env`
- `/run/homelab-control/infisical.env`
- `/run/homelab-control/model-gateway.env`
- `/run/homelab-control/agent-homelab.env`

## Review flow

- every task starts as a Planka card
- moving a card to `Plan Ready` asks the agent for a plan
- moving a card to `Approved To Execute` starts agent execution
- labels explain state and risk; label changes do not start work
- author agent opens a PR/MR
- review agent evaluates diff + checks + risk policy
- if human approval is needed, the card moves to `Needs Human Review`
- merging the PR moves the card to `Done`

See:

- `config/policies/review-policy.yaml`
- `config/planka/board-template.yaml`
- `config/memory/principals.yaml`
- `docs/AGENT_SERVICES.md`
- `docs/VLLM_FAST_SERVICE.md`
- `docs/VLLM_STRONG_SERVICE.md`
- `docs/STRONG_MODEL_STRATEGY.md`
- `docs/MODEL_OPTIMIZER_AGENT.md`
- `docs/INVENTORY_MEMORY_SYNC.md`
- `docs/IMMICH_ON_PROXMOX.md`
- `docs/HUMAN_INTERFACES.md`
