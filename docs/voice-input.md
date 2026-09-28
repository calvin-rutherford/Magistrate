# Voice input slice

Magistrate has one microphone seam in `frontend/src/input/VoiceInputAdapter.ts`. Chat stops capture, transcribes through the selected input mode, and places the result in the composer; it never sends from the microphone action. Voice Mode uses the same seam and keeps its continuous listen/respond loop.

Input selection is device-local (`magistrate.voice.input-mode`) and intentionally separate from execution harness/model selection:

- **Automatic** preserves the existing path: browser speech may provide interim text, with authenticated gateway STT as the final transcript.
- **Browser speech** uses the browser SpeechRecognition API and does not upload audio for the final transcript.
- **Native device** uses Expo microphone capture and authenticated gateway STT.
- **Gateway OpenAI** uses the gateway's server-side OpenAI STT credential; no client secret is exposed.

`GET /api/v1/voice/capabilities` reports gateway configuration without returning credentials. Browser and native capabilities are determined locally. An unavailable locally-detectable persisted mode falls back to Automatic with a visible notice in Voice Mode; an unavailable gateway provider is reported as an authenticated transcription error. The chat composer reports a clear error and leaves existing text intact.

## Native Magi ownership

Chat microphone capture only populates the ordinary composer. Continuous Voice
Mode submits the final transcript to `/api/v1/magi/messages` with
`source: voice`, reconciles canonical rows into the same owner-scoped thread as
typed Chat, and speaks only the completed persisted assistant message. It has
no alternate assistant, worker target, terminal transcript, or legacy Voice
Move transport.

## Verification

The authenticated local loop was exercised by the existing Puppeteer web suites with fake media capture, including mic-to-composer, no implicit send, permissions/error handling, mode persistence, and continuous Voice Mode. Gateway STT adapter, capability, authentication-boundary, and voice-move tests run under `uv run pytest`.

The iOS App Intents, Shortcuts, Action Button entry, foreground lifecycle,
network recovery, input-route observation, and tetrahedron renderer are covered
by repository tests/configuration; see `ios-voice-shortcuts.md`. Physical-device
audio, Siri discovery, Action Button assignment, signing, and App Store console
evidence remain `BLOCKED_EXTERNAL`. Background capture and ambient listening
are intentionally unsupported; leaving the foreground stops unsubmitted audio.
