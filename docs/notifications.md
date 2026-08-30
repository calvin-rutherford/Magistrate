# Attention notifications

The Gateway is the source of truth for Captain Attention transitions. It
reconciles an item fingerprint (`kind`, revision, copy, status, and deep link)
and sends at most one Expo push for each active fingerprint. Provider errors
retry with bounded backoff; invalid device tokens are revoked. Quiet hours
defer a transition rather than acknowledging it, so it is eligible after the
quiet window. A successful remote send is the only native delivery that marks
the transition delivered.

Native beta builds obtain a real `ExponentPushToken[...]` from
`expo-notifications`, register it over the authenticated Gateway session, and
receive push data containing an app-owned deep link (`/attention`, `/chat`, or
`/pr-detail`). A device permission denial, missing EAS project/credentials,
simulator, offline Gateway, or provider failure is shown as unavailable and
keeps the item in the in-app Attention fallback. The app never schedules a
local notification from a foreground poll and never claims that this is
background push.

Web retains its fallback behavior: with browser permission granted, the open
page uses the browser Notification API; denied or unsupported browsers get the
in-app stack. Web notifications require an open and eligible browser tab (they
are not a service-worker push subscription). Native remote push is the beta
background channel and still depends on a physical-device/release build with
valid Apple/FCM/EAS credentials; Expo Go and simulators cannot validate
production delivery.

## Operating-permission modes

Settings persist `restricted` (restricted / ask-first), `moderate`, or `full`.
They select notification policy only: restricted surfaces decisions/blockers,
moderate also includes review-ready PRs and meaningful milestones, and full
suppresses routine progress while retaining stalls, failures needing Captain
action, completions, and consequential decisions. These modes do **not** grant
merge, destructive, irreversible, security-sensitive, or external-public
authority. Existing Firstmate policy, command scopes, and Captain confirmation
rules remain the authority for every operation.
