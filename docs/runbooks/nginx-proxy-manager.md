# Nginx Proxy Manager (homelab)

NPM runs on Proxmox LXC **102** (`192.168.1.42`). Admin UI: `https://nginx.dev-path.org`
(API on port **81**, LAN-only).

## How NPM auth works for automation

NPM does **not** give us a permanent API key in this setup. Scripts call:

```http
POST http://192.168.1.42:81/api/tokens
{"identity":"<admin email>","secret":"<admin password>"}
```

That returns a **short-lived JWT** (typically ~24h). Each run of
`npm_ensure_fava_proxy.sh` logs in again — we only store the **email + password** in
Infisical, never the JWT on disk.

Human NPM login → **Vaultwarden**. Machine copy for scripts → **Infisical**
([docs/SECRETS.md](../SECRETS.md)).

---

## Walkthrough: store NPM credentials in Infisical

### Step 0 — What you are storing

In Infisical folder **`/homelab/npm`** (environment **`prod`**), create three secrets:

| Secret key | Example | Notes |
|------------|---------|--------|
| `NPM_IDENTITY` | `you@dev-path.org` | Same email you use to log into NPM UI |
| `NPM_SECRET` | *(password)* | NPM admin password — machine secret only |
| `NPM_API_URL` | `http://192.168.1.42:81` | LAN API base (not the HTTPS UI URL) |

Do **not** store JWTs in Infisical; they expire and are recreated each script run.

---

### Step 1 — Open Infisical

1. Browser: `https://infisical.dev-path.org`
2. Sign in as admin (human password stays in Vaultwarden if you use it there too).

---

### Step 2 — Pick project and environment

1. Open the homelab project (the one other stacks use — same `INFISICAL_PROJECT_ID`
   as Forgejo / operator secrets).
2. Select environment **`prod`** (must match `INFISICAL_ENVIRONMENT` in scripts;
   default is `prod`).

If you do not know the project ID: **Project Settings →** copy **Project ID**
(UUID). You will put it in `npm.config` (non-secret).

---

### Step 3 — Create the secret folder

1. **Secrets** → ensure path **`/homelab/npm`** exists (create folder `homelab` then
   `npm` if needed).
2. Add the three keys from the table above.
3. Save each secret.

Optional: add a note on `NPM_SECRET` like “NPM admin — rotate with Vaultwarden entry”.

---

### Step 4 — CLI access on your Mac (operator)

Install CLI if needed: `brew install infisical/get-cli/infisical`

**One-time login** (stores Infisical session locally, not NPM password in shell profile):

```bash
infisical login --domain https://infisical.dev-path.org
```

**Non-secret config** on your Mac:

```bash
mkdir -p ~/.config/homelab-control
cp ~/git/homelab-control/config/env/npm.config.example ~/.config/homelab-control/npm.config
# Edit: set INFISICAL_PROJECT_ID=<uuid from step 2>
```

---

### Step 5 — Verify Infisical → NPM login (no secrets on disk)

```bash
export INFISICAL_PROJECT_ID="<your-project-uuid>"

# Print keys only (not values)
infisical export \
  --projectId "$INFISICAL_PROJECT_ID" \
  --env prod \
  --path /homelab/npm \
  --format dotenv | cut -d= -f1

# Login test: should print a long JWT string
infisical run \
  --projectId "$INFISICAL_PROJECT_ID" \
  --env prod \
  --path /homelab/npm \
  -- bash -c 'source <(infisical export --projectId "$INFISICAL_PROJECT_ID" --env prod --path /homelab/npm --format dotenv); \
    curl -sk -X POST "$NPM_API_URL/api/tokens" -H "Content-Type: application/json" \
    -d "{\"identity\":\"$NPM_IDENTITY\",\"secret\":\"$NPM_SECRET\"}" | jq -r .token | head -c 40; echo ...'
```

Simpler — use homelab-control script (fetches from Infisical, logs in, calls API):

```bash
cd ~/git/homelab-control
export INFISICAL_PROJECT_ID="<uuid>"
./scripts/npm_ensure_fava_proxy.sh
```

You should see `npm_ensure_fava_proxy: OK — NPM proxy host id=...` and Fava in the NPM UI.

---

### Step 6 — Optional: render to `/run` on Linux (Alienware / operator VM)

For hosts that should not call Infisical on every ad-hoc command, render once into tmpfs:

```bash
export INFISICAL_PROJECT_ID="..."
export INFISICAL_TOKEN="..."   # machine identity token, not NPM password
./scripts/render-npm-env-from-infisical.sh
# → /run/homelab-control/npm.env  (mode 0600)
./scripts/npm_ensure_fava_proxy.sh
```

`INFISICAL_TOKEN` here is the **Infisical machine token** (how the CLI reads secrets),
not NPM’s JWT.

---

### Step 7 — `infisical run` (recommended for Mac — nothing written to disk)

```bash
infisical run \
  --projectId "$INFISICAL_PROJECT_ID" \
  --env prod \
  --path /homelab/npm \
  -- ./scripts/npm_ensure_fava_proxy.sh
```

Infisical injects `NPM_IDENTITY`, `NPM_SECRET`, and `NPM_API_URL` into the process
environment only; the script exchanges them for a JWT and talks to NPM.

---

## File-only deploy vs API

| Method | Updates UI / DB |
|--------|-----------------|
| Copy `config/nginx-proxy-manager/*.conf` | No |
| **REST API** (`npm_ensure_fava_proxy.sh`) | Yes |

`deploy_npm_fava_proxy.sh` uses the API when Infisical (or `/run`) credentials resolve.

## Rotation

1. Change NPM password in NPM UI.
2. Update `NPM_SECRET` in Infisical `/homelab/npm`.
3. Update Vaultwarden human entry if you mirror it there.
4. Re-run `./scripts/npm_ensure_fava_proxy.sh` — no script changes needed.

## Fava + Authentik

Proxy host via API above; forward-auth: [fava-authentik.md](fava-authentik.md).
