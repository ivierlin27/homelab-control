# PR Workflow

1. Author agent creates or updates a Planka card.
2. Card moves to `Approved To Execute`.
3. Author agent works on a branch and opens a PR/MR.
4. Card moves to `Author Review Ready`.
5. Review agent evaluates the PR/MR using `apps/review_agent/main.py`.
6. Outcome:
   - `approve_and_merge`
   - `request_changes`
   - `needs_human_review`
7. On merge, automation moves the card to `Merged / Applied` and then `Done`.

## Branch protection

- `main` is protected
- direct pushes to `main` are disabled for agents
- at least one review is required
- review agent is the default first reviewer for agent-authored work
