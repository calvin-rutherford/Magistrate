# Ambient Magi — device-independent work and Attention

Authority: [production matrix](PRODUCTION_STATUS.md). Repository contracts:
**COMPLETE**. Live isolated autonomy and signed physical-device acceptance:
**BLOCKED_EXTERNAL**. Ambient does not mean continuous listening or thought reading.

## Product hierarchy

- **Chat:** one provider-native canonical human thread shared by typed and
  transcribed input, including verified outcome reports. No terminal target.
- **Projects:** durable owner-qualified personal-workspace projects, authorized
  GitHub App repositories and scoped context; no invented team membership.
- **Fleet:** persisted product objectives, status, decisions, artifacts and
  cancellation requests, not panes/PIDs/process controls.
- **Activity:** durable typed progress/evidence with replay, never inferred stdout.
- **Attention:** unresolved decisions and actionable outcomes; viewing, dismissing
  or acknowledging is separate from authorizing the exact answer/action.

The drawer and Settings remain independent scrolling overlays over full-bleed
Chat. Product-safe copy and empty/error states must survive new integrations;
a provider settings page, notification or diagnostic is not another chat.

## Closed background loop

The host persists an authorized objective, frozen context and credit reservation,
then admits it through deterministic restricted-local Firstmate intake or the
exclusive hosted queue. Hosted write-side leases/reconciliation survive clients
closing and Gateway restarts. The mTLS isolation backend and digest-pinned worker
must enforce real isolation; mocked receipts do not prove that enforcement.

Authenticated structured events update Fleet/Activity and measured settlement.
Accepted is not running, cancellation requested is not cancelled, and a process
exit is not verified completion. A run-specific decision enters Attention, then
an owner confirms exact ID/revision plus canonical answer bytes. Verified
completion may add one assistant outcome in the original thread. Normal reads,
health and the release runner never drive execution/Herdr lifecycle.

## Voice and device boundaries

Voice is foreground `expo-audio` capture, server STT, Native Magi submission and
local TTS. Safe behavior on microphone denial, interruption, lock/background and
provider failure is mandatory. No continuous background recording is implemented.
SDK 57 App Intents/Action Button hooks are foreground-only and use allowlisted
pending intents into the same record; execution-routing and speech-mode settings
are separate. Physical Bluetooth/audio route, lock-screen, interruption and
TestFlight behavior is still unobserved by repository tests.

Device-neutral `magistrate.perception-event.v1` is a consent/normalization seam,
not a shipped headset, wearable, neural or subvocal sensor. Every result remains
non-executing, including after explicit confirmation. Device presentation cannot
select a tenant, credential, project authority or execution scope.

## Push tickets, receipts and Attention

`notifications.py` and `push_receipts.py` now distinguish:

1. an owner registers a genuine Expo token;
2. the send endpoint returns an opaque accepted **ticket**, persisted with the
   exact owner/item/fingerprint and token **hash**;
3. the existing notification background loop polls bounded Expo receipt batches
   after 15 minutes, with cross-instance due-time leases/backoff and a 24-hour
   expiry; only an `ok` receipt marks provider delivery;
4. a physical device arrives/opens (separate external evidence); and
5. the owner views/acknowledges the Attention item (separate unread state).

A receipt means Expo handed the notification to APNs/FCM, **not** that an iPhone
received it. The ledger stores no copy or raw token. Missing/malformed/provider
failures remain pending then expire; terminal errors retain in-app fallback.
`DeviceNotRegistered` revokes only the matching current token. Late receipts
cannot acknowledge a new fingerprint or retire a replacement registration.
Claims dedupe concurrent sends; crash-expired send claims have bounded retries.
Expo has no exactly-once send API: a crash after remote acceptance but before
ticket persistence may repeat a push, never repeat an objective or answer.

Receipt polling uses the existing notification loop only, not a new execution
reconciler or product-read timer. Public status returns owner-only receipt counts.
Registration remains one token per principal; second-device conversation
continuity is supported, but fan-out to every device is not claimed. Web fallback
requires an eligible open tab; no service-worker push or fabricated native local
notification is presented as background delivery. Denied/offline/error states
retain Attention and unread state.

## Acceptance

Mandatory synthetic suites: `spencer-hermetic`, `moat-hermetic`,
`execution-recovery-isolation`, `push-deep-links`, `voice`, and full `frontend`.
Receipt tests include restart/deduplication, delayed polling, outage/expiry,
replacement-token and cross-owner/fingerprint refusal, lease recovery and account
erasure. Restore/PostgreSQL tests preserve/isolate the new migration-9 ledger.

Real Spencer: fresh provider account → welcome/name → GitHub identity and App
installation/project → approved subscription/budget → real objective/evidence →
confirmed Attention answer → files/voice → offline/reopen/second device → real
push ticket **and receipt** plus cold/warm tap → logout/revoke/deletion. No SSH,
bootstrap credential, worker configuration or seeded user profile is allowed.

The seven independent moat gates cover provider replacement, harness replacement,
context continuity, cost routing, client-independent work, the full Attention
loop and device independence. Record exact signed build/Gateway SHA, physical
iPhone/iOS/network, approved accounts and redacted hashes. Execute failure/recovery
only in authorized isolated infrastructure, not by driving shared Herdr. See
[PRODUCTION_ACTIVATION.md](PRODUCTION_ACTIVATION.md).
