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

- author agent opens PR/MR
- review agent evaluates diff + checks + risk policy
- if clean and low risk, review agent merges
- if sensitive, novel, or ambiguous, review agent moves the task to human review

See:

- `config/policies/review-policy.yaml`
- `config/planka/board-template.yaml`
- `config/memory/principals.yaml`
