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

1. Open the **`homelab`** project (separate from `finance` agent scopes).
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

### Step 4 — Alienware config (primary operator host)

Homelab automation runs on **Alienware**; config lives in
`~/.config/homelab-control/` (not required on your Mac).

```bash
ssh alienware
mkdir -p ~/.config/homelab-control
cd ~/git/homelab-control && git pull

# Infisical CLI (user install, no sudo)
mkdir -p ~/bin
# if missing: see scripts/bootstrap-infisical-cli.sh

cp config/env/npm.config.example ~/.config/homelab-control/npm.config
cp config/env/infisical-homelab.env.example ~/.config/homelab-control/infisical-homelab.env
chmod 600 ~/.config/homelab-control/infisical-homelab.env
```

Edit **`npm.config`**: set `INFISICAL_PROJECT_ID` = homelab project UUID.

Edit **`infisical-homelab.env`**: set `INFISICAL_TOKEN` = machine identity token for the
**homelab** project (finance agent tokens cannot read this project).

Create the token: Infisical → **homelab** project → **Access** → **Machine Identities**
→ create → allow read on `/homelab/npm` → copy token once.

Ensure `~/bin` is on `PATH` (add to `~/.bashrc` if needed):

```bash
export PATH="$HOME/bin:$PATH"
```

---

### Step 5 — Verify Infisical → NPM login (on Alienware)

```bash
ssh alienware
export PATH="$HOME/bin:$PATH"
set -a && source ~/.config/homelab-control/infisical-homelab.env && source ~/.config/homelab-control/npm.config && set +a

# Keys only (not values)
infisical export --domain https://infisical.dev-path.org \
  --token "$INFISICAL_TOKEN" --projectId "$INFISICAL_PROJECT_ID" \
  --env prod --path /homelab/npm --format dotenv | cut -d= -f1

cd ~/git/homelab-control
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
