# Magistrate → Magi roadmap implementation report

**Audience:** Captain / merge authority  
**Baseline:** latest repository `main`, `513e3b5` (`fix: keep chat viewport on latest readable response (#52)`)  
**Reviewed:** 2026-08-31  
**Status:** reconnaissance and sequencing only. This report is not implementation authorization; preserve the captain-hold completion gate.

## Executive readout

The two authoritative DOCX files agree on one test: reduce the distance between human intent and **verified** reality, while moving the human above execution mechanics. The roadmap is a capability sequence, not a calendar promise:

`trustworthy owner beta → engineering command center → outcome delegation → deskless engineering → persistent Magi → general digital action → ambient → cognitive`.

The immediate work is not a larger dashboard. It is to make the existing Expo/Web → FastAPI Gateway → Herdr → Firstmate loop trustworthy enough that it can be used away from a workstation. The approved Deskless Operator Alpha direction narrows that first proof to one trusted owner, one operator-owned Gateway/runner, foreground voice, honest push fallback, and no friend/multi-user claims. A future friend preview is a separate identity/isolation decision.

**Recommendation:** finish the owner-alpha trust path, finish typed Attention delivery/routing, then add only a thin persistent Objective/evidence seam around the existing chat. Treat native, push, voice, redeploy, and physical-device evidence as gates—not as permission to add more dashboard surfaces. Do not close the alpha or invite friends until the named device acceptance artifact has observed evidence.

## Source audit

Both files were read in full, including headings, lists, tables, headers, and footers. Their DOCX packages contain no `word/media` parts and no comments, footnotes, or endnotes; `customXml` parts are empty. Header text is `MAGI / MAGISTRATE`; footer text is `CALM AT REST. SPECTRAL WHEN ALIVE.`

| Source | SHA-256 | Size | Visible canonical date | Package metadata |
|---|---|---:|---|---|
| `Magi_Manifesto.docx` | `9c92c96e069f715aeab8b18f0ea27b8c482e04e03426b787259ed23cc72ef592` | 46,491 B | August 30, 2026 | creator `python-docx`; created/modified December 23, 2013; revision 1 |
| `Magistrate_to_Magi_Roadmap.docx` | `6f4b3ad3841409864bfb4682ee7fe3bee407b73fbefce1495e2aa88427825e58` | 52,371 B | August 30, 2026 | creator `python-docx`; created/modified December 23, 2013; revision 1 |

The 2013 package timestamps conflict with the visible August 30, 2026 canonical date. This is metadata provenance, not a product contradiction; use the visible canonical content and hashes above as the reviewed source identity.

## North-star principles extracted from the sources

1. **Intent → verified reality:** success is a real desired state established by tests, evidence, or other verification—not plausible text or an agent's assertion.
2. **Magi and Magistrate are layers:** Magi captures context, intent, memory, attention, and outcomes; Magistrate governs objectives, delegation, authority, routing, policy, audit, schedules, recovery, and verification; engines remain replaceable.
3. **Outcomes over sessions:** the durable object is the desired state/objective, not a chat transcript, terminal, agent, or model session.
4. **Autonomy inside authority:** act freely inside explicit scopes and stop at consequential judgment; preserve confirmation, revocation, auditability, and reversibility.
5. **Attention compression:** busy machine organization must yield fewer, more contextual interruptions; honor quiet periods and learn preferences only visibly and controllably.
6. **Context before commands:** retain relevant project, history, constraints, and current state so the person does not repeat machinery-level instructions.
7. **Model/harness independence:** route Claude Code, Codex, Pi, Gemini, local models, APIs, and future tools beneath a stable relationship.
8. **Persistent continuity:** objectives survive conversations, devices, restarts, and agent replacement; memory is separate from conversation and provenance-bearing.
9. **Calm interface:** detail is available on demand, but the default surface should be a compressed executive briefing rather than a live fleet obligation.
10. **Measure disappearance of human labor:** prioritize human interventions per verified outcome, objective success, recovery, Attention precision, decision latency, false “done” rate, provider portability, and trust incidents.

## Milestones, dependencies, and sequencing

### Phase 0 — Trustworthy owner/friend-beta foundation

The roadmap's Phase 0 exit is a new friend opening HTTPS, authenticating, issuing work, seeing progress, safely refreshing, recovering from expiry/revocation, and continuing without SSH rescue. Required foundations include an application auth gate, persistent data outside the mutable checkout, CI auth coverage, authenticated deployment smoke, degraded states, and the live Gateway → Herdr → Firstmate loop.

The approved Alpha direction is intentionally narrower than the roadmap's eventual “friend beta”: one trusted owner with command/voice scopes against one operator-owned Gateway/runner. It explicitly is **not** friend preview, tenant isolation, or multi-user SaaS. The physical path is `iPhone → HTTPS/WSS Gateway → private Herdr → Firstmate/harnesses`; no runner address, socket, bootstrap secret, provider credential, or harness credential belongs in the app.

**Dependencies:** production HTTPS/WSS and persistent state; secure native session storage; a physical EAS build; exact intent routing; real notification-provider evidence; foreground voice acceptance; and the DEAT-001 device acceptance record. The alpha artifact says these may be planned/foundation work, but its physical and provider rows remain unexecuted.

### Phase 1 — Personal engineering command center

Make Magistrate the normal place for one technical user to understand, direct, and recover a day of engineering. Keep chat, fleet, worktree, agent, and terminal detail for transparency while trust is earned, but make them secondary to an executive summary. Add a persistent Objective and unified timeline (intent, plan, delegation, decisions, verification, artifacts, outcome), a common state contract across harnesses, first-class typed Attention, resumable jobs, audit-grade activity, backups, rate/budget controls, and crash-safe objective state.

**Dependency:** Phase 0 trust and operational reliability. A thin objective ledger is appropriate next; a planner/delegation engine is not.

### Phase 2 — Executive engineering

Move from controlling agents to delegating outcomes: objective graph, intent compiler, revisable planner, resource router, policy-aware autonomy, independent verification, bounded recovery, and explicit decision memory. The exit is a multi-hour objective delegated once and completed with sparse judgment.

**Dependency:** durable Objective/evidence records and trustworthy state from Phase 1. Do not infer this capability from current synchronous prompt acknowledgements.

### Phase 3 — Deskless engineering

Make phone/voice a first-class command surface: fast voice engage, contextual briefings, high-value push with inline decisions where platform rules permit, streaming/interruption, compact artifact review, secure remote authorization, outcome-level voice commands, and safe offline queuing. The exit is majority of routine supervision off-desk.

**Dependency:** Phase 2 objectives, verification, authority, and recovery; plus physical iPhone/network/audio/push evidence. The Alpha's foreground-only voice is a foundation, not Phase 3 completion.

### Phases 4–7 — later architecture

- **Phase 4, Persistent Magi:** long-term memory with provenance, correction/forgetting, policy and objective continuity across devices and engine replacement, and explanations grounded in evidence.
- **Phase 5, General digital action:** research first, then documents/operations, communications/scheduling, business systems, finance, and infrastructure/device control; each action needs intent, authority, plan, evidence, risk, escalation, and audit.
- **Phase 6, Ambient:** optional app opening through earbuds/glasses, explicit context permissions, attention arbitration, cross-device continuity, and local/private processing where possible.
- **Phase 7, Cognitive:** hardware-contingent subvocal/neural channels with *more conservative* intent confirmation as input becomes less explicit.

These phases must not drive current UI or hardware scope. The roadmap explicitly says not to build speculative neural interfaces, broad consumer automation, every coding-harness feature, or fleet visualizations before the underlying trust stack.

## Baseline comparison: current main vs Alpha and roadmap

### Already implemented on current main

- **Active architecture:** Expo/React Native/Web client, FastAPI Gateway, Herdr terminal/socket/CLI integration, Firstmate fleet snapshot/attention, and provider adapters. The older Django/Celery government model is legacy/exploratory on this path (`README.md`, `gateway/app/main.py`, `docs/architecture.md`).
- **Application session gate:** short-lived opaque bearer sessions, server-side SHA-256 token digests, expiry/revocation, scopes, 401 invalidation, 403 preservation, logout, and a protected-route gate (`gateway/app/auth.py`, `frontend/src/api/client.ts`, `frontend/app/_layout.tsx`).
- **Chat/live loop:** bounded Herdr history parsing, typed tool rows, persisted normalized captain messages in `ConversationSession`, HTTP fallback, authenticated WSS first-frame auth, and explicit harness/provider/model routing context (`gateway/app/herdr_client.py`, `frontend/src/services/ConversationSession.ts`, `frontend/src/realtime/socket.ts`, `gateway/app/execution_capabilities.py`).
- **Attention foundation:** real Firstmate/GitHub/Jira/Teams aggregation, keyed transition fingerprints, quiet hours, restricted/moderate/full notification policy, in-app fallback, Expo token registration endpoint, server-side reconciler, Expo push request handling, bounded retries, and invalid-token revocation (`gateway/app/attention_service.py`, `gateway/app/notifications.py`, `frontend/src/services/NotificationManager.ts`, `docs/notifications.md`).
- **Foreground voice:** Expo microphone capture/metering, browser interim speech where available, final Gateway STT, continuous Voice Mode, TTS, tap-to-finish/interruption, visible states, and confirmation for control/prompt moves (`frontend/app/voice.tsx`, `frontend/src/input/VoiceInputAdapter.ts`, `gateway/app/stt_adapter.py`, `gateway/app/voice_moves.py`).
- **Redeploy safeguards:** guarded clean-checkout fast-forward deployment, production env validation, external persistent SQLite requirement, static export, restart, bounded HTTP readiness polling, and an optional trusted-host authenticated smoke (`scripts/deploy_magistrate.sh`, `scripts/smoke_magistrate.sh`, `docs/deployment.md`).

These are foundations and source-level implementations, not all exit-gate evidence.

### In progress / foundation with open evidence

- **Native/iPhone:** `app.json` has iOS identifiers, permissions, URL scheme, notifications/audio plugins; `eas.json` has development/preview/production profiles. Current development is simulator-only and there is no EAS project ID, physical build, TestFlight install, signed archive, or device result. The Alpha's physical-device EAS and DEAT-001 direction is approved but not in baseline `main`.
- **Trusted-device auth:** current session auth is a useful single-operator bearer foundation, but the native bearer is persisted in AsyncStorage. There is no Keychain/SecureStore seam, pairing flow, device identity, invite issuance, refresh token, tenant isolation, or per-user runner authorization. Alpha explicitly defers one-time trusted-device pairing/refresh; this does not make current storage device-trusted.
- **Push Attention:** registration and server dispatch code exist, but the current `push_tokens` primary key is `user_id` (one device overwrites another), there is no durable worker/receipt lifecycle, and no physical APNs/FCM/provider delivery evidence. Foreground polling/in-app fallback must not be called background push.
- **Voice:** functionally real on web/source and designed as foreground-only for Alpha, but no iPhone microphone/TTS/audio-route/interruption/background evidence exists. `SiriShortcutAdapter` currently emits `magistrate:` URLs for `/chat?record=true`; it is not an App Intent/Siri registration and `/chat` does not consume `record`.
- **Redeploy/health:** deployment automation verifies process reachability (2xx/401/403) and can opt into a host-local authenticated smoke. The Actions path is not an external authenticated HTTPS/WSS client acceptance test; no cellular/WSS/physical evidence is present.
- **Chat continuity:** normalized captain messages persist locally and Voice shares the `captain` target, but this is not the roadmap's cross-device persistent objective/context graph. Herdr terminal scrollback remains a separate, parsed source.

### Next actionable implementation

1. **Close the owner-alpha trust seam without new dashboard UI:** add the approved native EAS/config path and native SecureStore-backed session storage; preserve the existing validated route gate, explicit bootstrap re-entry after expiry, public HTTPS/WSS configuration, and no secret in bundles. Add the exact DEAT-001 evidence fields/automated contract coverage, but do not mark device rows passed without a physical run.
2. **Make Attention a typed, exact target contract:** centralize versioned pending-intent parsing/queueing for `/voice?autostart=true`, `/attention?item=…`, `/chat?agentId=…`, and `/pr-detail?number=…`; bind it across app URL events, warm/cold push responses, and auth restoration. Then complete multi-device token rows, provider receipt/error lifecycle, and an operated/durable reconciliation path. Keep in-app fallback honest.
3. **Add a thin Objective/evidence ledger behind chat:** persist desired state, constraints, status, acceptance/evidence references, decisions, and linked artifact/run identifiers for one objective flow; expose a concise timeline or API seam while retaining the current chat. Do not add a planner, general action fabric, fleet dashboard, or broad domain adapters in this slice.

Foreground voice tests, redeploy authenticated smoke, and the physical iPhone/network matrix should be acceptance gates attached to slices 1–2, not reasons to expand the product surface. If a gate fails, record the defect rather than replacing it with a web/export claim.

### Later architecture

- Objective graph with parent/child outcomes, dependencies, acceptance tests, completion evidence, intent compiler, revisable planner, resource router, policy-aware autonomy, independent verifier, recovery engine, and decision memory (Phase 2).
- Durable cross-device memory/policy and objective continuity (Phase 4).
- General digital action adapters, finance/infrastructure authorization, ambient devices, and neural interfaces (Phases 5–7).
- Separate multi-user identity/invites, refresh/device/session management, tenant-isolated storage/execution/memory/tools, and authenticated runner service identity/mTLS/ACLs. These are future production architecture, not prerequisites to claim owner alpha, but are prerequisites to any genuine friend/multi-user product.

### Unsupported claims to avoid

- “Native iPhone beta,” “TestFlight-ready,” “Siri,” “Action Button,” “background voice,” “continuous listening,” “AirPods routing,” or “push delivery” without the corresponding physical/provider/archive evidence.
- “Trusted device auth” when the current native secret is AsyncStorage and bootstrap re-entry remains required.
- “Multi-user/friend preview” from a `default_user` deployment and shared bootstrap model. Observer sessions with `read,notifications` and no execution/voice access are the Alpha direction for a future restricted cohort; issuance is not implemented here.
- “Verified outcome,” “objective completion,” or “correlated final agent result” from a prompt acknowledgement, terminal snapshot, local chat message, or agent assertion. Current active FastAPI code has no persistent Objective/evidence/run-result graph. Legacy Django models named `Objective`, `Artifact`, `Evidence`, and `AuditEvent` exist, but they are not the active Gateway product path (`backend/agents/models.py`).
- “Healthy deployment” from an unauthenticated process/readiness probe alone. The trusted smoke is an operator action and still does not prove external HTTPS/WSS, cellular, native, or physical behavior.
- “Magi is already the executive layer” or “the dashboard has disappeared.” Current chat/fleet/agent views remain the primary interaction model; the roadmap says to earn trust first and make detail secondary over time.

## Contradictions and captain decisions

1. **Phase 0 wording vs Alpha boundary:** the roadmap says “friend beta,” while approved Alpha says owner-only and explicitly not friend preview. Resolve by sequencing: close owner Alpha first; keep the friend gate open until identity, restricted scopes, and isolation are deliberately approved. Decide whether any future friends are observers only (`read,notifications`) or receive command/voice access under an explicitly accepted shared-operator risk. Do not silently broaden scopes.
2. **Native URL adapter vs native Siri:** current URL construction is a fallback seam, not App Intent support. Decide later whether to fund a signed iOS App Intent/App Shortcut that opens `/voice?autostart=true`; never describe the URL adapter as Siri/Action Button implementation.
3. **“Persistent” chat vs persistent objectives:** local AsyncStorage transcript continuity is not cross-device objective continuity. Decide the first Objective schema/evidence contract before adding more chat persistence or dashboard views.
4. **Push semantics:** current server reconciliation and Expo HTTP ticket handling are useful foundations, but the captain must decide the operating model for the first real device (durable worker vs deliberately operated poller), token fan-out, receipt retention, and exact notification target UX. These are implementation choices within the source direction, not permission to claim delivery now.
5. **Voice scope:** Alpha's honest first contract is foreground final STT/TTS, visible capture, and tap interruption. Decide only after device evidence whether streaming, VAD, automatic barge-in, route changes, or background transitions deserve Phase 3 work.
6. **Legacy government architecture:** the roadmap says not to let the old metaphor force unnecessary complexity. Decide whether the Django/Celery models remain a documented legacy surface or are retired; do not build a second Objective/governance system beside the active FastAPI path without an explicit reactivation decision.
7. **Captain hold / implementation authorization:** this report identifies sequencing and gates only. A green test, roadmap recommendation, or Alpha artifact does not authorize implementation, deployment, friend invitation, merge, or closure of DEAT-001.

## Evidence snapshot

- Baseline source inspection: `gateway/app/auth.py`, `gateway/app/main.py`, `gateway/app/notifications.py`, `gateway/app/herdr_client.py`, `frontend/src/api/client.ts`, `frontend/src/services/NotificationManager.ts`, `frontend/src/services/ConversationSession.ts`, `frontend/src/services/SiriShortcutAdapter.ts`, `frontend/app/_layout.tsx`, `frontend/app/voice.tsx`, `frontend/app.json`, `frontend/eas.json`, deployment scripts/docs, and legacy `backend/agents/models.py`.
- `cd frontend && npx tsc --noEmit`: **passed** on this checkout.
- `cd gateway && PYTHONPATH=. uv run pytest -q`: **128 passed, 1 failed**. The failure is `tests/test_gateway.py::test_static_spa_deep_links_serve_the_exported_frontend`, because this checkout has no exported `frontend/dist` (`/chat` and `/voice` return 404). Five deprecation warnings were emitted. This is baseline evidence, not a fix made for this report.
- `cd frontend && npm test`: started successfully; the run was terminated by the scout timeout during the long `test:chat` stage after earlier suites passed. No completion claim is made for the full suite.
- No EAS project/build, signed archive, TestFlight install, physical iPhone, APNs/FCM delivery, cellular endpoint, WSS handoff, Siri/App Intent, Action Button, Bluetooth route, background voice, or DEAT-001 result was observed.

## Proposed acceptance order

1. Captain chooses/records owner-alpha versus any future observer policy; no friend access is implied.
2. Native config + SecureStore + public HTTPS/WSS are built and checked without secrets in the bundle.
3. DEAT-001 install/auth/deep-link/security cases are run on one physical iPhone.
4. Push registration, provider ticket/receipt behavior, exact targets, duplicate/revision handling, and honest fallback are observed.
5. Foreground voice, interruption, permission, and background-stop behavior are observed; no ambient claim is made.
6. Authenticated deployment smoke and external Wi-Fi/cellular/WSS checks pass.
7. A thin Objective reaches a real acceptance criterion with linked evidence; only then consider Phase 1 expansion.

**Bottom line:** build less surface area, persist the outcome, verify it independently, and make the phone a trustworthy command surface before making it ambient. The workstation disappears only after the machinery underneath is reliable enough to deserve disappearance.
