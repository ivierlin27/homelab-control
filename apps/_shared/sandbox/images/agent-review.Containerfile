# Sandbox image for agent:review.
#
# Inherits the locked-down base. Review agent reads PRs and writes review
# comments via the Forgejo API. No write access to repos beyond review verbs.

FROM agent-base:latest

USER root
RUN python3 -m pip install --no-cache-dir --break-system-packages httpx
USER agent
