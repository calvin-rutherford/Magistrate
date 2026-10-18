# Attention notifications

The Gateway is the source of truth for Captain Attention transitions. It
reconciles an item fingerprint (`kind`, revision, copy, status, and deep link)
and tracks one durable delivery per active fingerprint/token, with at most three
network send attempts. An ambiguous network failure can duplicate a provider
push, but never grants duplicate execution authority. Sparse supported kinds
cover decisions, completion, actionable failure/stall, review-ready results,
budget/credit, repository disconnect, and payment issues; ordinary milestones
are not sent. Visible push copy is
fixed product language: task, repository, provider, host, path, and billing
identifiers never appear on the lock screen. App-owned payload targets retain
only the opaque key needed to open the authenticated Attention/Magi destination. Provider errors
retry with bounded backoff; invalid device tokens are revoked. Quiet hours
defer a transition rather than acknowledging it, so it is eligible after the
quiet window. An Expo ticket records provider acceptance; an Expo receipt records
provider handoff to APNs/FCM, **not observed phone arrival or viewing**. The app
keeps actionable items unread until the authenticated destination is opened and
acknowledged. The legacy DB `delivered` field must not be presented as evidence
that a phone displayed the notification.

Native beta builds obtain a real `ExponentPushToken[...]` / `ExpoPushToken[...]` from
`expo-notifications` after an explicit owner action in Account, register it over
the authenticated Gateway session, and receive push data containing an
app-owned deep link (`/attention`, `/chat`, or `/pr-detail`). The payload also
carries `intent_version: 1`, `target_type`, `target_id`, and `route`; RootLayout
passes that route through the shared pending-intent parser. A device
permission denial, missing EAS project/credentials, simulator, offline
Gateway, or provider failure is shown as unavailable and keeps the item
available through the Attention drawer and its unread indicator. There is no
in-app notification popup. The app never schedules a local notification from
a foreground poll and never claims that this is background push.

Web retains its fallback behavior: with browser permission granted, the open
page uses the browser Notification API; denied or unsupported browsers keep the
item in the Attention drawer with the unread indicator. Web notifications
require an open and eligible browser tab (they are not a service-worker push
subscription). Native remote push is the beta background channel and still
depends on a physical-device/release build with valid Apple/FCM/EAS
credentials; Expo Go and simulators cannot validate production delivery.

## Operating-permission modes

Settings persist `restricted` (restricted / ask-first), `moderate`, or `full`.
They select notification policy only: restricted surfaces decisions/blockers
and account/repository issues, moderate also includes review-ready PRs,
and sparse outcomes, and full suppresses routine review
progress while retaining stalls, failures, completions, consequential
decisions, and account/repository issues. These modes do **not** grant
merge, destructive, irreversible, security-sensitive, or external-public
authority. Existing Firstmate policy, command scopes, and Captain confirmation
rules remain the authority for every operation.

## Native loop integration

See [native loop evidence](native-loop-evidence.md) for the exact
`magistrate.decision-notification.v1` producer/consumer seam, owner-triggered
neutral test endpoint (requires integration mounting), token rotation handling,
and explicit streaming/native/device blockers. A tap is navigation only and is
not an approval, even if a notification action identifier says `approve`.

## Keyed Attention decisions (MVP boundary)

A concrete Firstmate `needs-decision` item may include an
`attention-action.v1` contract. Its server-issued `action_key` is bound to the
exact Firstmate `task_id`, `decision_key`, and live source revision. The only
supported actions are `approve` and `reject` for that captain-hold record.

The app first requests a confirmation token, then shows the action, exact
Firstmate target, consequence, and reversibility before the owner can confirm.
Opening a detail, reading it, acknowledging a notification, dismissing it, or
clearing the unread dot never authorizes an action. The Gateway checks the
owner session, action key, target, live revision, risk boundary, confirmation,
and replay/idempotency state before calling Firstmate's keyed captain-hold
intake. Outcomes are durable and bounded (`pending`, `succeeded`, `failed`, or
rejected/stale) with actor session, timestamp, selected action, target, source
revision, and safe operation evidence only.

This is **not** general approval authority: GitHub merges/reviews, deploys,
external communications, credential/security changes, destructive operations,
and irreversible requests are not supported and are rejected. Notification
acknowledgement remains a separate viewed/unread operation.
