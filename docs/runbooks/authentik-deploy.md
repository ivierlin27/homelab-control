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
2. Add NPM proxy host `authentik.dev-path.org` → `http://192.168.1.73:9000` (UI or API)
   **before** creating API tokens if you want the Copy button to work (HTTPS).
3. Create an **API token** (see **Step 5 — API token** below). Store in Infisical
   `/homelab/authentik` as `AUTHENTIK_API_TOKEN`.

## Step 5 — API token (homelab automation)

**Where:** Admin interface → **Applications** → **Tokens** (or **Directory** → **Tokens**).

### “New Token” only asks for a name — that’s normal

Recent Authentik versions use **defaults** on create:

| Field | Default for admin-created tokens |
|--------|-------------------------------------|
| User | Your admin user (`akadmin`) |
| Intent | **API Access** |
| Expiring | Often **Yes** (~30 minutes unless changed) |

You set options **after** create: open the token row → **Edit** (pencil) → change
**Expiring** / expiry time, or disable expiry if your install allows it.

The UI **Edit** dialog often only changes the **name** — expiry is not editable there
in current Authentik. Use one of these instead:

**Option 1 — Use the short-lived token now (fastest)**  
Copy the key on HTTPS, paste into Infisical, and immediately run:

```bash
./scripts/authentik_ensure_fava_proxy.sh
./scripts/npm_apply_fava_authentik.sh
```

You only need the token alive for a few minutes.

**Option 2 — Create a non-expiring token via API** (while your current token still works)

```bash
export AUTHENTIK_API_TOKEN='ak-...'   # the token you just copied
export AUTHENTIK_URL=https://authentik.dev-path.org
./scripts/authentik_create_api_token.sh
```

Replace Infisical `AUTHENTIK_API_TOKEN` with the new key (identifier default:
`homelab-automation-long`, `expiring: false`).

**Option 3 — Allow non-expiring tokens from the UI (permanent)**  
**Directory → Users →** your admin user → **Attributes** → add JSON:

```json
{
  "goauthentik.io/user/token-expires": false
}
```

Delete the old token, **New Token** again — new tokens should show **Expiring: No**.

### Copy button broken (“Clipboard not available”)

Browsers **block clipboard** on plain **HTTP** (`http://192.168.1.73:9000`). This is
expected, not a misconfiguration.

**Workarounds (pick one):**

**A — Use HTTPS admin (best)**  
After NPM proxy exists, open **`https://authentik.dev-path.org`**, log in, go to
Tokens → Copy should work.

**B — View key in the browser (HTTP OK)**  
While logged in as admin, open a new tab:

```text
http://192.168.1.73:9000/api/v3/core/tokens/homelab-automation/view_key/
```

(Replace `homelab-automation` with your token **Identifier**.)

You should see JSON like `{"key":"ak-...."}`. Copy the `key` value manually into
Infisical (`AUTHENTIK_API_TOKEN`) or `~/.config/homelab-control/authentik.config`.

**C — From Alienware with curl** (after you have any valid API token once):

```bash
AUTHENTIK_URL=https://authentik.dev-path.org   # or http://192.168.1.73:9000
curl -sk -H "Authorization: Bearer $AUTHENTIK_API_TOKEN" \
  "$AUTHENTIK_URL/api/v3/core/tokens/homelab-automation/view_key/"
```

**Do not** commit the key to git.

### Store the token

Infisical → **homelab** project → **`/homelab/authentik`**:

```bash
AUTHENTIK_API_TOKEN=ak-...paste-key...
```

Optional on Alienware (`chmod 600`):

```bash
~/.config/homelab-control/authentik.config
```

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
