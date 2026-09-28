# Context plane — durable continuity without authority leakage

Authority: [production matrix](PRODUCTION_STATUS.md). Repository context plane:
**COMPLETE**. Real provider/harness/device continuity: **BLOCKED_EXTERNAL**.

## Canonical authority

`project_memory.py`, migration 6 and its owner-qualified store own context, not
provider threads, harness sessions, embeddings or terminal transcripts. Each
entry, mutation and retrieval has authenticated owner, derived tenant/personal
organization, persisted workspace/project and optional repository qualification.
This is personal-workspace tenancy, not arbitrary enterprise memberships.
Repository retrieval may inherit project-wide entries in that exact scope;
it never inherits a sibling repository or any other owner's data.

Current `project_memory_entries`, revisions, terms, audit and retrieval tables
are authority. The older encrypted `project_memories` key/value table remains
migration-compatible history. Supported typed memories include goals, decisions,
conversation facts, repositories, objectives, outcomes/artifacts, failed
approaches, questions/preferences and Fleet facts. Model `magi.remember` is
available only for an explicit user request and cannot claim authoritative
outcomes, artifacts or Fleet state; those originate in structured ledgers.

Stable owner/scope/memory keys produce deterministic identities. Revisions are
append-only, deletion creates a tombstone and deleted identities cannot be
reused. A bounded lexical index ranks term/title overlap, importance, repository
specificity and recency. Mutation audits form a digest chain; retrieval records
query digests and selected entry IDs, not raw queries in operational logs.

## Selective assembly and frozen handoff

`MagiContextAssembler` emits bounded `magi.context-plane.v1` inert JSON facts:
up to eight relevant memory entries, three recent project intents, five Fleet
outcomes, five pending decisions, modality and ten authenticated attachment
metadata records. The document is capped at 16,000 characters with 1,200-character
memory excerpts. Native conversation continuity separately admits up to 40
completed whole rows and 100,000 characters. No transcript dumping or hidden
reasoning enters the memory plane.

Before objective publication, at most ten relevant memories and their exact
canonical context/digest freeze on `magi_objective_submissions`. Retry, restart
and hosted/local handoff reuse the saved bytes; a later memory edit cannot change
an accepted task. `context_refs` alone grant no dereference authority. Context is
not a system instruction, tool definition, credential, entitlement or confirmation.
Verified completion prose remains a separate narrow path generated from accepted
objective/evidence JSON, not prior chat or worker transcripts.

## Files, privacy and lifecycle

Private uploaded bytes are owner/digest validated before provider encoding.
Stored status does not mean extraction, indexing or understanding. OpenAI's
implemented file parts are distinct from other providers' capability/refusal
behavior; metadata-only consumption must not be described as file understanding.
A device/perception adapter normalizes consented drafts, never execution intent
by itself. See [CLIENT_PROTOCOL.md](CLIENT_PROTOCOL.md).

Memory writes/search are bounded and fail closed on capacity, controls,
credential-like content or unsupported kinds. They never silently evict history.
The component [context contract](magi-context-plane-v1.md) owns exact limits and
API details. Account erasure removes entries, revisions, terms, audits and
retrievals in the same owner-scoped lifecycle transaction. Backup expiration,
provider-side erasure and legal retention remain explicit operator obligations;
logical deletion cannot prove all external copies disappeared.

## Acceptance

`magi-context-routing`, `tenant-authorization`, `moat-hermetic`, `migrations`,
`backup-restore` and `postgres-persistence` include the actual merged memory,
routing and isolated-handoff tests. The composed restore fixture compares memory
revisions/index/audit/retrieval rows and frozen context with the full DB and
restored object bytes. PostgreSQL exercises two concurrent tenants and selective
erasure. Synthetic replacement tests prove Gateway-owned continuity, not a
real vendor switch or running harness migration.

External moat acceptance must recall an authorized project fact across real
provider, process, harness and device changes, preserve provenance/digests,
reject another tenant and demonstrate deletion. A convincing model answer
without source/selection evidence cannot pass. Follow
[PRODUCTION_ACTIVATION.md](PRODUCTION_ACTIVATION.md).
