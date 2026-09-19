# Secrets Model

## Human secrets

Stored in **Vaultwarden** only:

- personal passwords
- shared family credentials
- recovery codes
- break-glass instructions

Nothing machine-read looks at Vaultwarden.

## Machine secrets

**SOPS + age** in this repo (`secrets/<name>.env`). Recipients are in `.sops.yaml`
(Mac operator + proxmox1/2/3 + proxnas). Private keys stay off git:

| Host | Private key |
|------|-------------|
| Mac | `~/.config/age/keys.txt` (`SOPS_AGE_KEY_FILE`) |
| each pve / proxnas | `/root/.config/sops/age/keys.txt` |
| node-key backup | `~/.config/age/homelab-nodes/node-privates.age` (operator-encrypted) |

Decrypt at deploy:

```sh
export SOPS_AGE_KEY_FILE=~/.config/age/keys.txt   # Mac
./scripts/sops-render.sh vaultwarden              # → /run/homelab-control/vaultwarden.env
```

On a Proxmox host, same script after `/opt/homelab-control` is synced (R-010), then
`pct push` the rendered file into the guest's `/run/homelab-control/`. Guest `/run`
is tmpfs; render again after a reboot if you recreate compose.

Harvested 2026-09-19 from live guests (not empty `/run/homelab-control`):

| File | Source |
|------|--------|
| `nebula-sync.env` | CT 204 `/opt/nebula-sync/.env` |
| `authentik.env` | CT 204 `/opt/authentik/.env` |
| `vaultwarden.env` | CT 202 container env |
| `forgejo.env` | CT 201 container env |
| `infisical.env` | CT 203 container env (archive; service retired) |
| `memory-engine.env` | CT 200 `/opt/memory-engine/.env` |
| `move-command-center.env` | CT 200 archive (move CC retired) |
| `nut.env` | proxnas `/root/.nut-monuser.pw` |

Alienware `~/.config/homelab-control/*.env` is **not** in SOPS — agent stack is frozen
(`memory/decisions/2026-09-16-alienware-agent-stack-frozen.md`).

## Infisical

Retired. CT 203 stopped. `scripts/render-env-from-infisical.sh` now wraps
`sops-render.sh`. Do not stand Infisical back up for machine secrets.

## Break-glass

- Vaultwarden for human recovery
- age-encrypted node-key bundle on the Mac + next B2 crown-jewel pass
