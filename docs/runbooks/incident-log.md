# Incident log

Append-only chronological log of cross-cutting incidents. **Newest entry
at the top.** Per-runbook incidents (where the symptom and fix live
entirely inside one service) go into that service's runbook §Past
incidents — only put things here that touch multiple systems or that
deserve a proper post-mortem.

Format:

```
## YYYY-MM-DD — <one-line summary>

- **Detected by:** how we noticed (alert? user report? scheduled scan?)
- **Impact:** what was broken, for whom, for how long
- **Timeline (UTC):**
  - HH:MM — what happened / what we did
- **Root cause:** the real cause, not the immediate trigger
- **Fix:** what stopped the bleeding
- **Followups:** code changes, runbook updates, alerts added, tests added
- **Runbooks updated:** [foo.md](foo.md) §Past incidents
```

---

_No cross-cutting incidents recorded yet._
