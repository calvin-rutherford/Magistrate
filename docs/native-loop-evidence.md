# Native decision / push / voice slice: evidence and integration seams

This slice is **not physical iPhone acceptance** and does not implement streaming
speech-to-speech. No provider calls, credential configuration, deployment, or
phone-delivery tests were performed. Tests use local fixtures and browser/native
boundary doubles.

## Delivered boundaries

- Native notification taps are queued before authentication but issue no
  acknowledgement until the authenticated root consumes the route. The OS's
  last response is cleared only on consumption. Default taps navigate; custom
  action identifiers (including an `approve` label) cannot execute anything.
- Delivery identity deduplicates warm/cold callbacks. App Shortcut invocation
  UUIDs deduplicate a launch without permanently disabling subsequent invocations.
  Legacy URL duplicates have a short suppression window instead of a lifetime ban.
- Token rotation exchanges the changed APNs/FCM token for an **Expo** token,
  without recursively retrieving a device token. Foreground resumption rechecks
  permission, including revocation in Settings. Registration retries are throttled
  to one per minute / three per attempt cycle; explicit permission action or
  foreground re-entry allows recovery. Owner/session changes discard stale results.
- A push send has at most three durable network attempts per event fingerprint
  and token, not nested three-by-three retries. Leases expire. Receipt polling
  remains leased, bounded/backed off, and separate from viewing Attention.
  Network ambiguity can still duplicate a provider delivery: Expo is not an
  exactly-once transport. This never authorizes or duplicates execution.
- Notifications exclude ordinary milestones and non-actionable failures. Completion
  and review-ready results may be informational; all categories honor persisted
  preferences/quiet hours. Test notification text is explicitly neutral and labeled.
- Voice input remains record → transcribe → canonical Native Chat → device TTS,
  visibly labeled **turn-based**, not streaming. Microphone mute stops unsubmitted
  input; pausing, ending, backgrounding, and interrupting speech never call the
  engineering/chat cancellation endpoint. Both surfaces still share canonical text.
- A delayed microphone permission result cannot start recording after cancellation.
  Cancellation checks the recorder's current native state, not a stale React render.
  Existing tetrahedron geometry stays monochrome; RGB gradients/ripples are removed
  and Reduce Motion remains supported.

## Composition required from integration owner (not edited in this lane)

In `gateway/app/main.py`, behind the same normal authenticated/personal-deployment
policy as other notification endpoints:

```python
from app.notification_test_api import router as notification_test_router
app.include_router(notification_test_router)
```

`POST /api/v1/notifications/test` accepts only `{ "request_id": "stable-client-id" }`
(8–80 alphanumeric/underscore/hyphen characters). The owner comes from the live
`notifications`-scoped principal, not the body. Use the same request ID to retry
an uncertain result. The durable store deduplicates, limits new tests to one per
minute per principal, and honors disabled preferences / quiet hours. A missing
registration returns recovery guidance, not success. **This router is tested in
isolation and is intentionally not mounted by this lane.**

Expose `PushDeliveryStore(notification_db).evidence_summary(principal.user_id)`
through the existing status endpoint, instead of labeling its legacy `delivered`
DB bucket as phone delivery. Public buckets distinguish queued, provider accepted
(ticket awaiting receipt), provider handoff (successful Expo receipt), failed and
expired. `observed_phone_delivery` and `owner_saw_notification` remain `unknown`.
UI integration must explicitly label acceptance/handoff and offer permission,
registration, offline, quiet-hours, and provider-failure recovery; do not infer
that a test was seen from its HTTP response.

## Decision notification schema / remaining execution boundary

`gateway/app/notifications.py::DecisionNotification` and
`frontend/src/services/PendingIntentRouter.ts::DecisionNotification` define
`magistrate.decision-notification.v1`. A durable execution producer must supply
an Attention event's **`decision_notification`** object:

```json
{
  "schema_version": "magistrate.decision-notification.v1",
  "decision_id": "opaque-decision",
  "task_id": "opaque-task",
  "run_id": "opaque-run",
  "request_id": "opaque-request",
  "scope": "opaque-scope-reference",
  "revision": 1,
  "expires_at": 1800000000000
}
```

IDs/scope are opaque references (`[A-Za-z0-9._:-]`, 1–128 chars), not repository
paths, prompts, or permission grants. Revision is a positive signed-32-bit integer;
expiry is the runtime request's Unix-millisecond expiry. Unknown fields and missing
bindings fail closed. The push carries this object as `data.decision`, and its
route pins `item`, `decision`, and `revision`. Expired/malformed envelopes are not
pushed; the durable Attention item remains available. An expired tap may navigate
for read-only inspection; it is not an answer. Legacy projections remain explicitly
legacy, without fabricated task/run/request/scope/expiry values.

The existing Firstmate contract binds a private lifecycle identity and canonical
answer bytes, with session-bound confirmation TTL and idempotent delivery. It does
**not** yet publish this complete request-expiry envelope. The integration/execution
owner must bind that producer and make the destination compare the pinned revision
with current owner-authorized request state before presenting approval/decline.
The current `/attention` UI does not consume these added revision parameters.
Tests verify approval/decline **bytes** deliver once, stale/foreign confirmations
fail and expired confirmation is rejected. They do not certify that a real worker
continued/stopped, nor full request-expiry enforcement. No notification action
executes a command.

## Streaming and native acceptance blockers

Installed: Expo 57, React Native 0.86, `expo-audio ~57.0.4`,
`expo-notifications ~57.0.17`, `expo-dev-client ~57.0.18`. Expo Audio exposes file
recording; its PCM sampling callback is for an **AudioPlayer**, not microphone
capture. There is no installed native WebRTC or streaming microphone PCM transport.
The Gateway currently exposes transcription capability, not an authorized Realtime
session/credential and canonical voice event bridge. Do not label the existing
path as equivalent or mint credentials for an unusable native session.

A follow-up must select and compile a supported native streaming transport,
discover enabled provider models/settings from server capability authority, and
implement authorized short-lived Realtime credentials (where required), canonical
history/usage/tool sideband authorization, interruption, mute, network recovery,
and measured headset/foreground behavior. Persistent provider keys stay server-only.
No model capability or installed-native compatibility is inferred from API examples.

Official contracts inspected:

- https://docs.expo.dev/versions/v57.0.0/sdk/audio/
- https://docs.expo.dev/versions/v57.0.0/sdk/notifications/
- https://developers.openai.com/api/docs/guides/realtime-webrtc
- https://support.apple.com/guide/iphone/use-and-customize-the-action-button-iphe89d61d66/ios

Host: Linux. Attempted `xcodebuild -version` → `xcodebuild: command not found`
(exit 127). No Xcode/Apple SDK, native target compilation, signing, App Intent
metadata extraction/discovery, APNs credentials, TestFlight, or phone observation
is claimed. Expo prebuild and TypeScript are not substitutes for those gates.
See [iOS entry instructions](ios-voice-shortcuts.md) for owner/device actions.

## Reproducible validation

```sh
cd frontend
npx tsx --test tests/notification-native.test.ts tests/pending-intents.test.ts tests/voice-capture.test.ts tests/voice-session.test.ts tests/voice-branding.test.ts tests/ios-entry-config.test.ts
npm run test:voice && npm run test:notifications
npm run test:pending-intents
npm run typegen && npm run typecheck
npm run lint
npx expo export --platform web
cd ../gateway
uv run pytest tests/test_firstmate_decisions.py tests/test_notification_transitions.py tests/test_push_receipts.py
```

The review description records executed results, including failures/fixes. These
commands are hermetic contract evidence, not owner/device acceptance.
