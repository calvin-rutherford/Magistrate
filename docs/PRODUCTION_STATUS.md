# Production status — integration and release authority

**Production verdict: BLOCKED_EXTERNAL.** Repository-controlled convergence is
**COMPLETE** on the release branch reconciled with main
`9abe699dce512be1c8995a3ca5b4c02788a9703e` (all preceding production workstreams).
There are no waived, skipped or unimplemented mandatory repository suites.
This is **not** activated production, an accepted live RC, a signed EAS/TestFlight
build or App Store approval. Real service/device/legal/account evidence remains
mandatory and unobserved by this work.

This is the single production matrix. The eight companion uppercase documents
own architecture/activation requirements; component runbooks explain subsystem
contracts. Earlier baseline audits and historical rollout claims cannot override
current code or certify a new candidate.

- **COMPLETE**: the specifically bounded repository contract exists and its
  executable acceptance passes; external activation is a separate row.
- **BLOCKED_EXTERNAL**: a specifically named account/service/device/legal-owner
  action cannot be completed by repository edits.
- **FAILED**: missing repository implementation, failed tests or unresolved
  integration. Never relabel this as an external dependency or waive its suite.

## Production matrix

Test IDs are executable in `scripts/production_acceptance.py`. Source/test
citations are boundaries, not substitutes for a hash-bound candidate receipt.

| Capability | Current implementation | Production requirement | Owner | Dependencies | Tests / entrypoint | Acceptance evidence | Status |
|---|---|---|---|---|---|---|---|
| Release contracts | 24 required suites, hashed clean-candidate receipts and separate external checkpoints | Refuse missing/dirty/stale/failed/duplicate/wrong-class evidence | A12 release | Domain gates and independent review | `release-foundation`, `client-contract` | Admission negatives; actual-validator schema export | COMPLETE |
| Native human Chat / Voice | Canonical owner thread, `/api/v1/magi/*`, `magi_messages` | Preserve IDs/revisions/exact bytes; no Pi/terminal/worker fallback | A7 / A12 | Auth, persistence | `unit`, `magi-context-routing`, `client-contract` | Complete-message, route-retirement, idempotency and owner tests | COMPLETE |
| Identity / personal tenancy / Projects | One personal workspace, durable owner-qualified projects/repository metadata | No foreign principal data; no invented enterprise membership | A1 / A11 | Provider identity, persistence | `tenant-authorization`, `postgres-persistence` | Two-tenant CRUD, guessed IDs, selective deletion and DB rollback | COMPLETE |
| Provider auth / recovery / onboarding | Verified Apple/Google, platform channel, refresh rotation/linking; welcome/name/GitHub/signed subscription | Free-account initialization or Checkout return must not bypass subscription gate | A2 / A4 / A12 | Login identity, OAuth and signed billing facts | `provider-auth`, `spencer-hermetic` | Signed synthetic assertions and composed checkout/cancellation regression | COMPLETE |
| Apple / Google activation | Console/native/web evidence not supplied | Actual audiences/redirects/team/clients, real login/recovery | Provider account owners | Activation sections 2–3 | `activation/provider-consoles`, Spencer | Real login and device records, not fixture signatures | BLOCKED_EXTERNAL |
| GitHub repository plane | Owner-bound App installations, scoped read tokens, signed idempotent webhooks; global CLI service removed | Current selected repositories only; replay/revoke/foreign owner refusal | A3 / A1 | OAuth identity distinct from repository App | `github`, `tenant-authorization` | Private-repository, callback, token and lifecycle negative tests | COMPLETE |
| GitHub / broker activation | Real App/installations/broker permissions not observed | Exact callbacks, selected repos, approved read/write scope and short-lived broker token | GitHub/security owners | Activation section 4, hosted backend | `activation/provider-consoles`, `isolated-workers`, Spencer | Console IDs, actual revocation/scope proof and forge artifact | BLOCKED_EXTERNAL |
| Billing / credits / entitlement | Catalog, signed webhooks, integer ledger/reservation/measured settlement | Atomic budget/concurrency denial before intake; idempotent paid state | A4 / A5 / A12 | Principal, measured execution use | `billing-ledger`, `execution-recovery-isolation`, `migrations` | Replay/order/conflict, limits, settlement/refund and restore tests | COMPLETE |
| Stripe activation | Catalog intentionally has no live price IDs | Approved merchant/pricing/mode, webhook/portal and real reconciliation | Merchant/legal owners | Activation section 5 | `activation/stripe-reconciliation`, Spencer | Test-mode event IDs and ledger review; no live charge inferred | BLOCKED_EXTERNAL |
| Durable local execution bridge | Deterministic task/intake/wake, structured events/decisions/evidence | Firstmate owns lifecycle; reads never probe/control workers | A5 | Pinned producer and DB | `execution-recovery-isolation` | Replay/crash/read tripwires and pinned producer fixture | COMPLETE |
| Hosted execution contract | Durable controller/leases, mTLS backend, objective-bound bearer, scoped broker, measured terminal facts | Closed recovery/cancellation/cleanup; no shared local fallback | A5 / A11 | PostgreSQL, backend/image/mTLS/broker | `execution-recovery-isolation`, `postgres-persistence` | Synthetic backend, lease/recovery, cross-tenant and cleanup refusal tests | COMPLETE |
| Enforced live worker isolation/autonomy | Interface implemented; backend/image/control enforcement unobserved | Hostile tenant filesystem/process/credential/egress/resource isolation; client-independent recovery | Infrastructure/security owners | Activation section 7 | `activation/isolated-workers`, `moat/background-autonomy` | Actual controlled failure and isolation evidence, not mocked receipts | BLOCKED_EXTERNAL |
| Persistent context | Scoped revisions/index/audit/tombstones and frozen objective context | Bounded authorized provider/harness-independent retrieval | A6 | Projects, files, structured ledgers | `magi-context-routing`, `moat-hermetic`, `backup-restore` | Scope/replacement/deletion/frozen-byte and restore tests | COMPLETE |
| Model/cost/harness routing | Native OpenAI/Anthropic/Google, capability/price/budget/failover policy; separate harness strategy | No unknown-as-zero, no fallback after tool call, request-bound high-impact confirmation | A7 / A4 | Operator catalog/credentials; execution ledger distinct | `magi-context-routing`, `unit`, `moat-hermetic` | Native wire adapters, policy/budget/usage and replacement seams | COMPLETE |
| Live provider/harness/context/cost moat | Required checks registered, no real replacements performed | Actual replacement, context provenance, budget/usage reconciliation | Model/runtime/release owners | Configured providers and isolated workers | `moat` | Seven independently reviewed live/device artifacts | BLOCKED_EXTERNAL |
| Product hierarchy | Restrained Chat / Projects / Fleet / Activity / Attention, overlays and truthful states | Canonical UI, no synthetic runtime completion or target selector | A8 / A9 | Merged domain contracts | Full `frontend` | Browser/state/type/lint/export and UI contract tests | COMPLETE |
| Private files / perception | Private POSIX objects, sniffed MIME/digest/quota/retention/signed access; non-executing consent drafts | Owner-qualified safe bytes and no device-originated execution authority | A10 / A11 | Persistent shared volume, scoped context | `uploads`, `tenant-authorization`, `backup-restore` | Spoof/traversal/quota/expiry/scanner/protocol/restore tests | COMPLETE |
| Storage activation / recovery | POSIX adapter and SQLite drill; managed state unobserved | Provision encrypted private/shared volume, scanner if enabled, coordinated backup/key restore | Storage/DB/security owners | Activation section 6 | `activation/persistent-restore` | Actual bytes/rows/secret versions/ownership and measured recovery | BLOCKED_EXTERNAL |
| Foreground voice / iOS entry code | Capture → STT → same thread, safe background stop, foreground-only App Intents | Permission/interruption-safe foreground UX; no continuous listening claim | A9 | Native build, STT key | `voice`, `frontend` | State machines, mocked STT and browser mic/shortcut tests | COMPLETE |
| Push receipt reconciliation / links | Durable owner/fingerprint/token-hash tickets, delayed leased polling/backoff/expiry, exact token retirement | Receipt is provider handoff only; preserve unread/fallback and no duplicate authority | A9 / A12 | Expo send/receipt endpoints; migration 9 | `push-deep-links`, `migrations`, `postgres-persistence` | Restart/dedupe/outage/expiry/late-token/foreign-owner and erasure tests | COMPLETE |
| Physical voice / push / links / device independence | Repository seams implemented; no exact candidate device run | Real APNs/FCM ticket+receipt, cold/warm tap, audio route/interruption and second device | Apple/Expo/device owner | Signed candidate and configured services | Spencer, `distribution/testflight-candidate` | iPhone/iOS/network/build records and DEAT-001 | BLOCKED_EXTERNAL |
| Composed migrations / SQLite restore | Ordered versions 1–9; populated pre-domain upgrade and full-table backup/restore plus object snapshot | Preserve legacy/native rows, credits/context/evidence/secrets/push; repeatable init | A12 / schema owners | Final schema and matching key | `migrations`, `backup-restore` | Real operator SQLite backup/restore code, exact rows/FKs/digests, orphan recovery | COMPLETE |
| PostgreSQL multi-instance persistence | Shared adapter and transaction/migration locks | Concurrent startup, owner isolation/erasure and durable hosted/receipt state | A1 / A12 | Disposable Docker PostgreSQL 16 for test | `postgres-persistence` | Two real concurrent fixture processes; never a production DSN | COMPLETE |
| Production DB / DNS / monitoring | Preflight, service/edge/alert templates, content-free telemetry and private metrics | Actual provisioning/TLS/WSS/private scrape, alert delivery and isolated PostgreSQL restore | Infrastructure/on-call owners | Activation sections 6–8 | `activation/persistent-restore`, `edge-monitoring` | Public authenticated smoke, actual restore and alert record | BLOCKED_EXTERNAL |
| Security / dependency remediation | Byte/time/in-flight caps, secret/URL controls, lab quarantine; compatible patched dependency call sites | No remaining known advisory finding or unreviewed parser substitution | A11 / A12 | Frozen deps and isolated CI/build credentials | `security-boundaries`, `release-configuration`, CI advisory checks | Zero current npm/Python findings; hostile parser and real Metro/query regressions | COMPLETE |
| Deployment / CI / EAS repository config | Manual-only guarded deploy, strict readiness, fail-closed EAS production preflight | No auto deploy/rollback; exact reviewed candidate and required checks | A12 | Reviewed service environment and forge rulesets | `deployment-smoke`, `release-configuration`, full CI | Hermetic refusal/backup/smoke/build-config tests; no host lifecycle action | COMPLETE |
| EAS / store / legal activation | Committed bundle/Expo linkage; actual `ascAppId` and approvals absent | Owner accounts/legal URLs/team, exact signed archive/TestFlight and submission approval | Expo/Apple/legal owners | Activation section 9 | `distribution`, `activation/legal-security` | Actual console/build/submission IDs, device records and reviewer | BLOCKED_EXTERNAL |
| Spencer synthetic composition | Fresh verified-identity fixture, OAuth/Checkout/signed subscription, project/memory/file/chat, second session and erasure | No preseeded name or fabricated paid state; explicit fake external edges | A12 | Real stores/routes and mocked HTTP/model/identity | `spencer-hermetic` | Cross-domain checkout bypass/cancellation regression and owner/continuity checks | COMPLETE |
| Spencer real new-user path | All ten checkpoints mandatory; no actual new-user service/device evidence | No bootstrap/SSH/runner config; real onboarding through retirement | Release operator + Spencer | All activated domains and exact candidate | `spencer-new-user` | Per-checkpoint hashed live/device artifacts; synthetic journey cannot substitute | BLOCKED_EXTERNAL |
| Retired/non-core surfaces | AR route/client removed; root scripts refuse production; Django/Compose lab-only; Pi regression adapter retained | No legacy execution/conversation/rollback loophole; unavailable integrations truthful | A11 / A12 | Scope controls | `integration`, `backend`, `pi-extension`, `deployment-smoke` | Regression and retirement negatives; no external non-core activation claim | COMPLETE |

## Executable inventory and composition authority

- `gateway/app` is product identity/API/state/routing/execution/observation;
  `gateway/scripts` owns provisioning/preflight/storage/schema export and
  PostgreSQL test entrypoints. `db.py` migration order is authoritative; all stores
  use `persistence.py`. New migration **9** adds `notification_push_deliveries`.
- `frontend/app`, `src`, `native`, `plugins`, `scripts`, `tests` are Expo web/iPhone
  product, shared auth/cache/voice/intents, release config and browser gates.
  `frontend/patches` contains narrow Metro/query-string compatibility bridges,
  applied by `npm ci`, not parser forks or an Expo downgrade.
- `runtime` contains the pinned external Firstmate contract and reviewed service,
  edge and alert **templates**, not proof of provisioned services. The release
  runner never invokes live deploy/smoke, paid providers or worker lifecycle.
- `backend` Django/Channels/Celery, `cli`, root launch/setup/rsync/Compose and
  opt-in live scripts are retained/lab/retired surfaces, not the Native Magi SaaS
  authority. Backend PostgreSQL is distinct from the Gateway PostgreSQL adapter.
  `pi-extension` is regression-tested, never a human-conversation transport.

All preceding workstreams are merged; the early foundation merge order is no
longer an open dependency. Final composition preserves the complete product,
keeps AR retirement, binds auth onboarding to signed subscription identity,
adds durable receipt migration/recovery/erasure and repairs dependency consumer
compatibility. Future schema/API changes must update owner-negative tests,
exported validators, ordered migrations, restore fixtures and this registry.
Only merge authority merges; this integration branch does not deploy or merge.

## Reproduce the exact gates

Use Python 3.12, Node 22 (SDK 57 requires >=22.13), Chrome, Docker for the isolated
PostgreSQL fixture, `uv sync --frozen` in Gateway and `npm ci` in frontend/Pi.
The retained backend requires `backend/requirements.txt` in the Python environment
used by the runner. Build Expo web before Gateway SPA tests. Install/verify the
pinned producer **inside a disposable fixture root**, never a supervising home,
and set `MAGISTRATE_TEST_PINNED_FIRSTMATE_ROOT` to it; no activation is performed.

```sh
mkdir -p .release
python3 scripts/production_acceptance.py check
python3 scripts/production_acceptance.py list
python3 scripts/production_acceptance.py run --output .release/all-suites.json
(cd gateway && uv run python -m scripts.export_client_protocol) > .release/client-protocol.json
python3 scripts/production_acceptance.py template --revision "$(git rev-parse HEAD)" --output .release/rc.json
python3 scripts/production_acceptance.py verify .release/rc.json --revision "$(git rev-parse HEAD)"
```

The last command **must fail** for an unfilled external template. Record real
artifacts/attestations before admission. Output files never overwrite earlier
receipts; use unique names for reruns. `--suite ID` supports focused diagnosis but
never replaces the mandatory **full** frontend `npm test` or all-suite admission.
POSIX tests use short `/tmp` fixture paths for AF_UNIX/Chrome and grant-output
contracts. Missing dependencies/timeouts fail rather than skip. PostgreSQL tests
create only a new loopback-bound container, ignore deployment environment/DSNs,
and clean up only that owned container.

Receipts contain revision, registry digest, timestamps, exit codes and cleanliness,
not test output/environment/content. An uncommitted pass is diagnosis, not clean
RC evidence. CI workflows remain release gates and assert generated-file hygiene.
Online advisory scans in Production foundation are separate from hermetic receipts:
`npm audit` for frontend/Pi and pinned `pip-audit 2.10.1` for Gateway/backend.

## Current evidence and remaining acceptance

Local reconciliation exercised all **24 suites**; updated Gateway full regression
is **473 passed**, release admission **17 passed**, backend **16 passed**, Pi
adapter **8 passed** plus typecheck. Patched frontend typegen/typecheck/lint,
**full `npm test` including browser suites**, and web export pass (15 existing
lint warnings, no errors). Disposable concurrent PostgreSQL/isolation/erasure
and deployment workflow/deploy/restricted-beta smoke contract scripts pass.
Framework deprecation warnings remain non-failing. Current frontend/Pi npm audits
and Gateway/backend Python scans report **zero known vulnerabilities**.

Candidate receipts and forge CI must be rerun from the clean committed candidate;
local diagnostic output is not a live-service or physical-device receipt. No
external acceptance is fabricated. Exact owners/console fields/callbacks/env
names/verification are in [PRODUCTION_ACTIVATION.md](PRODUCTION_ACTIVATION.md).
Spencer's ten checkpoints, all seven moat checkpoints, activation and distribution
remain BLOCKED_EXTERNAL until their actual per-checkpoint evidence is reviewed.
A green repository is not permission to call production activated.
