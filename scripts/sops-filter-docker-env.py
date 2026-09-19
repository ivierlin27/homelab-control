#!/usr/bin/env python3
"""Strip image-default env vars from a docker inspect Env dump (KEY=value per line)."""
from __future__ import annotations

import sys

DROP_EXACT = {
    "PATH",
    "HOSTNAME",
    "HOME",
    "TERM",
    "USER",
    "NODE_VERSION",
    "YARN_VERSION",
    "POSTHOG_API_KEY",
    "INTERCOM_ID",
    "CAPTCHA_SITE_KEY",
    "DD_GIT_REPOSITORY_URL",
    "DD_GIT_COMMIT_SHA",
    "PORT",
    "HOST",
    "HTTPS_ENABLED",
    "STANDALONE_BUILD",
    "STANDALONE_MODE",
    "NODE_OPTIONS",
    "ChrystokiConfigurationPath",
    "TELEMETRY_ENABLED",
    "GITEA_CUSTOM",
    "DEBIAN_FRONTEND",
    "ROCKET_PROFILE",
    "ROCKET_ADDRESS",
    "INFISICAL_PLATFORM_VERSION",
    "API_ENABLED",
}

def main() -> None:
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0]
        if key in DROP_EXACT:
            continue
        print(line)


if __name__ == "__main__":
    main()
