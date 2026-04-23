# Secrets Model

## Human secrets

Stored in Vaultwarden:

- personal passwords
- shared family credentials
- recovery codes
- break-glass instructions

## Machine secrets

Stored in the machine secret manager:

- Proxmox API tokens
- Forgejo deploy keys
- review/author agent SSH material
- gateway credentials
- service passwords

## Delivery

- render to `/run/homelab-control/*.env`
- keep files mode `0600`
- restart services after rotation

## Break-glass

- recovery path kept in Vaultwarden
- optional offline encrypted copy
- SOPS/age allowed only for bootstrap or DR exports
