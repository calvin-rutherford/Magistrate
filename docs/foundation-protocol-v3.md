# Additive foundation protocol for PERSONAL_RUNTIME_V2

Schema owner: `gateway/app/contracts.py`. Generated normalized client output:
`frontend/src/protocol/ObjectiveStateProtocol.ts`. This foundation exports value
contracts only; it does **not** implement Chat/Attention projection, activate a
Firstmate producer, change persisted rows, or open a streaming voice transport.
Those downstream owners must adopt these contracts at authenticated write seams.

```sh
(cd gateway && uv run python -m scripts.export_client_protocol --write-types)
(cd gateway && uv run python -m scripts.export_client_protocol --check-types)
(cd gateway && uv run python -m scripts.export_client_protocol) > .release/client-protocol.json
```

The schema bundle includes eight foundation entries. The generated TypeScript
contains `readonly` normalized output types (all defaults present) and a schema
hash. It is not a client-side authorization validator. Pydantic rejects unknown
fields, coercion, unsafe JS integers and invalid cross-field claims. Model
validators remain authoritative beyond what JSON Schema can express. CI and the
Gateway suite reject generated drift. No deployment configuration is read by the
CLI; normal export retains all existing schemas and API paths.

## Objective identity and lifecycle (`magistrate.objective-state.v3`)

`ObjectiveIdentityV3` freezes owner/project, objective/job/run, canonical Native
Magi conversation/origin user message, idempotency key and exact request SHA-256.
The write-side store must persist these together before dispatch, retain them
through uncertainty and reject reuse with different bytes. A new transport,
provider attempt or display label is not a new objective. Explicit execution
attempt changes require durable causality, not regeneration after a timeout.

`ReplayIdentityV3` binds authenticated producer instance + source event ID to an
exact event digest. An owner-scoped durable positive replay cursor is separate
from wall-clock time. Duplicate identical events are idempotent; changed bytes
under the same key conflict. These frozen value objects do not themselves
implement database uniqueness, compare-and-swap or producer authentication.

Lifecycle values are exactly `accepted`, `running`, `verifying`,
`waiting_for_input`, `failed`, `cancelled`, `completed`. `state: null` means no
producer lifecycle observed; UI must not call it accepted/running. Any non-null
state needs persisted event provenance and acknowledged dispatch. `dispatch`
separately distinguishes `pending`, `acknowledged`, `uncertain`. The write-side
projector must preserve monotonic revision and the last valid lifecycle across
reconnect/partial dispatch; reads never reconcile by launching workers.

`cancellation` distinguishes `none`, `requested`, `acknowledged`, `observed`.
Only `observed` pairs with terminal `cancelled`; a request or receipt is not
termination and must not release reservations/capacity. `completed` requires an
immutable completion-evidence digest whose referenced evidence the write seam
has independently validated. Ledger settlement still requires measured usage;
completion, a deadline or process exit cannot invent a zero-cost measurement.

`magistrate.objective-state.v1` is exported unchanged as
`objective_state_legacy`. It preserves `unknown/submitting/queued/recovering`,
`succeeded`, progress high-water marks and nullable historical context. Do not
fabricate missing v3 identity/provenance for old rows or rename legacy success on
read. A separately validated write-side migration/projection may produce v3;
unsupported/unbound rows remain explicitly legacy/read-only.

## Requested envelope versus acknowledged enforcement

`firstmate.runtime-envelope.v2` freezes the identity, exact GitHub installation /
repository / base commit, one or two implementation workers, positive bounded
execution duration (at most one day), integer micro-USD objective/month ceilings,
absolute acknowledgment deadline in milliseconds, and cancellation grace (at most
300 seconds). Objective ceiling cannot exceed monthly ceiling. These are requests,
not proof that a producer can enforce them; `PERSONAL_RUNTIME_V2` is a versioned
selection feature, never a permission or enforcement boolean.

`firstmate.runtime-acknowledgment.v2` binds an authenticated persisted event to the
same identity, exact envelope digest and pinned producer commit. Its status is
`acknowledged`, `rejected` or `unsupported`; the latter two cannot carry any
enforcement claim. Repository, concurrency, duration, cancellation and spend
claims remain separate. At admission the controller must compare exact frozen
bytes, pin and identities, check deadline/current authorization, and require each
necessary enforcement capability before publication. A signed/authenticated
receipt is a claim to validate against the selected producer's implementation,
not a sandbox certification. This foundation adds no capability-default bypass.

`RuntimeSpendV2` separates reservation, requested limit and measured cost.
Unavailable measurement is `null`, never zero. Partial/final measurement requires
an event reference; runtime-hard-limit requires an acknowledgment reference.
References must resolve to the authenticated matching ledger, not arbitrary
caller strings. `admission-only` is explicitly not runtime spend enforcement.
Measured usage may exceed a requested limit; do not cap reported facts or forge
settlement to make a budget look enforced.

## Revision-bound decisions

`firstmate.decision-binding.v2` binds immutable objective context to opaque decision
ID, exact positive revision, request ID, project scope and expiration. Scope must
equal the frozen authorized project. Historical decisions missing this authority
remain compatible/readable but cannot acquire response authority from config.

`firstmate.decision-acknowledgment.v2` carries that exact binding, canonical answer
user-message ID/digest and persisted runtime event/time. `received` is not worker
continuation; only an authenticated `applied` event observes application.
`rejected` does not consume a new request revision. The existing Gateway answer
seam still loads exact owner-bound bytes out of band, checks expiry/revision and
requires bound confirmation. No answer text, bearer or confirmation token is
exported. The store owns replay/conflict rules and atomic state changes.

## Voice continuity (`magistrate.voice-session.v1`)

One owner and Native Magi conversation remain bound to an opaque voice session.
A positive session generation fences replacement/revocation; a connection ID
identifies transport without replacing the conversation. Turn ID and canonical
`client_message_id` must occur together and dedupe one canonical turn across
retries. Persisted event sequence, not interim transcript text, owns ordering.

Authorization is `unavailable/authorized/expired/revoked`, distinct from
`unavailable/connecting/connected/reconnecting/closed` transport. Active transport
requires authorization plus an expiring authenticated event reference. Current
expiry/revocation checks belong to the server/session controller, not this static
value object or local microphone permission. Reconnect cannot mint a new identity
or reset event high-water marks; expiry/logout must fence the old generation.
Provider credentials and Gateway bearers never enter this exported contract.

## Integration gates

`gateway/tests/test_foundation_protocol.py` covers all seven lifecycle values,
immutable identities, uncertainty/cancellation distinctions, exact decision
scope, unsupported-capability refusal, honest spend, voice authorization and
generated drift. Existing Gateway/client suites preserve legacy behavior. The
foundation evidence does not claim downstream adoption, real bounded execution,
voice streaming or physical/native acceptance; integration must prove those
separately before another owner soak.
