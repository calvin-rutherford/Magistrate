# Pi semantic ownership channel v1

Status: production contract, opt-in (`MAGISTRATE_PI_OWNERSHIP_ENABLED=false` by
default). Unknown boolean values fail startup/activation closed rather than
silently selecting the legacy path.

This channel gives Magistrate a source-native correlation path for the canonical
captain. It does not infer identity from a terminal, viewport, pane, process
name, prompt echo, or timing window. Gateway creates the canonical turn and its
reserved primary assistant identity first; a local Pi extension then binds that
reservation to durable Pi session entries through Pi's native extension API.

## Guarantees and non-goals

For one client `message_id`, the channel guarantees:

1. one canonical turn, objective, run, and reserved primary assistant message;
2. one random dispatch incarnation and one opaque, single-use capability;
3. one exact Pi session/user-entry binding, including the exact prompt hash;
4. one exact normally stopped, finalized Pi assistant entry;
5. one canonical update of the pre-reserved primary assistant row; and
6. idempotent replay at every accepted transition.

The only response content crossing into Gateway is the complete visible text
from the bound final assistant entry, in original block order and with whitespace
and Unicode preserved. The adapter does **not** send thinking content, tool call
arguments/results, model/provider usage, token accounting, internal/custom
payloads, environment data, or presentation/worker transport metadata. Entries
between ownership anchors are represented only as structurally redacted chain
nodes. Visible text blocks are renumbered independently, so omitted private
blocks do not disclose their positions.

This contract does not select or migrate a model, harness, session, or worker.
When enabled, it targets the already-running captain Pi session. An explicit
per-request execution profile is rejected rather than pretended. It does not
replace `magi.event.v1`, which remains the richer structured-response producer
for turns that were not Pi-owned at prepare time. It does not provide semantic
worker-pane transcripts. The legacy `/api/v1/voice/moves` service dispatches
through Herdr before canonical persistence, so it is rejected while this channel
is enabled rather than creating an ownership-bypassing captain turn; typed
captain prompts remain available.

## Components and trust boundaries

- `gateway/app/conversation_store.py` creates canonical identity.
- `gateway/app/pi_ownership.py` owns the durable dispatch state machine and the
  only canonical write from Pi evidence.
- `gateway/app/pi_adapter_ipc.py` is a bounded authenticated Unix-socket client.
- `pi-extension/index.ts` is a Magistrate package loaded by Pi. It is the only
  component that invokes `pi.sendUserMessage()`, `pi.appendEntry()`, and Pi's
  read-only native session manager.
- `pi_semantic_dispatches` is Gateway's encrypted restart ledger.
- The adapter journal is an atomically replaced, size-bounded AES-256-GCM
  document. It stores hashes and correlation state while open and encrypted
  final visible evidence after finalization, so an unacknowledged response
  remains replayable even if Pi has since opened another session. An exact
  Gateway receipt is the only boundary that permits fsynced evidence deletion.

The two processes should run as the same dedicated OS user. The runtime
directory is `0700`; the socket, shared key, and adapter journal are `0600`.
Gateway checks socket type, owner, mode, and Linux `SO_PEERCRED`. The adapter
checks every local file against owner/type/mode/no-symlink requirements. Both
sides authenticate canonical JSON frames with HMAC-SHA-256 and compare MACs in
constant time. The dedicated Pi process/extension is part of the trusted
computing base: a compromise of that same UID/key can forge evidence. The
channel protects against unrelated local users, stale/cross-turn data, and
accidental transport confusion; it is not a sandbox for a compromised adapter.
Filesystem isolation is defense in depth, not authentication.

The opaque capability has at least 256 bits of randomness. Gateway stores its
plaintext and the exact submitted prompt only under application encryption;
canonical rows, source markers, and adapter journal records contain hashes
instead. The capability is sent only inside the authenticated local request. It
is consumed by the first accepted binding and can thereafter replay only the
same semantic evidence. Once the exact adapter receipt is durable, Gateway
clears the live encrypted prompt/capability columns while retaining only hashes
and accepted audit identity.

## Atomic prepare and precedence

`record_prompt(..., pi_semantic=True)` executes under one SQLite
`BEGIN IMMEDIATE` transaction. It creates/reuses:

- `conversations` and `conversation_turns`;
- the canonical user message;
- the primary `conversation_assistant_reservations` row; and
- `pi_semantic_dispatches` with tenant/principal, canonical IDs, prompt hashes,
  encrypted prompt/capability, expiry, and `state='prepared'`.

An edited retry conflicts once ownership exists. A retry with identical text and
identity receives the same incarnation/capability. Ownership can be selected
only when the turn is first created; an existing unowned or structured turn
cannot be seized on retry.

Ownership precedence is fixed:

1. the source chosen atomically at turn prepare (`pi_semantic_dispatches`);
2. an accepted `magi.event.v1` semantic stream for an unowned turn;
3. terminal fallback only for a turn having neither semantic owner.

Therefore fallback is disabled at **prepare**, before adapter availability,
binding, generation, or finalization. Adapter outage never causes a prompt
resubmission through a legacy provider. A previously created unowned turn cannot
be retrofitted because a feature flag changed on an HTTP retry; that could run
its model twice even if no legacy response had arrived yet. Existing HTTP
list/replay and WebSocket
canonical delivery are unchanged; clients see `content_source: "pi-semantic"`
on the stable assistant row.

## Native Pi entry protocol

The extension appends context-free custom entries; these entries are not sent to
the model:

1. `magistrate.pi.dispatch.prepare.v1`
2. the user entry created by `pi.sendUserMessage(prompt, {
   expandPromptTemplates: false })`
3. `magistrate.pi.dispatch.bind.v1`
4. zero or more source entries, represented to Gateway only as redacted chain
   nodes
5. the final assistant message entry with `stopReason === "stop"`
6. `magistrate.pi.dispatch.finalize.v1`

The native call is armed for one dispatch only. Its `input` event must report
`source: "extension"`, then the final `before_agent_start` prompt must retain the
exact submitted hash before `turn_start` is allowed to mint a bind marker. Thus
a manually/RPC-submitted matching string cannot claim ownership. `turn_start`
is used for user binding because the native user entry has already been
persisted at that point. The durable bind marker is the restart proof; a bare
matching user entry is never attributed after process loss. The extension never
finalizes on assistant `message_end`: Pi emits it before
`SessionManager.appendMessage()`.
It waits for `turn_end`, then locates exactly one persisted assistant entry that
matches that event and appends the finalize marker. A normally stopped assistant
found after a lost `turn_end` is recoverable from the durable native branch.
`toolUse` assistant entries remain in-progress; abnormal stop reasons fail
closed and publish no assistant text.

The source envelope carries a bounded, gap-free parent chain from prepare marker
to bind/finalize marker. Each descriptor contains only order, entry ID, parent
ID, and one of:

- the designated user/final assistant role;
- the designated ownership-marker type; or
- `redacted`.

The canonical SHA-256 of that sequence, a cursor, exact anchor IDs/orders,
session ID, prompt/user hash, finality, and the SHA-256 of visible text block
descriptors are checked again by Gateway. Unknown fields, malformed Unicode,
unsafe visible control characters, non-integral ordering, gaps, forks,
duplicate IDs, another user boundary,
non-final assistant entries, oversized frames, and mismatched hashes are
rejected before mutation.

## IPC framing

The transport is one newline-delimited JSON request and response per Unix socket
connection, each at most 1,250,000 bytes:

```json
{
  "body": { "schema_version": "magistrate.pi.ipc.v1", "message_type": "dispatch" },
  "mac": "lowercase-hmac-sha256"
}
```

Canonical JSON recursively sorts object keys, uses UTF-8, and has no insignificant
whitespace. A dispatch body includes a random request nonce, bounded issue/expiry
times, capability and capability hash, exact canonical/tenant identities,
prompt hash, and prompt. A status request omits the prompt. Nonces are
single-use within the adapter replay window. Responses echo the nonce and are
either a closed `magistrate.pi.ownership.v1` envelope or a bounded signed error.
After a final/failed envelope commits, Gateway sends a separate authenticated
`ack` bound to the accepted semantic-envelope hash. Until that receipt is
stored, Gateway keeps the completed dispatch in its bounded recovery query and
the adapter cannot evict its encrypted evidence. If the adapter deletes evidence
but its receipt response is lost, authenticated `unknown-dispatch` converges the
already-committed Gateway row. Unauthenticated/malformed input
receives no diagnostic oracle.

The Gateway validates the closed ownership model independently after HMAC
verification. Transport authentication alone never authorizes a canonical
write: capability, authenticated principal/tenant, conversation/turn/message,
objective/run, source session/entries, transition state, ordering, and hashes
must all match durable state.

## State machine

| Durable Gateway state | Accepted evidence | Result |
| --- | --- | --- |
| absent | atomic prompt prepare | `prepared` |
| `prepared` | exact binding revision 1 | consume capability, persist Pi user/session anchors, `bound` |
| `prepared` | complete final revision 2 | atomically synthesize/verify its binding prefix, then finalize |
| `bound` | byte-identical binding replay | duplicate success |
| `bound` | final revision 2 with the accepted prefix | canonical update + lifecycle completion + `finalized` in one transaction |
| `finalized` | identical final semantic hash/entries/content hash | duplicate success, no revision bump; retry signed receipt until acknowledged |
| any non-final | explicit bounded failure | frozen `failed`; no assistant text |
| canonical turn already cancelled/failed | exact final proof | accept only as a discard receipt, acknowledge/prune it, write no assistant text |
| `failed`/`finalized` | conflicting later evidence | reject |

The final canonical transaction either updates all of these or none:

- reserved primary row text/source/content source/revision;
- conversation change ledger;
- turn `status='answered'` and `lifecycle_state='completed'`; and
- exact Pi assistant/finalize IDs, sequence/content hashes, times, and dispatch
  state.

A structured canonical row, a cancelled/failed turn, another primary identity,
or a source entry already claimed by another dispatch causes a conflict and is
never overwritten.

## Crash and retry matrix

| Boundary | Recovery behavior |
| --- | --- |
| Before prompt transaction commit | no visible turn or capability exists |
| After Gateway prepare, before adapter receipt | same encrypted dispatch is retried; no fallback is eligible |
| After Pi prepare marker, before adapter journal rename | marker is found by exact identity and reused |
| Fsynced adapter `prepared`, before native send | safe to call native send once |
| Fsynced `dispatching`, native-send outcome unknown | never resubmit; recover only an existing exact bind marker, otherwise fail `indeterminate-before-bind` once expired and natively idle |
| Native user persisted, process lost before bind marker | fail closed; the bare matching entry cannot prove the in-memory native input authorization |
| Bind marker/journal written, before Gateway accepts | status returns the same binding envelope; Gateway replay is idempotent |
| During tools/generation | remains `bound`; no partial visible text is ingested |
| Final assistant persisted, before/lost `turn_end` | status finds the sole normally stopped assistant and appends finalize |
| Finalize marker written, before adapter journal rename | marker and assistant are revalidated from native entries |
| Final journal written, Pi restarts/switches session | encrypted final evidence replays without reading Pi storage |
| Gateway final transaction rolls back or response is lost | adapter returns the same semantic final envelope; canonical retry commits once |
| Canonical commit succeeds, receipt request is lost | completed dispatch remains recoverable; final replay is a no-op and the signed receipt retries |
| Adapter fsyncs receipt deletion, response is lost | signed `unknown-dispatch` retires the already-committed Gateway recovery row |

Open dispatches prevent Pi session switching, forking, or tree navigation
through native `session_before_switch`/`session_before_fork`/
`session_before_tree` cancellation. Never manually edit
Pi session entries or the adapter journal. If an indeterminate boundary cannot
be proven, failure is intentional: availability never wins over duplicate input
or cross-turn attribution.

## Installation and operation

The Pi package is local to this repository and pins the reviewed Pi `0.84.4`
native lifecycle contract as its only peer. Upgrade that pin only with lifecycle
and crash-boundary revalidation:

```sh
cd /absolute/path/to/Magistrate/pi-extension
npm ci
npm run typecheck
npm test
pi install /absolute/path/to/Magistrate/pi-extension
```

Configure the same absolute runtime/key/socket paths and
`MAGISTRATE_PI_OWNERSHIP_ENABLED=true` in both Gateway and the dedicated captain
Pi environment. In Chat, select **Current backend session** so requests carry no
per-request profile/harness/model fields; the ownership channel rejects those
fields because it does not claim to migrate the running Pi session. A saved
server default is not applied on this path. Start Gateway once to
create/validate the `0600` key, then start
Pi with the installed extension. Do not copy the key into a repository, shell
history, logs, backup shared with another service, or frontend environment.

Quiesce submissions and resolve/acknowledge all dispatches before rotating or
replacing the IPC key. The adapter durably removes the journal when the last
record is acknowledged; if a journal remains, rotation is not safe. The adapter
deliberately refuses a nonempty old journal that cannot be authenticated with
the configured key; never delete it merely to make startup pass.

Readiness checks:

```sh
stat -c '%a %u %F %n' \
  "$MAGISTRATE_PI_RUNTIME_DIR" \
  "$MAGISTRATE_PI_IPC_KEY_PATH" \
  "$MAGISTRATE_PI_ADAPTER_SOCKET"
test ! -e "$MAGISTRATE_PI_ADAPTER_JOURNAL" || \
  stat -c '%a %u %F %n' "$MAGISTRATE_PI_ADAPTER_JOURNAL"
ss -xl | grep --fixed-strings "$MAGISTRATE_PI_ADAPTER_SOCKET"
```

Expected modes are `700` for the directory and `600` for all files/socket, with
the configured service UID. The journal is absent when no evidence is pending.
Inspect open state without selecting encrypted
columns or source identities:

```sh
sqlite3 "$MAGISTRATE_DB_PATH" \
  "select state, count(*) from pi_semantic_dispatches group by state order by state;"
```

Do not print file contents. A prompt returning HTTP
503 after prepare means it is durably owned and recovery is retrying; it does
not authorize a legacy resend. Check process health and permissions, restore the
same adapter/session where an open bind exists, and let reconciliation converge.

To roll back, first stop new submissions, let open owned dispatches reach
`finalized` or explicit `failed` **and receive their durable adapter receipts**,
then set `MAGISTRATE_PI_OWNERSHIP_ENABLED=false` in both processes and restart
Gateway/Pi. Disabling the feature does not erase or
release existing ownership; retrying an owned client message while the flag is
off is rejected before legacy dispatch. That is deliberate: deleting dispatch
rows would make old terminal output newly eligible and violate source precedence. The
normal whole-conversation reset therefore returns HTTP 409 until every owned
row has a durable adapter receipt; after that receipt, it may remove the entire
conversation and its acknowledged ownership rows atomically. Restore from the
pre-change SQLite backup only as a whole-database emergency rollback, never by
deleting individual ownership rows.

## Verification

Focused checks:

```sh
cd gateway
MAGISTRATE_ENV=test PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_pi_ownership.py tests/test_pi_adapter_ipc.py tests/test_pi_route.py

cd ../pi-extension
npm ci
npm run typecheck
npm test

cd ../frontend
npm run typecheck
npm run test:magi-response
# Release gate:
npm test
```

The focused suites cover exact long Unicode content, source redaction,
tenant/capability substitution, finality, one-use/replay behavior, malformed and
gapped chains, parent/cursor/hash mismatch, cancellation, expiry, adapter
absence, immediate fallback suppression, legacy compatibility, local file/socket
security, native lifecycle ordering, encrypted restart replay, lost `turn_end`,
and the architectural ban on worker/display transport dependencies.
