# Proposal: make SearXNG a first-class agent search tool

## Goal

Proposal task: incorporate SearXNG as a first-class search tool in memory and agent flows.

Goal: agents should know SearXNG exists, how to query it, and when to use it alongside memory lookup.

Acceptance criteria:

- Document SearXNG endpoint and JSON query pattern.
- Add agent guidance: search memory first for internal context, use SearXNG for current/public web context, then synthesize with citations/provenance.
- Update link-intake proposal so SearXNG is used for related context during analysis.
- Add a small smoke test for `https://search.dev-path.org/search?q=...&format=json`.
- Avoid writing durable memory from search results without human review.
- Consider exposing SearXNG through MCP or a simple local tool wrapper for agents.

## Human Feedback

- No comments.

## Executable First Slice

Create or update this SearXNG agent search document as the first reviewable slice.
After this lands, the next card should implement the concrete workflow, service, or code changes described here.

## Review Gate

Do not write durable memory or deploy runtime changes until the proposal has been reviewed.
