# iOS voice, App Intents, Shortcuts, and Action Button

Magistrate's iOS App Intents are compiled into the foreground app target by
`frontend/plugins/withMagistrateAppIntents.js`. The Swift source of record is
`frontend/native/ios/MagistrateAppIntents.swift`.

Supported shortcuts are **Start Magistrate**, **Talk to Magistrate**, **Ask
Magistrate**, **What's running?**, **What needs attention?**, and **Magistrate
Voice**. The last shortcut is the named Action Button entry. In iOS Settings,
choose Action Button → Shortcut → Magistrate Voice. All voice entries open
`/voice?autostart=true`; they do not run a second assistant, perform background
capture, or carry speech in a URL. Work summary and Attention entries open
allowlisted routes behind the normal authenticated route gate. “What's
running?” submits a fixed product query through the same owner-scoped Native
Magi conversation.

Voice recording is foreground-only. Leaving the foreground stops unsubmitted
capture and TTS. Returning refreshes the canonical conversation. A network or
audio-service interruption pauses capture without sending partial audio; route
changes are surfaced while capture continues when iOS can preserve it. Voice
Mode exposes explicit mute, cancel-turn, barge-in, and end controls. Reduce
Motion freezes the tetrahedron and removes animated amplitude/ripple motion.

## Native verification

From `frontend/`:

```sh
npm run typecheck
npm run test:voice
npx expo prebuild --platform ios --no-install --clean
# Confirm ios/Magistrate/MagistrateAppIntents.swift is in Compile Sources.
npx expo run:ios --device
```

On a signed physical iPhone, verify first-use/denied microphone permission,
Bluetooth/wired/speaker route changes, phone-call interruption, background and
foreground transitions, Wi-Fi/cellular loss and recovery, VoiceOver, largest
Dynamic Type, Reduce Motion, all six shortcuts, and Action Button assignment.
Confirm every spoken turn appears in the same Chat thread.

`BLOCKED_EXTERNAL`: App Store signing, an Apple Developer/App Store Connect
record, production APNs/EAS credentials, TestFlight processing, physical-device
shortcut discovery, Action Button assignment, and on-device audio-route results
require the release operator's certificates, consoles, and hardware. Repository
configuration and tests cannot truthfully close those gates.
