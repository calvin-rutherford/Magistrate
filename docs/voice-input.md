# Voice input slice

Magistrate has one microphone seam in `frontend/src/input/VoiceInputAdapter.ts`. Chat stops capture, transcribes through the selected input mode, and places the result in the composer; it never sends from the microphone action. Voice Mode uses the same seam and keeps its continuous listen/respond loop.

Input selection is device-local (`magistrate.voice.input-mode`) and intentionally separate from execution harness/model selection:

- **Automatic** preserves the existing path: browser speech may provide interim text, with authenticated gateway STT as the final transcript.
- **Browser speech** uses the browser SpeechRecognition API and does not upload audio for the final transcript.
- **Native device** uses Expo microphone capture and authenticated gateway STT.
- **Gateway OpenAI** uses the gateway's server-side OpenAI STT credential; no client secret is exposed.

`GET /api/v1/voice/capabilities` reports gateway configuration without returning credentials. Browser and native capabilities are determined locally. An unavailable locally-detectable persisted mode falls back to Automatic with a visible notice in Voice Mode; an unavailable gateway provider is reported as an authenticated transcription error. The chat composer reports a clear error and leaves existing text intact.

## Verification

The authenticated local loop was exercised by the existing Puppeteer web suites with fake media capture, including mic-to-composer, no implicit send, permissions/error handling, mode persistence, and continuous Voice Mode. Gateway STT adapter, capability, authentication-boundary, and voice-move tests run under `uv run pytest`.

Not verified in this worktree: an Expo iOS/Android native build on a physical iPhone, microphone permission UX on device, audio codec behavior on device, Siri/Action Button invocation, lock-screen/background capture, and native streaming STT. The existing deep-link/Siri seam only requests foreground chat capture; it is not a claim that Action Button or background voice is complete.
