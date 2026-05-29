# Authentik (homelab IdP) — deploy

Authentik is required for Fava SSO (`fava.dev-path.org`) and planned for
Planka / Forgejo / Khoj. Until it runs, forward-auth on NPM will 502.

## Recommended host

**homelab-operator** LXC (`192.168.1.73`, CT 204) — Docker already installed,
62G+ free on **memory-engine** (`192.168.1.69`) is an alternative if you prefer
co-locating with Planka/Khoj.

## Install (operator, on the LXC)

```bash
ssh root@proxmox.dev-path.org
pct exec 204 -- bash -lc '
  set -euo pipefail
  mkdir -p /opt/authentik && cd /opt/authentik
  curl -fsSL -o compose.yml https://docs.goauthentik.io/compose.yml
  if [[ ! -f .env ]]; then
    echo "PG_PASS=$(openssl rand -base64 36 | tr -d "\n")" >> .env
    echo "AUTHENTIK_SECRET_KEY=$(openssl rand -base64 60 | tr -d "\n")" >> .env
  fi
  docker compose pull
  docker compose up -d
'
```

1. Open `http://192.168.1.73:9000/if/flow/initial-setup/` and create the admin user.
2. Create an **API token** (Admin → Directory → Tokens) for automation, or a
   machine identity token. Store in Infisical `/homelab/authentik` as
   `AUTHENTIK_API_TOKEN`.
3. Add NPM proxy host `authentik.dev-path.org` → `http://192.168.1.73:9000` (UI or API).

## Alienware config

`~/.config/homelab-control/npm.config`:

```bash
AUTHENTIK_UPSTREAM=http://192.168.1.73:9000
```

Optional `~/.config/homelab-control/authentik.config`:

```bash
AUTHENTIK_URL=https://authentik.dev-path.org
AUTHENTIK_API_TOKEN=<from Infisical or UI>
```

## Enable Fava SSO (after Authentik is up)

```bash
cd ~/git/homelab-control
export PATH="$HOME/bin:$PATH"
set -a && source ~/.config/homelab-control/npm.config \
  && source ~/.config/homelab-control/infisical-homelab.env && set +a

./scripts/authentik_ensure_fava_proxy.sh   # Provider + Application via API
# Add app to Embedded Outpost in UI (one click)
./scripts/npm_apply_fava_authentik.sh      # NPM Advanced via API
```

Or: `./scripts/fava_enable_authentik.sh`

## Verify

Private window → `https://fava.dev-path.org` → Authentik login → Fava ledger.
