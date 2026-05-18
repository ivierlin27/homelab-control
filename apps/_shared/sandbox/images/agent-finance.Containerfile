# Sandbox image for agent:finance.
#
# Phase 1 MVP-B scope (see docs/plans/phase-1-finance.md):
#   - git + openssh-client: clone/push the (private) finance ledger repo from
#     the local Forgejo instance over SSH. Pinned key is mounted from the host
#     at sandbox launch.
#   - beancount: ledger validation and queries inside the sandbox so a
#     malformed import can't crash the agent process. `smart_importer` is
#     pinned (it's the categorizer's library; the LLM call is host-side only).
#   - pandas: CSV statement parsing for the per-institution importers.
#   - No httpx / no urllib3 in PATH for the agent user: the sandbox MUST NOT
#     reach the network at all (sandbox.network.allowed_hosts = []). Egress is
#     blocked at the runner level too, but defense-in-depth.

FROM agent-base:latest

USER root
RUN apt-get update \
    && apt-get install --no-install-recommends -y openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Beancount v3 + smart_importer pinned to known-good majors. The Phase 1 plan
# flagged the v2->v3 migration as a risk; pinning to v3 explicitly so the
# image bake captures the intended major.
RUN python3 -m pip install --no-cache-dir --break-system-packages \
        "beancount>=3,<4" \
        "smart_importer>=0.5,<1" \
        "pandas>=2,<3"

USER agent
