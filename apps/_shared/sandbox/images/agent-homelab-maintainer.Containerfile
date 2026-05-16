# Sandbox image for agent:homelab-maintainer.
#
# Inherits the locked-down base. Adds the deps the maintainer needs to read
# inventory, talk to Planka/Forgejo, and call the model gateway.

FROM agent-base:latest

USER root
RUN python3 -m pip install --no-cache-dir --break-system-packages \
        httpx \
        jsonschema
USER agent
