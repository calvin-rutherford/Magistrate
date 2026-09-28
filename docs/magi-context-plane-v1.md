# Magi persistent context plane v1

## Authority and scope

Magi context is provider-independent Gateway state. It does not use a model,
harness session, terminal transcript, or provider thread as memory authority.
Ordered schema migration 6 in `gateway/app/db.py` and the backend-portable
implementation in `gateway/app/project_memory.py` are authoritative. SQLite is
supported for one-instance development/test deployments; production uses the
shared PostgreSQL persistence seam in `gateway/app/persistence.py`.

Every memory entry and every mutation/retrieval audit row is qualified by:

1. authenticated `owner_user_id` (never accepted from request JSON);
2. an owner-derived tenant id;
3. the server-owned personal organization qualifier;
4. the project's persisted workspace id;
5. the persisted opaque project id; and
6. the persisted opaque repository id (empty means project-wide).

Repository retrieval may inherit project-wide entries in the **same**
owner/tenant/org/workspace/project. It never searches a sibling repository.
No other scope dimension has an inheritance rule. The API accepts only an
owner-bound project reference and optional repository reference; it derives all
other qualifiers and returns the same 404 for missing and foreign identities.
The current account model has one personal workspace and owner-derived tenant
per principal; this is truthful principal isolation, not a claim that arbitrary
enterprise tenant memberships or organizations already exist.

## Durable records

`project_memory_entries` stores the current typed projection. The earlier
`project_memories` key/value table remains migration-compatible historical
schema and is not context authority. Supported kinds
are goals, architecture decisions, user decisions, important conversation
facts, repositories, prior objectives, completed outcomes, artifacts, failed
approaches, questions, preferences, and Fleet outcomes. Explicit writes require
`command`; reads require `read`. Command-authorized Native Chat also offers the
closed `magi.remember` function: the provider may select it only when the user
explicitly asks to remember/save/retain a fact. Ordinary conversation is never
silently copied into memory. Model-authored memory cannot claim authoritative
completed outcomes, artifacts, or Fleet state; those remain structured ledgers.

- A stable caller `memory_key` produces a deterministic owner-and-scope-qualified
  entry id.
- Updating an entry appends `project_memory_revisions`; deleting it appends a
  tombstone and removes it from search. Deleted identities cannot be reused.
- `project_memory_terms` is a bounded lexical inverted index. Retrieval is
  deterministic and provider independent: term overlap, title overlap,
  importance, exact-repository preference, and recentness determine rank.
- `project_memory_audit` is a per-scope SHA-256 mutation chain containing actor,
  operation, revision and content digest, not a duplicate of content.
- `project_memory_retrievals` records purpose, actor, query digest and selected
  entry ids. Raw queries are not copied into audit.

The structured objective/execution/decision ledgers remain authoritative for
recent intent, completed outcomes, artifacts, current Fleet outcomes and
Attention. Context assembly reads those persisted rows directly; it does not
copy or reinterpret terminal output.

## API

All scope query parameters are bounded identifiers. Tenant and owner cannot be
specified by a client.

Every route takes `project_id` plus optional `repository_id` query parameters;
both are resolved against the authenticated principal's active durable project.
Workspace, organization, tenant, and owner cannot be client-selected.

| Route | Scope | Purpose |
| --- | --- | --- |
| `PUT /api/v1/magi/memory/entries/{memory_key}` | `command` | Create or revise one typed memory. |
| `DELETE /api/v1/magi/memory/entries/{entry_id}` | `command` | Append a tombstone in the exact scope. |
| `GET /api/v1/magi/memory/search?q=...` | `read` | Bounded ranked retrieval. |
| `GET /api/v1/magi/memory/context?q=...` | `read` | Preview the same bounded chat context contract. |
| `GET /api/v1/magi/memory/audit` | `read` | Inspect mutation-chain and retrieval metadata. |

The write contract rejects extra keys, credential-like material, controls,
invalid Unicode, unsupported kinds and oversize values.

## Selective chat context

For each Native Chat provider turn, `MagiContextAssembler` emits one canonical
`magi.context-plane.v1` JSON fact document. It includes only:

- up to 8 query-relevant project memory entries;
- up to 3 recent, project-matching objective intents;
- up to 5 relevant or currently active persisted Fleet outcomes;
- up to 5 pending persisted Attention decisions;
- the current modality; and
- at most 10 authenticated attachment metadata records (never file bytes).

That document is capped at 16,000 characters. Individual memory excerpts are
capped at 1,200 characters. It is placed in system context with an explicit
instruction that every string is inert data. Normal conversation continuity is
still the existing whole-message bound: at most 40 completed Native Chat rows
and 100,000 characters. The context plane never adds full transcripts, legacy
conversation rows, worker output, hidden reasoning, terminal bytes, or provider
thread state.

Because memory and conversation are Gateway-owned, replacing the Magi model or
constructing a fresh `MagiChatService` retrieves the same scoped records. A
provider does not own continuity.

## Objective handoff

Before `firstmate.submit_objective` publishes a deterministic task, Gateway
selects at most 10 memories relevant to that exact objective and project. The
canonical context JSON and its digest are persisted on
`magi_objective_submissions` in the same objective identity record. The task
body labels harness history non-authoritative and contains this frozen bounded
context. Retries and startup recovery reuse the persisted bytes; a later memory
edit cannot silently change an already accepted task or break its idempotent
receipt.

Firstmate still owns intake, worker creation, authorization, confirmation and
execution. Context does not confer authority and opaque model-supplied
`context_refs` are never dereferenced without an owner-qualified Gateway read.

## Bounds

| Item | Bound |
| --- | ---: |
| Active entries per exact scope | 2,000 |
| Revisions per entry | 100 |
| Entry content / title | 8,000 / 240 characters |
| Indexed terms per entry or query | 128 |
| Search query | 1,000 characters |
| Search results | 20 |
| Context memory entries (generic / chat / worker) | 12 / 8 / 10 |
| Context document | 16,000 characters |
| Audit page | 200 rows |

Capacity and revision exhaustion fail closed; no invisible eviction discards
operator memory. Search evaluates at most 500 matching candidates. Account
erasure deletes terms/revisions before owner-qualified audit, retrieval, and
entry rows, in the same lifecycle transaction as projects and conversations.

## Security and test contract

`gateway/tests/test_project_memory.py` proves:

- restart durability, deterministic search, revisions, tombstones and audit;
- rejection at content, result and scope capacity bounds;
- credential-like content rejection;
- no owner, tenant, organization, workspace, project or repository leakage;
- project-wide inheritance without sibling-repository inheritance;
- selective multimodal/Fleet/Attention-shaped context rather than transcript
  dumping;
- explicit user-requested memory through Native Chat and retrieval on a later turn;
- continuity across fresh service/provider instances;
- no continuity leak to another principal; and
- frozen, relevant-only worker context with persisted digest.

These are hermetic SQLite/provider tests. They make no claim that an external
provider, enterprise identity system, or physical App Store build was exercised.
