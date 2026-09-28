# Ambient Magi — device-independent work and Attention

Authority: [production matrix](PRODUCTION_STATUS.md). **Ambient production
acceptance: FAILED.** Durable execution/Attention seams exist; live isolated
background autonomy, receipt-aware push and physical-device behavior still need
their own acceptance.

## Product hierarchy

- **Chat:** one provider-native human thread shared by typed and transcribed
  input, including verified outcome reports. No terminal transcript or worker
  target selector.
- **Projects:** durable authorized project/repository/context boundary. The
  baseline does not yet have the complete SaaS Projects plane; A1/A3/A6/A8 own it.
- **Fleet:** product-safe persisted objectives, status, decisions, artifacts and
  cancellation requests, not panes/PIDs/raw task/run controls.
- **Activity:** chronological durable typed progress and evidence with replay,
  not a feed inferred from stdout or generated completion prose.
- **Attention:** unresolved questions/decisions and actionable outcomes. Viewing,
  dismissing or acknowledging is separate from authorizing an action.

The drawer/settings are overlays on the full-bleed Chat canvas with independent
scrollers. Preserve the canonical product hierarchy as UI workstreams integrate;
do not make a notification, diagnostic or provider account page another chat.

## Closed autonomy loop

1. Authenticated user asks Magi for work; the closed tool contract persists
   objective identity and performs deterministic queue acceptance plus one wake.
2. Firstmate alone decides scheduling/capacity and owns isolated workers. A
   disconnected app cannot prevent already accepted durable work progressing.
3. Structured producer events update persisted Fleet/Activity; failures remain
   failures. “Queued” is not “running”; cancellation requested is not observed.
4. A structured decision projection enters Attention and authorized model
   context. Notifications announce its presence without copying sensitive answer
   bytes. Delivery acknowledgement is not decision resolution.
5. An authorized owner confirms the answer tied to exact opaque decision ID/
   revision and canonical native user row. Stale/foreign/replayed answers fail.
6. Verified completion evidence may generate a new canonical assistant outcome
   in the original thread, independent of the original HTTP request or device.

Startup recovery replays interrupted write-side intake/cancellation/generation
claims once under their idempotency contracts. Normal startup observations and
reads never snapshot/control Herdr, poll for execution reconciliation or take
over Firstmate scheduling. Do not add client background timers as worker owners.

## Voice, push and device boundaries

Voice is foreground microphone capture, server STT, Native Magi submission and
local TTS. It is not continuous background recording or a separate conversational
worker. Permission denial, interrupted capture, app background/lock and unavailable
provider must stop safely and show truthful state. Speech-mode selection is not
execution routing. App shell decoration uses cached personalization, not an
extra authorized network read that could invalidate the session.

Native push uses a real Expo token and server remote delivery. Provider tickets
mean acceptance, not physical arrival; require receipt evidence and wrong-token
retirement. Web uses the Notification API only in an eligible open tab, not a
service-worker background subscription. Denied/offline/unconfigured delivery
retains Attention and unread state. No local notification fabricated from a
foreground poll may be described as background push.

Deep links are typed allowlisted intents, resolved after authentication into the
correct principal's item. Test cold/warm start, expired session, another signed-in
account, malformed route, stale target and duplicate delivery. Device replacement
must preserve canonical IDs/context without copying runner secrets. One baseline
push token per principal is not proof of multi-device notification delivery.

## Mandatory acceptance

Synthetic: `spencer-hermetic`, `moat-hermetic`, `execution-recovery-isolation`,
`push-deep-links`, `voice` and the **full** `frontend` suite. Those gates isolate
external edges; they do not prove real Apple/Google/GitHub/Stripe/model/APNs access.

Real Spencer: new account → required onboarding → authorized project/repository
→ approved billing/budget → objective → verified evidence → Attention answer →
upload/voice → kill/reopen/network handoff/second device → remote push/deep link →
logout/revoke/deletion. Exact checkpoints are enforced by the release entrypoint.
The user must not need SSH, a bootstrap credential or worker configuration.

Moat: provider replacement, harness replacement, context continuity, cost routing,
client-independent background autonomy, complete Attention loop, and device
independence. A real disconnected-client run must be backed by structured events
and passed evidence, not a manually authored progress message. Execute failure/
recovery exercises only with explicit runtime authorization, never by driving
Herdr from a health/read test. Record signed build, Gateway SHA, device/iOS,
network, timestamps, account ownership and redacted artifact hashes. Follow
[PRODUCTION_ACTIVATION.md](PRODUCTION_ACTIVATION.md); until every required checkpoint
is COMPLETE, this is not an accepted autonomous production release.
