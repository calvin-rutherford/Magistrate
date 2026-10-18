# Magistrate: bounded engineering-assistant case study

## Problem and boundary

An engineer needs to converse, authorize a bounded repository change, leave the
app, and later understand what happened or answer a specific blocker. A chat
transcript alone cannot establish ownership, queue acceptance, worker state,
spend, approval authority, or completion.

This candidate narrows the product to one authenticated owner, one canonical
Chat/Voice conversation, explicit authorized repository work, and durable
structured evidence. It does **not** claim that the full personal execution loop
is activated: runtime hard-limit and producer-binding gaps remain open.

## Architecture and decisions

| Concern | Authority and implementation | Deliberate tradeoff |
|---|---|---|
| Conversation | `magi_chat_service.py`, `magi_chat_store.py`; provider-native APIs/events | Harness history and terminal text cannot become user-message authority. |
| Identity | Normal verified session plus exact `DeploymentMode` owner check | Personal means no commercial entitlement, not anonymous access or a user-ID prefix bypass. |
| Repository work | `ObjectiveSubmissionStore.claim`, owner-bound GitHub App/project repository | A project name or arbitrary repository URL is not permission. |
| Resource admission | `PersonalResourceLedger` in `firstmate_execution.py`, one DB transaction with the claim | Full micro-USD ceilings and at most two reserved worker slots; unknown work retains capacity. |
| Execution | Existing pinned Firstmate adapter; no alternative default runtime | The pin lacks enforceable worker/cost envelopes, so personal dispatch fails closed before publication. |
| Progress | Persisted execution/decision rows; write-side monotonic objective projection | Submitted, queued, accepted, running, waiting, recovering, unknown and terminal are different facts. Cancellation requested is not cancellation observed. |
| Decisions | Optional producer run/request/scope/expiry binding plus revision and current repository checks | Notifications navigate only. Gateway and Attention use exact canonical owner-message bytes and confirmation; the UI retains only the bound returned outcome. Old personal holds cannot authorize. |
| Recovery | Frozen identity/context/resource provenance and measured terminal settlement | A timeout is not proof of non-publication. Do not release an uncertain hold or reinterpret it as commercial credit. |
| Mobile experience | Expo shell; shared Native Magi Chat/Voice record; native push receipts | Foreground voice and provider receipts are implemented surfaces, not proof of background listening or phone delivery. |

The integration preserves the routing/idempotency and partial-dispatch ledger
safeguards of https://github.com/calvin-rutherford/Magistrate/pull/123. There is
no new execution framework or separate personal-runtime module. Existing
commercial history and historical conversation/Pi tables are retained.

## Engineering work demonstrated

- Composed three separate review branches without copying their UI/native
  implementations or changing their ownership boundaries; no approval or landing
  of those branches is implied.
- Put admission and frozen provenance in durable storage rather than prompts;
  retained reservations through uncertainty and recovered settlement from
  immutable measured terminal events.
- Kept source identity, request revision, expiration, current authorization,
  canonical answer bytes and one-use confirmation separate.
- Added narrowly scoped, content-free SQLite diagnostics that cannot initialize
  a missing database or import Gateway startup.
- Tested hostile provider/quoted-content execution attempts, concurrent capacity,
  auth revocation, mode changes, repository revocation, decision replay/expiry,
  memory immutability, and notification evidence using disposable fixtures.

## Evidence and limits

[Acceptance](portfolio-acceptance-report.md) records exact commands and results.
Automated positive execution uses a substituted external dispatch boundary and
producer fixtures; it proves Gateway contracts, not a real worker rollout.
Personal refusal is itself tested at the real admission edge. No live objective,
current-candidate deployment, signed IPA, or physical-device result was produced
by this integration.

The strongest design choice is the visible closed gate: admission accounting is
not runtime enforcement. Shipping a configuration switch labelled “two workers”
would not make Firstmate obey it. A reviewed runtime contract must carry and
enforce the immutable objective envelope, report measured terminal usage, and
produce bound decisions before the personal end-to-end demo can pass.

## Deferred scope

Multi-tenant SaaS/hosted activation, marketplace integrations, employer-system
access, direct Codex/OpenCode execution, whole-fleet supervision, AR/neural devices,
cloned voice, App Store release, and always-on/background voice remain outside
this bounded candidate. Existing SaaS/hosted and perception code is not evidence
of personal activation or external infrastructure/device isolation. Remaining
advisories are not waived for portfolio presentation.

## Attribution

Magistrate supplies the application composition, policy and evidence surfaces;
it does not claim to implement the underlying models or execution harnesses.
[Firstmate](https://github.com/calvin-rutherford/firstmate) supplies the pinned
execution/producer bridge; the repository lock is the exact source/revision
attribution. [Expo](https://expo.dev/), React Native and React provide the client
framework; FastAPI/Pydantic and SQLite/PostgreSQL provide API, validation and
persistence foundations. GitHub App APIs supply repository access, and Expo Push
with Apple APNs/Google FCM supplies native delivery infrastructure. OpenAI and
other configured model/STT providers retain their own credentials, terms, quotas
and capabilities; the alternative-runtime evaluation does not grant provider
entitlement. Dependencies and licenses remain in their manifests/lockfiles.
Foundation, UI and native contributions are credited by exact review inputs in
the acceptance report; this lane owns composition, diagnostics and this package.
