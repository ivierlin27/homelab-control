# Fava (finance ledger UI) — F7

Read-only [Fava](https://beancount.github.io/fava/) over `~/finance/ledger` on
Alienware. SSO at `https://fava.dev-path.org` is configured on the reverse
proxy (Authentik), not in this repo.

## Prerequisites

- Ledger initialized: `~/finance/ledger/main.beancount` exists and `bean-check` passes
- Podman on Alienware
- Reverse-proxy route `fava.dev-path.org` → `http://127.0.0.1:5002` (or your `FAVA_PORT`)

## Install

```bash
cd ~/git/homelab-control
cp config/env/fava.env.example ~/.config/homelab-control/fava.env
# edit FAVA_LEDGER_DIR / FAVA_PORT if needed

mkdir -p ~/.config/systemd/user
cp systemd/alienware-fava.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now alienware-fava.service
systemctl --user status alienware-fava.service   # active (exited) is normal
```

## Smoke test (LAN, before SSO)

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5002/
# expect 200 or 302
```

Open `http://127.0.0.1:5002/` on Alienware (or via SSH tunnel) and confirm
transactions load.

## NPM reverse proxy

**Preferred (updates NPM database + UI):** REST API via Infisical — see
`docs/runbooks/nginx-proxy-manager.md` (`NPM_IDENTITY` + `NPM_SECRET` → short-lived JWT per run).

```bash
# example: macOS Keychain after npm-keychain-store-token.sh
./scripts/npm_ensure_fava_proxy.sh
```

**Fallback (nginx only, no UI row):** `deploy_npm_fava_proxy.sh` without credentials
copies `config/nginx-proxy-manager/fava.dev-path.org.conf` to LXC **102**
(`192.168.1.42`), upstream `http://192.168.1.45:5002`.

Pi-hole should already point `fava.dev-path.org` → `192.168.1.42`.

## Authentik SSO

Fava is **read-only** on disk (`:ro` mount). Authentik only protects the web UI.

Step-by-step: **`docs/runbooks/fava-authentik.md`**

Snippet to paste into NPM Advanced:
`config/nginx-proxy-manager/fava-authentik-advanced.conf`

## Cleanup / ops

| Task | Command |
|------|---------|
| Restart Fava | `systemctl --user restart alienware-fava.service` |
| Logs | `podman logs homelab-fava` |
| Finance Planka smoke cleanup | `scripts/planka_cleanup_finance_test_cards.py` |
| Categorize defer Planka | `docs/runbooks/agent-finance.md` |

## Related

- Phase plan: `docs/plans/phase-1-finance.md` (F7)
- Inventory: `config/inventory/services/fava.yaml`
- Agent finance: `docs/runbooks/agent-finance.md`
