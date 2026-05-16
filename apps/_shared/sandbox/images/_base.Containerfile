# Locked-down base for every agent sandbox.
#
# After the first pull on a new host, replace the tag below with the digest
# reported by `podman image inspect debian:bookworm-slim --format '{{.Digest}}'`
# and commit the change so rebuilds across hosts are byte-identical.
# Format: FROM docker.io/library/debian:bookworm-slim@sha256:<digest>
FROM docker.io/library/debian:bookworm-slim

ARG AGENT_UID=1000
ARG AGENT_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HISTFILE=/dev/null \
    LESSHISTFILE=/dev/null \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        git \
        python3 \
        python3-pip \
        python3-venv \
        python3-yaml \
        tini \
        tzdata \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

RUN groupadd --gid "${AGENT_GID}" agent \
    && useradd --create-home --uid "${AGENT_UID}" --gid "${AGENT_GID}" \
        --shell /usr/sbin/nologin agent

WORKDIR /work
RUN chown -R agent:agent /work

USER agent

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/sh", "-c", "echo 'sandbox base image; provide a command via run()' >&2; exit 64"]
