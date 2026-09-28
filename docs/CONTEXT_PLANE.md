# Context plane — continuity without authority leakage

Authority: [production matrix](PRODUCTION_STATUS.md). **Production context plane:
FAILED.** Native conversation continuity is implemented; portable project memory
is a separate workstream, not a synonym for transcript history.

## Existing context

`MagiChatStore.context_before` selects bounded owner-scoped history (maximum 40
eligible messages) before the current turn. `MagiChatService` adds the observed
profile, bounded optional deployment context and authenticated pending-decision
projection. Native Chat is persisted independently of model/provider sessions;
new service/model instances can reuse canonical IDs and history. The release
replacement fixture proves this synthetic boundary and cross-owner refusal.

Objectives accept only bounded opaque `context_refs`, not arbitrary raw context
or model-selected credentials. Decision answers load exact bytes from the
owner's canonical user row with bound confirmation. Verified execution outcome
messages are deliberately generated from accepted objective/evidence JSON with
**no prior chat history or worker transcript**. Preserve that narrow outcome path.

No baseline vector database, project-memory provenance/version/deletion plane,
portable cross-harness checkpoint or automatic complete long-term recall is
proved. A provider's prompt window or an opaque reference is not a durable
retrieval implementation. Files with `stored` status are not automatically
extracted, embedded, indexed or submitted to a model.

## Required A6 contract

- Bind every context object/reference to authenticated principal/tenant/project
  membership. Resolve references only on the server; reject unknown, revoked,
  expired or foreign objects without revealing another tenant's existence.
- Store provenance/source identity, immutable version/hash, freshness and
  retention/deletion state. Separate user facts, file-derived material, verified
  execution evidence, provider output and untrusted repository text.
- Bound retrieval by count/bytes/tokens and explicit model-policy purpose. Record
  selected IDs/versions and policy for a replayable audit without copying private
  content into logs. Mark missing/truncated/stale context truthfully.
- Treat retrieved content as data, never system policy, shell instructions, tool
  definitions, authority, a decision confirmation or a billing entitlement.
- Keep provider/model/harness neutral canonical context, with approved adapters
  for transport. Replacement cannot silently widen egress or discard provenance.
  Provider-specific session IDs are not the portable source of truth.
- Honor revocation and deletion in retrieval, caches, derived indexes, uploads
  and backups under the approved retention policy; do not promise immediate
  destruction of independently retained financial/audit records.
- Allow background execution to use only a frozen authorized context binding or
  an explicitly governed refresh. Reads of context do not wake or supervise work.

A1 supplies membership, A10 durable files, A7 bounded model selection/usage, A5
execution handoff and A4 charging rules. A12 needs exact schema/route/config and
migration dependencies, not proposed environment variables. Any external storage/
index/provider console activation must follow the merged adapter's actual names.

## Acceptance

Repository: extend `magi-context-routing`, `tenant-authorization`, `migrations`,
`backup-restore` and `moat-hermetic` with seeded project facts, provenance/version
checks, wrong-tenant/project guesses, revoked references, bounded retrieval,
deletion and restart/replacement tests. Verify exact conversation IDs/revisions
and no duplicate objective across provider/harness boundary failures.

Live: moat context-continuity and provider/harness replacement must recall a
known authorized project fact across real process/provider/device changes while
denying another tenant. Include the source artifact and redacted selection/
version evidence; “the model sounded like it remembered” is insufficient. The
synthetic provider seam cannot pass this gate. See
[MAGISTRATE_ROUTING.md](MAGISTRATE_ROUTING.md) and [CLIENT_PROTOCOL.md](CLIENT_PROTOCOL.md).
