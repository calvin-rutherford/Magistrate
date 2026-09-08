# Opus P5 — native activity/recovery evidence

This is compact software evidence for the native activity/recovery slice based on Magistrate `ce5db24`. It is not a deploy, notification-timeliness, or physical-iPhone acceptance claim.

## Observed software evidence (2026-09-08)

| Boundary | Command / observation | Result |
|---|---|---|
| Gateway authority and isolation | `cd gateway && PYTHONPATH=. uv run pytest -v` | **PASS — 337 tests** |
| Frontend behavior, browser recovery, auth isolation | `cd frontend && npm test` | **PASS — 206 tests** |
| Type generation and types | `cd frontend && npm run typegen && npm run typecheck` | **PASS** |
| Lint | `cd frontend && npm run lint` | **PASS — 0 errors, 29 warnings** |
| Web production bundle | `cd frontend && npx expo export --platform web` | **PASS — 25 static routes** |
| Unsigned native bundle | `cd frontend && npx expo export --platform ios --output-dir /tmp/magistrate-opus-native-activity-ios --clear` | **PASS — 1,741 modules; 4.4 MB Hermes bundle** |
| Patch hygiene | `git diff --check` | **PASS** |

Focused observed behavior includes a 430×820 mobile viewport showing exactly `Magi is working · 12 operations`, a tappable bounded native-style activity sheet with all 12 operation records, stable in-place revisions under delayed snapshot/realtime overlap, cold-start retention of an exact keyed decision during an outage, deep-link reuse of `captain-question-<decision_key>`, synchronous prior-principal cache eviction on account change/logout/expiry/401, overlap pagination in which 90 repeated focus rows do not consume the 400-row history budget and the final request is bounded to the remaining 80 rows, and historical-page admission that cannot checkpoint past unseen replay rows.

### expo-doctor baseline (not suppressed)

`cd frontend && npx expo-doctor` reports **19/21 checks passed** and exactly two failures:

1. `Check for legacy global CLI installed locally`: use the supported `npx eas-cli@23.0.0` invocation; EAS CLI is not a project dependency.
2. `Check that packages match versions required by installed Expo SDK`: six existing patch mismatches — `@expo/ui` 57.0.15 vs 57.0.17, `expo` 57.0.19 vs 57.0.21, `expo-glass-effect` 57.0.1 vs 57.0.2, `expo-image-picker` 57.0.15 vs 57.0.16, `expo-notifications` 57.0.16 vs 57.0.17, and `expo-router` 57.0.18 vs 57.0.20.

These are not introduced by this slice: `git diff --exit-code ce5db24 -- frontend/package.json frontend/package-lock.json` returned 0. They remain visible baseline debt rather than being suppressed or folded into this correctness-focused change.

## Physical iPhone SOAK — **NOT RUN / UNPASSED**

Required setup: a physical supported iPhone, a deployed build using this commit, a reachable staged Gateway with real structured Firstmate activity, and two test principals. Record screen/video, Gateway cursor diagnostics, iOS/build versions, exact times, and network transitions for every step.

- [ ] Start one objective that emits at least 12 real operations. Confirm one captain turn, ordered progress/decision/continuation/final messages, and a tappable count matching Gateway summary.
- [ ] While activity grows, alternate Wi-Fi/offline three times. Confirm stable rows revise in place, no duplicates/gaps appear, and disconnect reads as recovering/interrupted—not completed.
- [ ] Background for 10 minutes, create activity and an exact keyed decision from another client, then foreground. Confirm snapshot-plus-cursor replay catches up without moving the composer, queue, keyboard, or scroll position.
- [ ] Pull to refresh while text is entered and while a prompt is queued. Confirm neither is lost or resent.
- [ ] Force-quit while the decision is pending, create more operations, and cold-launch. Confirm cached state appears only for that principal, remains explicitly recovering until verified, and converges to Gateway revisions.
- [ ] Open the decision from both its assistant message and activity row. Confirm both focus the same Attention item; viewing/read state is separate from approve/reject resolution and stale revisions fail closed.
- [ ] Sign out and sign in as principal B while A has visible activity. Confirm no A title, summary, objective, run, operation, or decision paints for B, including during transition frames; switch back and recover A from Gateway.
- [ ] Complete, fail, and cancel separate objectives. Confirm terminal rows persist and each semantic label remains distinct.
- [ ] Exercise more than one activity page, largest Dynamic Type, VoiceOver, Reduce Motion, dark/light environments, keyboard show/hide, rotation, and home-indicator safe area. Confirm controls remain reachable and labels read meaningfully.
- [ ] Restart Gateway without deleting its SQLite store during foreground and background cases. Confirm cursors and stable ids survive with no duplicate or false terminal state.

## Deliberately deferred findings

The existing `activity.v1` source projection distinguishes `active`, `awaiting-user`, `resolved`, `completed`, `failed`, and `cancelled`; it does not provide separate authoritative `queued` versus `running`, or `blocked-external` versus `awaiting-user`, evidence. This slice does not infer those phases from prose or terminal state. A follow-up can add source-backed phase semantics additively once Firstmate exposes them. Physical background notification timeliness and process execution remain separate release/soak gates.
