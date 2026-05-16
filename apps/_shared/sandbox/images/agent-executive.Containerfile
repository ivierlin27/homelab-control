# Sandbox image for agent:executive.
#
# Inherits the locked-down base. Adds the agent's runtime deps.
# Build via: python3 -m apps._shared.sandbox build --principal agent:executive

FROM agent-base:latest

USER root
RUN python3 -m pip install --no-cache-dir --break-system-packages \
        discord.py \
        httpx
USER agent
