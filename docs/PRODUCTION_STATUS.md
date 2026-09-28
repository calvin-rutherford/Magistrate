# Production status — integration authority

**Production verdict: FAILED.** This is the early A12 release-foundation baseline
on main `8249f49`, not a declaration that the concurrent production workstreams
have merged. Repository gaps below must be fixed; they are not external blockers.
No live provider, payment, deployment, EAS build, TestFlight, or physical device
was exercised by this foundation change.

This document is the single production matrix. The eight companion uppercase
production documents define requirements and activation. Older phase/beta/live
runbooks remain useful historical evidence, but cannot override the current
executable Native Magi boundary or count as evidence for a new candidate.

Status vocabulary:

- **COMPLETE**: the specifically bounded repository contract exists and has
  executable acceptance coverage; this does not imply external activation.
- **BLOCKED_EXTERNAL**: the separately identified owner/service/device action
  cannot be completed by repository changes.
- **FAILED**: missing implementation, failed acceptance, or incomplete merged
  integration. Never relabel this as an external dependency or skip its suite.

## Production matrix

Test IDs below are executable through `scripts/production_acceptance.py`.
“Evidence” states the acceptance boundary, not an unobserved service result.
A source/test citation alone is not an RC receipt.

| Capability | Current implementation | Production requirement | Owner | Dependencies | Tests / entrypoint | Acceptance evidence | Status |
|---|---|---|---|---|---|---|---|
| Release contracts | Versioned schema export, suite registry, hashed candidate receipts and external-check admission | Reject missing, dirty/stale, failed, duplicate or wrong-class evidence | A12 | All owners reconcile registry | `release-foundation`, `client-contract` | Validator negative tests; generated schemas derive from real validators | COMPLETE |
| Human Chat / Voice transport | `magi_chat_{api,service,store}.py`; one principal-owned canonical thread | Only `/api/v1/magi/*` and `magi_messages`; closed model tool routing | A7 / A12 | Auth, persistent DB | `unit`, `magi-context-routing`, `client-contract` | Route-retirement, exact-byte/idempotency/context isolation tests | COMPLETE |
| SaaS identity / tenancy / Projects | Principal-scoped rows; shared operator runtime; no complete organization/project membership plane | Tenant membership, project/repository authorization and tenant runtime ownership everywhere | A1 / A11 | Schema and auth composition | `tenant-authorization`, `integration` | Two-tenant matrix across every new route; current tests prove only existing principal scopes | FAILED |
| Apple / Google protocol | Verified assertions, nonce/audience/replay, native rotation, web HttpOnly cookie | Preserve fail-closed platform channel and revocation | A2 | Identity | `provider-auth` | Signed synthetic assertions and refresh-family tests | COMPLETE |
| Apple / Google activation | No candidate console/device evidence | Real account IDs, registered callbacks, real native/web login | Account owners / A2 | [Activation](PRODUCTION_ACTIVATION.md) | `activation/provider-consoles`, Spencer | Redacted console IDs and real login records | BLOCKED_EXTERNAL |
| GitHub SaaS integration | OAuth adapter; PR reads use deployment `gh-axi` repository/service identity | Tenant repository access/installation ownership, scoped credentials, revoked installation refusal | A3 / A1 | Tenant membership and GitHub account | `github`, `tenant-authorization` | Per-installation private-repository negative tests and live scope test | FAILED |
| GitHub activation | OAuth configuration names exist; no new-candidate installation evidence | Approved organization/app/repositories and callback/permission evidence | GitHub owner / A3 | Merged A3 contract | `activation/provider-consoles` | Generated app/installation IDs, allowed-repository access, revocation | BLOCKED_EXTERNAL |
| Stripe / ledger / entitlement | No implementation in baseline; `usage.py` is quota display only | Durable idempotent signed webhooks, money/credit ledger, budget/reservation/settlement and reconciliation | A4 | A1 identity, A7 usage | `billing-ledger` fails explicitly until registered | Duplicate/out-of-order webhook, insufficient funds, refund and reconciliation suite | FAILED |
| Stripe activation | No account/product/price/webhook IDs observed | Approved legal merchant, test/live separation and secrets | Merchant owner / A4 | Merged billing routes/config | `activation/stripe-reconciliation` | Owner-reviewed console IDs and Stripe test-mode reconciliation | BLOCKED_EXTERNAL |
| Durable execution bridge | Deterministic objective queue/wake, persisted events/evidence/decisions/cancellation | Firstmate remains scheduler; reads never drive lifecycle | A5 | Pinned producer, DB | `execution-recovery-isolation`, `moat-hermetic` | Concurrent/replay/crash tests and runtime-read tripwires | COMPLETE |
| Multi-tenant execution isolation | Shared operator Firstmate and command authority | Per-tenant credentials, filesystem/network/resource boundaries and crash-safe admission | A5 / A11 | A1, A4, A7 | `tenant-authorization`, `execution-recovery-isolation` | Cross-tenant runtime denials and isolated worker recovery | FAILED |
| Live autonomous workers | Pinned producer contract; no candidate live run | Progress with all clients closed, recovery and verified completion | Runtime owner / A5 | Isolated runtime activation | `moat/background-autonomy`, `activation/isolated-workers` | Structured causality and evidence from authorized real run | BLOCKED_EXTERNAL |
| Context plane | Last 40 eligible messages, profile, bounded deployment/decision context; opaque objective references | Durable authorized project facts, provenance, retrieval, deletion and replacement continuity | A6 | A1, A10, A7 | `magi-context-routing`, `moat-hermetic` | Retrieval/ACL/deletion tests plus live continuity | FAILED |
| Cost/model/harness routing | One concrete OpenAI adapter; execution inventory/preferences separate | Policy-bound provider replacement, cost/quality routing, budget admission, observed usage | A7 / A4 | Context, ledger, execution | `magi-context-routing`, `unit` | Route policy/budget tests; real provider/harness replacement | FAILED |
| Product hierarchy | Chat and drawer Fleet/Activity/Attention; existing map/PR/account routes | Coherent Chat / Projects / Fleet / Activity / Attention, new-user empty/error states | A8 | A1/A3/A4/A5/A6/A7/A10 contracts | `frontend`, Spencer onboarding | Full browser suite and new physical journey | FAILED |
| Scoped file storage | Bounded chat uploads and owner-qualified downloads, explicit stored/attached state | Preserve these contracts through storage changes | A10 | Auth, DB | `uploads` | Size/type/ownership/idempotency tests | COMPLETE |
| Production file lifecycle | Local files; avatars in checkout-mounted public storage; no complete retention/scan/quota/backup plane | Durable private storage, bounded avatar handling, safe content access and deletion | A10 / A11 | Context, storage operator | `uploads`, `backup-restore` | Restart/restore/download/tenant/refusal tests and live storage proof | FAILED |
| Foreground voice adapter | Capture → server STT → same Magi thread; local TTS; background stop | Permission, interruption and truthful no-audio/no-provider handling | A9 | Model key, native build | `voice`, `frontend` | Synthetic STT and browser/state-machine tests | COMPLETE |
| Native voice / push / links | Native packages and notification registration/dedupe; no candidate device evidence | Mic/audio-route tests, real push receipt, authenticated cold/warm deep link | Apple/Expo owner / A9 | APNs/FCM, signed build | `spencer-new-user/upload-voice`, `push-deep-link` | Exact device/build/network records; ticket alone insufficient | BLOCKED_EXTERNAL |
| Push receipt reconciliation | Expo send response tracked; production receipt lifecycle still needs convergence | Reconcile provider receipts, expire invalid tokens, preserve Attention fallback | A9 | Push provider | `push-deep-links`, `integration` | Receipt failure/retry/token retirement tests | FAILED |
| Migration/restore foundation | Additive SQLite init; online deploy backup; new whole-table restore fixture | Preserve native/legacy rows, objectives/events/decisions/evidence, encrypted secrets | A12 / schema owners | All final migrations | `migrations`, `backup-restore` | Integrity/FK checks, exact rows, unchanged backup hash, orphan recovery | COMPLETE |
| Composed production migration | Other workstreams not in this baseline | Seed each new domain; old DB → merged schema → restart → isolated restore | A12 + A1/A4/A6/A10 | Merged schema owners | `migrations`, `integration` | Final merged-candidate rehearsal, not a fresh DB only | FAILED |
| Deployment safeguards / CI | Manual-only demo deploy, guarded fast-forward/backup/smoke; release CI added | No automatic deployment; full domain checks and clean receipts | A12 | Existing workflows | `deployment-smoke`, `release-foundation` | Hermetic refusal/restore/smoke contracts; no host restart performed | COMPLETE |
| Production host / DB / DNS / monitoring | Operator configuration required; legacy Docker/Django is not the SaaS Gateway deployment | Persistent state, TLS/WSS, redaction/limits/alerts, isolated recovery | Infrastructure owner / A11 / A12 | [Activation](PRODUCTION_ACTIVATION.md) | `activation/persistent-restore`, `edge-monitoring` | Public authenticated smoke and operator recovery record | BLOCKED_EXTERNAL |
| EAS / store / legal | Linked committed Expo owner/project and bundle; production `ascAppId` absent | Exact signed candidate, TestFlight/device proof, approved legal identity/URLs/store answers | Expo/Apple/legal owners / A12 | Production endpoint and permissions | `distribution`, production preflight | EAS/build/TestFlight IDs, archive and DEAT-001 evidence | BLOCKED_EXTERNAL |
| Spencer synthetic path | New identity mapping/name onboarding, canonical chat, second-session continuity and logout | Mandatory reproducible synthetic integration floor | A12 | Auth and Magi contracts | `spencer-hermetic` | Fake identity-verifier/model edges explicitly labeled; real stores/routes | COMPLETE |
| Spencer production path / moat | Required checkpoints registered; repository gaps above prevent complete journey | Every Spencer and all seven moat checkpoints; no waivers/skips | A12 + all domain owners | All FAILED implementations, then external evidence | `spencer-new-user`, `moat` | Human-reviewed, hash-bound per-checkpoint artifacts for exact candidate | FAILED |
| Non-core integrations / AR | Google integration OAuth, Twitter/Discord unavailable; Jira/Teams deferred; AR now refuses unimplemented dispatch instead of fabricating success | Keep unavailable integrations truthful; Google login remains separate; AR is not a second execution/chat route | A8 / A11 / A12 | Native Magi boundary | `integration`, provider truthfulness and AR refusal tests | No fake records, provider connection or AR dispatch receipt | COMPLETE |
| Retained executable surfaces | Django/Celery, CLI, Pi adapter, old deploy/bootstrap scripts retained | Regression coverage and explicit exclusion from production Chat; no unreviewed activation | A12 / A11 | Scope review | `backend`, `pi-extension`, `integration` | Existing tests; CLI/scripts inventoried, not declared SaaS-safe | COMPLETE |

## Executable inventory and ownership

- `gateway/app`: auth/provider integrations; native chat/model/tools; objective
  intake, decisions, events, cancellation; structured Fleet/Activity/Attention;
  files/STT/notifications; execution inventory; explicit non-chat controls.
  `gateway/scripts`: access provisioning, secret rotation, reliability probes,
  and the deployment-isolated shared schema export.
- `frontend/app`, `src`, `scripts`, `tests`: Expo web/native UI, auth/cache/socket,
  voice, notifications, capability preferences, release preflight and browser
  suites. `frontend/app.json`, `app.config.ts`, `eas.json` own native build config.
- `backend`: independent Django/Channels/Celery models, migrations, services and
  tests. Its Postgres is **not** the Gateway SQLite database. `cli`, root launch
  scripts, `Dockerfile`, `docker-compose.yml`, setup/push/pull scripts and
  `tests/e2e_live_test.py` are retained executable/legacy or opt-in live surfaces,
  not production entrypoints. The compose Docker socket mount is not isolation.
- `pi-extension`: retained native lifecycle adapter, not human Chat.
  `runtime/firstmate-producer.lock.json` and `scripts/install_firstmate_producer.sh`
  pin the external producer. Installation verification is not a live worker test.
- `.github/workflows`: actual regression/deploy gates. A12's release registry
  composes, not replaces, these commands. No test runner invokes live smoke,
  model-reliability probes, deployment or worker lifecycle commands.

## Merge order and conflict ownership

Land the A12 foundation first. Merge authority alone merges; A12 does not.
Then: A11 guardrails alongside A1 identity/schema; A2 auth and A3 GitHub against
that identity; A4 ledger before A7 charged routing; A10 files and A6 context;
A5 isolated execution plus A7 model policy; A8 UI and A9 voice/push consume those
contracts. Finish with A11 cross-boundary negatives and A12 reconciliation.
Independent changes may merge earlier only with compatible additive contracts.

Every owner must report changed routes, tables/indexes, principal semantics,
configuration names, tests and migration order. A12 arbitrates `db.py`, common
request/event names, startup ordering and shared CI, not pricing/legal choices.
Do not copy another lane's unmerged files, invent its API, or silently weaken a
gate to make this baseline green. Missing schema/contract functionality remains
FAILED until its implementation and tests merge.

## Acceptance entrypoints

```sh
python3 scripts/production_acceptance.py check
python3 scripts/production_acceptance.py list
mkdir -p .release
python3 scripts/production_acceptance.py run --suite release-foundation --output .release/foundation.json
# All suites, including intentionally failing unimplemented domains:
python3 scripts/production_acceptance.py run --output .release/all-suites.json
python3 scripts/production_acceptance.py template --revision "$(git rev-parse HEAD)" --output .release/rc.json
python3 scripts/production_acceptance.py verify .release/rc.json --revision "$(git rev-parse HEAD)"
```

Install dependencies first: `cd gateway && uv sync`; `npm ci` separately in
`frontend` and `pi-extension`; a Python 3.12 environment with
`pip install -r backend/requirements.txt` for the backend suite. Gateway full
integration additionally needs the web export and the pinned producer fixture
as configured in `.github/workflows/gateway.yml`. Use Node 22 and Chrome for the
full frontend suite; `npm test`, not selected browser substitutes, is its gate.

Receipts record commands' exits, timestamps, candidate SHA, registry digest and
cleanliness, never test output or environment values. The POSIX runner uses short
`/tmp` test fixtures: deep worktree paths break AF_UNIX/Chrome sockets, and grant
CLI tests correctly refuse output inside a release checkout. An uncommitted local pass
cannot be RC evidence. Missing dependencies/timeouts fail rather than skip.
`billing-ledger` deliberately fails without spawning anything until A4's actual
suite replaces the gap. Existing tenant/context suites are a baseline, not proof
of unimplemented SaaS requirements. Register the merged owners' tests as well.

For external checkpoints, put redacted artifacts beside the packet and record
relative path plus SHA-256. All statuses must become COMPLETE, with named human
attestation and the correct live-service/physical-device/operator-review class.
The validator checks structure, hashes and completeness, **not whether an
operator's assertion is true**. Release authority must inspect the artifacts.
Never put bearer tokens, private keys, customer content or raw prompts in Git or
public Actions artifacts. Keep the actual packet in restricted release storage.

## Foundation validation evidence

Local commands on this foundation change completed: Gateway full suite **359
passed** (including the pinned producer fixture, new journeys/restore/export and
AR truthful refusal); release admission **15 passed**; backend **16 passed**;
retained Pi adapter **8 passed** plus typecheck; frontend typegen/typecheck/lint,
**full `npm test` including browser suites**, and web export; all three deployment
contract scripts. Frontend lint has 18 existing warnings and no errors. Gateway
reports existing framework deprecation warnings. Temporary-path failures in the
first local run were resolved by the approved short `/tmp` test fixtures.

These are local hermetic results, not clean-candidate RC receipts or live/device
acceptance. CI must rerun the candidate on the forge. No Stripe implementation,
external-service acceptance, EAS build or physical test is claimed by this record.

## Follow-up reconciliation gate

After merged workstreams: replace the missing billing entrypoint, expand tenant,
context/routing, file, push and migration fixtures; rerun all CI commands on the
merged SHA; regenerate shared schemas; update every matrix row from evidence;
then activate external accounts and execute Spencer/moat/DEAT-001. Only that
follow-up may call a production RC accepted. This early PR is not that RC.
