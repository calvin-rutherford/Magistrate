# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Frontend-specific instructions live in `frontend/AGENTS.md`; read them before changing Expo code.
- Herdr exposes no conversation API; agent chat history is parsed from terminal snapshots by `parse_agent_history` in `gateway/app/herdr_client.py` (see its docstring for the marker conventions), mirrored in `frontend/src/services/ChatHistory.ts` for the captain `/captain/output` fallback — keep the two parsers in sync. A working agent's snapshot can be transiently empty (alternate screen/redraw), so consumers must tolerate empty output.
- Tool-call visibility in chat history uses the single persisted key `magistrate.chat.show-tool-calls` (`frontend/src/services/ChatPreferences.ts`); do not introduce a second key.
- Claude's harness renders tool activity as *unmarked* rows set off by a blank line, and herdr snapshots catch status overlays mid-frame (sometimes overwriting a message row) — both parsers must drop chrome and type tool rows, or tool output leaks into chat as prose. Verify parser changes against a real snapshot (`HerdrClient.read_agent_output`), not only fixtures.
- `gateway/app/firstmate_client.py`'s `get_attention_items` reads the live `fm-fleet-snapshot.sh --json` schema: captain-attention signal lives at `task.hints.pending_decision` / `hints.blocked_event`, with the actual keyed decisions in `hints.open_decisions` (`{key, verb, summary}`, verb `needs-decision` or `blocked`). Tasks carry only ids; join against `snapshot.backlog.records` for a human-readable title.
- `/api/v1/recent-activity` merges two real sources: fleet-snapshot task records (`backlog.records` + `secondmate_landed.records`, request/completion dates are date-only) and gh-axi merged PRs (relative timestamps like `2h ago`, normalized in `gateway/app/github_service.py`). See `gateway/app/recent_activity.py` for the merge/dedup rules.
- `frontend/src/services/ConversationSession.ts` is an in-memory-only store (no `AsyncStorage`/`localStorage`): every target's thread starts empty on a full app reload, and there is no "New Session" control. Chat and Voice Mode share the `'captain'` target in-session only. `frontend/src/realtime/socket.ts` (`RealtimeClient`/`ws://…/api/v1/events`) is unused dead scaffolding: the gateway has no such WebSocket route, so live chat updates instead come from `ChatCanvas`'s `syncFromHistory` poll (diffs a small bounded `fetchAgentHistory` read by `role|kind|text` against a `knownKeysRef` set and appends anything new, never replaying existing backlog) — reuse that poll rather than reviving the socket unless a real gateway event stream is built first.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
