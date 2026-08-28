# Magistrate refinement change log

## Brand guide pages

- Page 2 modified: elevated **Calm at rest. Spectral when alive.** into the governing behavior rule and defined dormant, listening, executing, and immersive expression.
- Page 4 modified: split activation into functional cyan/blue-violet and full Magistrate spectrum; preserved semantic colors.
- Page 6 modified: made conversation the canonical product center, added in-chat voice, the operational drawer, reusable agent chat, and equal light-mode hierarchy.
- Page 7 modified: specified Attention, Fleet Summary, Recent Activity, and authenticated Connections.
- Page 9 modified: established conversational voice as default and immersive Voice Call as optional; preserved thread continuity.
- Page 10 modified: added fleet reuse, mobile stability, and cross-medium hierarchy.
- Page 11 modified: updated the handoff and locked the system for product validation.
- The prior guide's connection-behavior page was replaced by operational and voice-continuity pages; restart-preserves-thread and explicit-end-confirmation remain in the implementation specification.

## Voice system

- Voice no longer requires a full-screen takeover.
- Default voice remains inside the conversation with live transcription, waveform, voice state, and continuous listening -> thinking -> speaking -> listening behavior.
- The full organic triangle is retained as optional immersive Voice Call mode.

## Spectral color

- Added a restrained functional level using cyan and blue-violet.
- Reserved the existing full green -> cyan -> violet -> magenta spectrum for immersive and high-expression moments.
- Preserved green, amber, and red semantic meanings.

## Chat and navigation

- Recentered the application on Firstmate conversation.
- Added jump-to-latest and realtime-message behavior to the product specification.
- Locked drawer order: Attention, Fleet Summary, Recent Activity, Connections, Settings.

## Fleet, attention, and connections

- Agent conversations now reuse the canonical chat UI.
- Attention cards are limited to human judgment and actionable review.
- Recent Activity records meaningful outcomes rather than internal operations.
- Connections now covers OAuth identity, capabilities, health, authorization, reconnect, and disconnect states without fake toggles.

## New tokens

- `color.spectrum.functional`
- `color.spectrum.full`
- `voice.listening`, `voice.thinking`, `voice.speaking`
- `attention.warning`, `attention.critical`
- `agent.active`, `agent.idle`, `agent.waiting`, `agent.blocked`

## Intentionally unchanged

- Magistrate name and no-tagline policy
- Wordmark and Bodoni Moda / Inter relationship
- Inverted triangle and centered spiral geometry
- Neutral-white / gray / black foundations
- Light and dark environments
- Atmospheric glass and background system
- Organic active artwork and full spectral palette
- App icon system
- Existing token names and core motion timings
- Premium, calm, restrained executive tone
