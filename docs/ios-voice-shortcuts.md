# iOS voice, App Intents, Shortcuts, and Action Button

Magistrate's iOS App Intents are installed into the foreground app target by
`frontend/plugins/withMagistrateAppIntents.js`. The Swift source of record is
`frontend/native/ios/MagistrateAppIntents.swift`.

Declared App Shortcuts are **Open Voice**, **Talk**, **Ask**, **Work status**,
**Attention**, and **Start Voice**. Swift identifiers remain stable; visible
titles/descriptions are neutral. App Shortcut phrases retain Apple's required
application-name interpolation.
On a supported iPhone, choose Settings → Action Button → Shortcut → Start Voice.
All voice entries open `/voice?autostart=true&entry=<invocation-uuid>`; the UUID
suppresses duplicate callbacks while allowing a later deliberate invocation.
They do not run a second assistant, perform background capture, or carry speech
in a URL. Discovery still requires a signed iOS build and on-device verification.

To create **Wake Magi**, open Apple's Shortcuts app, add Magistrate's **Start
Voice** action, and name your user-created shortcut **Wake Magi**. Invoke it with
Siri using that shortcut name. The app foregrounds, restores/validates the session,
and requests microphone consent before listening. Denial leaves recoverable
Settings guidance; it must not bypass consent. This is a user-created Siri
shortcut, **not** a custom wake word or arbitrary side/power-button control. Work summary and Attention entries open
allowlisted routes behind the normal authenticated route gate. “Work
status” submits a fixed product query through the same owner-scoped Native
Magi conversation.

Voice recording is foreground-only. Leaving the foreground stops unsubmitted
capture and TTS. Returning refreshes the canonical conversation. A network or
audio-service interruption pauses capture without sending partial audio; route
changes are surfaced while capture continues when iOS can preserve it. Voice
input exposes microphone mute, pause, speech interruption, and end controls.
Pausing speech or leaving the app never cancels submitted engineering work.
Reduce Motion freezes the existing monochrome tetrahedron and amplitude motion.

**Streaming Voice Mode is unavailable in this build.** The labeled fallback is
record/upload/transcribe/send plus device TTS, not speech-to-speech parity.
[Native loop evidence](native-loop-evidence.md) records the missing installed
native transport and server session bridge; no provider model is assumed.

## Native verification

From `frontend/`:

```sh
npm run typecheck
npm run test:voice
# On a macOS host with Xcode and signing, generate the project if needed:
npx expo prebuild --platform ios --no-install
# Generation is NOT compilation. Confirm MagistrateAppIntents.swift is in Compile Sources.
npx expo run:ios --device
# Retain the actual Xcode build log and physical-device result.
```

On a signed physical iPhone, verify first-use/denied microphone permission,
Bluetooth/wired/speaker route changes, phone-call interruption, background and
foreground transitions, Wi-Fi/cellular loss and recovery, VoiceOver, largest
Dynamic Type, Reduce Motion, all six shortcuts, the user-created Wake Magi
shortcut, duplicate cold/warm callbacks, later repeated entry, and Action Button
assignment. Verify a pending notification survives session renewal and opens the
exact current/expired decision without approving it.
Confirm every spoken turn appears in the same Chat thread.

`BLOCKED_EXTERNAL`: App Store signing, an Apple Developer/App Store Connect
record, production APNs/EAS credentials, TestFlight processing, physical-device
shortcut discovery, Action Button assignment, and on-device audio-route results
require the release operator's certificates, consoles, and hardware. Repository
configuration and tests cannot truthfully close those gates.
