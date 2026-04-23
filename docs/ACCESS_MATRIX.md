# Access Matrix

| Principal | Domain(s) | Git write | Git review | Memory read | Memory write | Human escalation |
|-----------|-----------|-----------|------------|-------------|--------------|------------------|
| `human:kevin` | all | yes | yes | yes | yes | n/a |
| `agent:homelab` | homelab | yes (`homelab/*`) | no | homelab only | homelab only | via review agent |
| `agent:review` | homelab | no author writes | yes | homelab only | review annotations only | yes |
| `agent:chinese` | learning | yes (`learning/*`) | no | learning only | learning only | yes |
| `agent:products` | products | yes (`products/*`) | no | products only | products only | yes |
| `agent:finance` | finance | yes (`finance/*`) | no | finance only | finance only | yes |

## Family onboarding

Before adding a family member:

1. create a dedicated forge account
2. map repo access explicitly
3. decide whether any shared project gets a dedicated agent
4. decide whether Vaultwarden collection access is needed
5. do **not** expand machine-secret scopes by default
