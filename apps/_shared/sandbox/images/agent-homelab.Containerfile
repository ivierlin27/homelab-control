# Sandbox image for agent:homelab (author agent).
#
# Inherits the locked-down base. Author agent needs git + openssh-client to
# push to Forgejo over SSH; everything else stays out.

FROM agent-base:latest

USER root
RUN apt-get update \
    && apt-get install --no-install-recommends -y openssh-client \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir --break-system-packages httpx
USER agent
