# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Frontend-specific instructions live in `frontend/AGENTS.md`; read them before changing Expo code.
- Herdr exposes no conversation API; agent chat history is parsed from terminal snapshots by `parse_agent_history` in `gateway/app/herdr_client.py` (see its docstring for the marker conventions).
- `gateway/app/firstmate_client.py`'s `get_attention_items` reads the live `fm-fleet-snapshot.sh --json` schema: captain-attention signal lives at `task.hints.pending_decision` / `hints.blocked_event`, with the actual keyed decisions in `hints.open_decisions` (`{key, verb, summary}`, verb `needs-decision` or `blocked`). Tasks carry only ids; join against `snapshot.backlog.records` for a human-readable title.
- `/api/v1/recent-activity` merges two real sources: fleet-snapshot task records (`backlog.records` + `secondmate_landed.records`, request/completion dates are date-only) and gh-axi merged PRs (relative timestamps like `2h ago`, normalized in `gateway/app/github_service.py`). See `gateway/app/recent_activity.py` for the merge/dedup rules.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
