# Fava + Authentik (forward auth via NPM)

Protect `https://fava.dev-path.org` with the same Authentik realm as the rest of
the homelab. Fava stays read-only on Alienware; Authentik only gates HTTP access.

**Prerequisites**

- NPM proxy host for `fava.dev-path.org` → `http://192.168.1.45:5002` (see
  `scripts/deploy_npm_fava_proxy.sh` or NPM UI).
- Authentik reachable at `https://authentik.dev-path.org` (or your IdP URL).
- Embedded **Proxy outpost** assigned to the default outpost (Applications →
  Outposts).

---

## 1. Authentik — Proxy Provider

1. **Applications → Providers → Create** → **Proxy Provider**.
2. **Name:** `fava-forward-auth`
3. **Authorization flow:** default provider authorization (or your standard flow).
4. **Mode:** **Forward auth (single application)**.
5. **External host:** `https://fava.dev-path.org`
6. **Advanced (optional):**
   - Cookie domain: `dev-path.org` (if you use shared cookies across subdomains).
   - Do **not** enable “Send HTTP Basic authentication” unless Fava needs it (it does not).
7. Save.

Note the provider slug; you will bind it to an Application next.

---

## 2. Authentik — Application

1. **Applications → Applications → Create**.
2. **Name:** `Fava (finance ledger)`
3. **Slug:** `fava`
4. **Provider:** `fava-forward-auth` (from step 1).
5. **Launch URL:** `https://fava.dev-path.org`
6. **Policy / group bindings:** restrict to **Kevin** (and anyone else who should
   see the household ledger). Example:
   - Policy: “Require group `homelab-admins`” or explicit user binding.
7. Save.

---

## 3. Authentik — Outpost

1. **Applications → Outposts →** open **authentik Embedded Outpost** (or your proxy outpost).
2. **Applications:** add **Fava (finance ledger)**.
3. Confirm the outpost is **healthy** and listening (embedded outpost uses the
   Authentik server HTTP port, typically `:9000` on the Authentik host).

**Find the outpost upstream for NPM**

From a host that can reach Authentik internally:

```bash
# Example — replace with your Authentik container/LXC IP
curl -s -o /dev/null -w "%{http_code}\n" http://<AUTHENTIK_IP>:9000/-/health/live/
```

Use `http://<AUTHENTIK_IP>:9000` in the NPM snippet below (not the public HTTPS URL,
unless hairpin NAT is known-good).

---

## 4. NPM — Enable forward auth (Advanced tab)

1. Open **Nginx Proxy Manager** → **Hosts → Proxy Hosts**.
2. Edit **`fava.dev-path.org`** (or rely on deployed `18.conf` and add Advanced via UI).
3. Open the **Advanced** tab.
4. Paste the contents of
   `config/nginx-proxy-manager/fava-authentik-advanced.conf` from homelab-control.
5. Replace `AUTHENTIK_UPSTREAM` with your Authentik internal base, e.g.:
   - `http://192.168.1.XX:9000` (embedded outpost on Authentik server)
6. Save. NPM reloads nginx automatically when saved through the UI.

If you deployed only via `deploy_npm_fava_proxy.sh` (file copy), either:

- Add the same Advanced block through the NPM UI (recommended — keeps UI in sync), or
- Merge the auth snippets into `config/nginx-proxy-manager/fava.dev-path.org.conf`
  manually and re-run the deploy script.

Official reference: [Authentik nginx / NPM template](https://docs.goauthentik.io/add-secure-apps/providers/proxy/server_nginx/).

---

## 5. Smoke test

1. Open a private window → `https://fava.dev-path.org`
2. Expect redirect to Authentik login, then Fava loads with your ledger.
3. Confirm **read-only** behavior (no edit controls that mutate Beancount files).

```bash
# From Alienware — upstream still local
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5002/

# Through NPM (no auth) — 200/302 before Authentik; 302 to login after Authentik enabled
curl -sk -o /dev/null -w "%{http_code}\n" --resolve fava.dev-path.org:443:192.168.1.42 https://fava.dev-path.org/
```

---

## 6. Troubleshooting

| Symptom | Check |
|--------|--------|
| NPM host **Offline** | Alienware `systemctl --user status alienware-fava.service`; `curl http://192.168.1.45:5002/` |
| **502** after Authentik | `AUTHENTIK_UPSTREAM` wrong; outpost not in Application list |
| **421** / HTTP2 issues | Disable HTTP/2 for this proxy host in NPM SSL tab |
| Login loop | External host in Provider must exactly match `https://fava.dev-path.org` |
| `upstream sent too big header` | `proxy_buffer_size 32k` in Advanced (included in template) |

---

## Related

- `docs/runbooks/fava.md` — Fava podman service on Alienware
- `docs/runbooks/agent-finance.md` — categorize / Planka defer
- `config/nginx-proxy-manager/fava.dev-path.org.conf` — baseline NPM server block
