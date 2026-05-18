# CI (GitHub Actions + nightly Alienware)

Two test-runner tracks keep `homelab-control` honest:

1. **On every push** — GitHub Actions runs the pytest suite against
   Python 3.14 on `ubuntu-latest`. Configured at
   `.github/workflows/ci.yml`. Forgejo Actions has an equivalent
   workflow at `.forgejo/workflows/ci.yml` that activates once an
   `act_runner` is registered against forgejo.dev-path.org (today: no
   runner, so the file is dormant but valid).
2. **Nightly on Alienware** — `alienware-nightly-tests.timer` runs the
   suite at 03:23 PT every night against the real Python 3.14 venv on
   the deploy host. Catches dependency drift and "API moved under us"
   regressions that CI's pinned-deps environment misses. Posts the
   tail of the failure to the same Discord webhook the health monitor
   uses, only on failure.

## Setup

GitHub Actions: nothing to do — the workflow runs as soon as the file
is pushed. Confirm at
`https://github.com/ivierlin27/homelab-control/actions`.

Nightly on Alienware (one-time):

```bash
mkdir -p ~/.config/systemd/user
ln -sf ~/git/homelab-control/systemd/alienware-nightly-tests.service \
       ~/.config/systemd/user/
ln -sf ~/git/homelab-control/systemd/alienware-nightly-tests.timer \
       ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now alienware-nightly-tests.timer
systemctl --user list-timers alienware-nightly-tests.timer
```

## Symptoms → likely causes

| Symptom | Likely cause | First check |
|---|---|---|
| GH Actions red but `pytest -q` green locally | python version mismatch (we pin 3.14); OR a dep was upgraded only in `requirements-test.txt` | open the failing run; check setup-python step + pip log |
| Nightly tests Discord alert | dep drift; OR upstream API changed; OR network blip during a test that talks to a real URL | `journalctl --user -u alienware-nightly-tests.service -n 200` |
| Import error on push | new test file shares a basename without `__init__.py` AND `pytest.ini` was removed | re-add `pytest.ini` with `addopts = --import-mode=importlib` (see Past incidents 2026-05-17) |
| `module 'X' not found` in CI but works locally | `requirements-test.txt` doesn't include it | add the dep, pin to the version Alienware's `.venv` uses |
| Tests take > 5 min | a test gained a real network call; OR a fixture leaks state across files | `pytest --durations=10 -q` to surface the slow tests |

## Investigation steps

1. **CI red**: open the failing Actions run; scroll to the pytest step
2. **Locally repro**: `cd ~/git/homelab-control && .venv/bin/python -m pytest <node-id> -xvs`
3. **Specific test stuck on import**: `pytest --co <file>` — collection-only confirms import-time errors
4. **Coverage of a slice**: `pytest apps/health_monitor/ -v`

## Recovery

- **Flaky test (real**, not deps): mark `@pytest.mark.flaky` or fix the test (preferred). NEVER skip without an issue link in the docstring.
- **Dep drift**: pin the breaking dep in `requirements-test.txt` to the last working version, open an issue to bump intentionally later.
- **GH Actions runner outage**: ignore; nightly will catch real issues. Don't disable.

## Past incidents

### 2026-05-17 — pytest collection errors: 5 files named `test_main.py`

- **Symptom:** `pytest` against the whole repo errored with `import file mismatch: imported module 'test_main' has this __file__ attribute … which is not the same as the test file we want to collect`
- **Root cause:** Python's default import-mode treats `test_main.py` as the module `test_main`. Five `apps/<app>/test_main.py` files all want that name. Without `__init__.py` in each directory, the first one to load wins; the rest error.
- **Fix:** added root `pytest.ini` with `addopts = --import-mode=importlib`. Resolves any same-basename collision without needing `__init__.py` files everywhere.
- **Followup:** this runbook; ci.yml runs `pytest -q` from the repo root which picks up pytest.ini automatically

## Future work

- **Forgejo act_runner**: provision one (LXC or Alienware service) so PRs on Forgejo get CI feedback too. Today we rely on GitHub. Recipe when ready:
  1. Pick a host (Alienware fine; a dedicated LXC is cleaner — needs docker).
  2. Download `act_runner` from `code.forgejo.org/forgejo/runner/releases`.
  3. Forgejo admin → Site Administration → Runners → Create new runner; copy the token.
  4. `act_runner register --no-interactive --instance https://forgejo.dev-path.org --token <token> --name <hostname>`.
  5. Wrap in a systemd service (the binary supports `daemon` mode). Same pattern as other `alienware-*.service` units.
  6. Verify by pushing any commit to phase-0-platform — `.forgejo/workflows/ci.yml` should dispatch automatically.
  7. Add `act_runner` to inventory + a check to `apps/health_monitor` so we notice if it dies.
- **Coverage report**: nice-to-have but not essential — we'd want it surfaced on the dashboard.
- **Lint job**: ruff/mypy gates would catch a lot of "works in unit tests but breaks in live" issues. Defer until we have more code.
