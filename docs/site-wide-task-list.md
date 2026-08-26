# Magistrate site-wide task list

This task list converts the findings in [`site-wide-audit.md`](site-wide-audit.md) into independently delegable work. Priority combines impact, exploitation or regression risk, and implementation effort. Packages are deliberately non-overlapping: a package owns its named surfaces and tests, while shared-file edits are sequenced through the dependencies below.

## Delivery rules and active-work boundaries

- Merge or rebase open PRs before editing their owned hotspots: PR #3 owns ambient backgrounds and Voice Mode behavior, PR #4 owns terminal history/polling/scrolling, and PR #5 owns GitHub/Home PR data, navigation, caching, and related attention integration. Merged notification behavior is baseline code, not pending work.
- Do not duplicate implementation or tests already present in those PRs. After each merge, run package `VAL-01` and adjust the remaining packages against the merged source.
- A package may be delegated by itself once its listed dependencies are satisfied. Its owner must avoid opportunistic fixes from another package and must record any newly discovered cross-package issue instead.
- Commands are run from the repository root unless a command starts with `cd`. Where a package introduces a test selector or script that does not exist yet, creating that deterministic selector is part of the acceptance criteria.

## Priority map

| Order | Package | Impact / effort | Audit findings | Dependencies |
| --- | --- | --- | --- | --- |
| P0.1 | `SEC-AUTH` — replace the shipped shared-token boundary | Critical control, high effort | SEC-01, TST-01 | Rebase after PRs #4 and #5; coordinate shared gateway/client files with `REL-HTTP` |
| P0.2 | `SEC-OAUTH` — bind and constrain OAuth transactions | Critical control, medium effort | SEC-02, TST-01 | Rebase after PR #5; no dependency on `SEC-AUTH` if callback transaction lookup is isolated |
| P0.3 | `SEC-WS` — authenticate and authorize command sockets | Critical control, medium effort | SEC-03, TST-01 | None; keep backend-only |
| P0.4 | `SEC-SECRETS` — fail closed for credential encryption | Critical control, medium effort | SEC-04, TST-01 | None; keep gateway database/config-only |
| P1.1 | `REL-HTTP` — status-aware API failures and recoverable UI states | High user impact, medium effort | REL-01 | PRs #4 and #5 merged; `SEC-AUTH` contract settled |
| P1.2 | `A11Y-NAV` — semantic shared navigation controls | High reach, low effort | A11Y-01 | None; recheck Voice destination after PR #3 |
| P1.3 | `UPLOAD-BOUNDS` — bound and validate uploaded media | Resource/security risk, medium effort | SEC-05 | `SEC-AUTH` contract settled |
| P1.4 | `OPS-SAFE` — fail-closed production configuration | Deployment risk, medium effort | OPS-01 | `SEC-SECRETS` for key policy; deployment topology decision |
| P1.5 | `TEST-BASE` — reproducible backend and frontend test foundations | Regression risk, medium effort | TST-02, TST-03 | PRs #3–#5 merged so their tests are retained |
| P2.1 | `VOICE-CONSENT` — decide and enforce capture consent | Privacy/UX risk, low-to-medium effort | UX-02 | PR #3 merged |
| P2.2 | `WEB-ATTENTION` — decide supported web notification parity | Product consistency opportunity, variable effort | MWC-02 | PR #5 merged; product decision required |
| P2.3 | `REALTIME-LIFECYCLE` — make reconnect ownership explicit | Latent reliability risk, low effort | REL-02 | Only schedule before first production caller is added |
| P2.4 | `VAL-01` — active-PR post-merge validation | Regression prevention, low effort | UX-01, A11Y-02, PERF-01, PERF-02, MWC-01, TST-02 | Run after PRs #3–#5 merge; produces follow-ups only for residual defects |

## P0 security packages

### SEC-AUTH — replace the shipped shared-token boundary

**Evidence.** SEC-01: `frontend/src/api/client.ts` and `frontend/src/realtime/socket.ts` ship `magistrate-device-token-12345`; `gateway/app/auth.py` accepts that default through a header or query parameter; `gateway/app/main.py` allows wildcard CORS. TST-01 confirms the only negative gateway authorization test covers `/api/v1/health`.

**Boundary.** Own identity issuance/verification, authorization scopes, credential lifecycle, CORS policy, client credential transport, and endpoint-family authorization tests. Do not absorb OAuth callback transaction integrity (`SEC-OAUTH`), backend Channels authentication (`SEC-WS`), encryption-at-rest (`SEC-SECRETS`), or general response UX (`REL-HTTP`). PRs #4 and #5 both edit `frontend/src/api/client.ts` and `gateway/app/main.py`; start implementation only after rebasing onto both, or explicitly revalidate after each merge.

**Acceptance criteria.** Production clients contain no reusable server authorization secret. The gateway verifies a server-issued user/device identity, rejects missing, malformed, expired, revoked, and insufficiently scoped credentials, and scopes captain prompts, key input, provider, fleet, terminal, voice, notification, and account operations appropriately. Credentials can be rotated and revoked. Production startup has an explicit origin allowlist; a clearly signposted local-development mode may remain. Logs and error bodies expose no credentials. Automated tests cover successful access plus negative authorization for every sensitive endpoint family, satisfying SEC-01 and its TST-01 mapping.

**Verification commands.** Run:

```sh
rg -n "magistrate-device-token-12345|DEVICE_TOKEN|allow_origins=\[\"\*\"\]|token=" frontend gateway
cd gateway && PYTHONPATH=. uv run pytest -q
cd frontend && npx tsc --noEmit
```

The first search must return no production client secret, wildcard production origin, or credential-bearing WebSocket URL; test fixtures may use obviously synthetic credentials.

### SEC-OAUTH — bind and constrain OAuth transactions

**Evidence.** SEC-02: `connect_oauth_provider` in `gateway/app/main.py` constructs unsigned caller-controlled `user_id::redirect_uri` state, while the unauthenticated callback trusts both values and lacks a nonce, expiry, one-time record, or redirect allowlist. TST-01 confirms callback rejection paths are untested.

**Boundary.** Own provider-independent OAuth transaction creation, storage, validation, consumption, and redirect policy plus callback tests. Do not redesign gateway authentication or provider-specific UI. Rebase after PR #5 because it changes adjacent gateway/GitHub surfaces.

**Acceptance criteria.** Connect creates a cryptographically random, expiring, single-use server-side transaction bound to the authenticated principal, provider, and allowlisted application redirect. Callback validation occurs before exchange or persistence. Missing, malformed, unknown, expired, replayed, principal/provider-mismatched state and disallowed redirects are rejected without credential writes. Tests cover every rejection plus one successful callback and prove one-time consumption, satisfying SEC-02 and its TST-01 mapping.

**Verification commands.** Run:

```sh
rg -n "user_id.*redirect_uri|split\(.*::|state" gateway/app gateway/tests
cd gateway && PYTHONPATH=. uv run pytest -q gateway/tests/test_account_oauth_github_voice.py
cd gateway && PYTHONPATH=. uv run pytest -q
```

The search is a review aid: no callback may recover trusted identity or redirect values solely by splitting caller-provided state.

### SEC-WS — authenticate and authorize command sockets

**Evidence.** SEC-03: `backend/og_broker/asgi.py` installs no authentication middleware, `backend/agents/consumers.py` accepts every connection, and `MagistrateConsumer.receive` dispatches arbitrary command text through `ExecutiveService.launch_fleet`. TST-01 confirms no consumer tests exist.

**Boundary.** Own backend ASGI/Channels authentication, observation-versus-dispatch authorization, origin policy, command schema/size/rate bounds, close codes, group isolation, and communicator tests. Do not change gateway WebSocket authentication or fleet domain behavior after an authorized dispatch.

**Acceptance criteria.** Anonymous sockets are rejected before joining a group; authenticated observers cannot dispatch; authorized principals can dispatch valid bounded commands; malformed, oversized, and rate-exceeding messages fail predictably; browser origins are constrained where applicable; tenants/users cannot observe another group. Channels communicator tests cover all of these paths and assert the documented unauthorized close code, satisfying SEC-03 and its TST-01 mapping.

**Verification commands.** Run:

```sh
rg -n "AuthMiddlewareStack|AllowedHostsOriginValidator|OriginValidator|accept\(|launch_fleet" backend/og_broker backend/agents backend/tests
cd backend && python3 -m pytest -q backend/tests/test_consumers.py
cd backend && python3 -m pytest -q
```

If the current environment still lacks pytest, complete `TEST-BASE` first; absence of a runnable environment is a blocker, not a passing result.

### SEC-SECRETS — fail closed for credential encryption

**Evidence.** SEC-04: `gateway/app/db.py` derives Fernet material from the public default `magistrate_super_secret_fernet_key_32bytes_len=` and `decrypt_token` returns stored content unchanged on every exception. TST-01 confirms missing-key, tamper, migration, and rotation paths are untested.

**Boundary.** Own encryption-key configuration, ciphertext versioning, fail-closed decryption, legacy migration, rotation tooling/procedure, and gateway persistence tests. Do not own OAuth transaction integrity or general deployment defaults beyond the encryption key.

**Acceptance criteria.** Outside explicit development/test mode, startup fails when the encryption key is absent, default, malformed, or unsuitable. Stored values have a versioned ciphertext format; tampering and authentication failures never return plaintext-like data. A documented, tested migration handles intentional legacy values, and rotation can read the previous version only during a bounded migration and rewrite it under the new key. Tests cover missing/invalid keys, tampering, legacy migration, rotation, and log redaction, satisfying SEC-04 and its TST-01 mapping.

**Verification commands.** Run:

```sh
rg -n "magistrate_super_secret|except.*Exception|return.*encrypted|MAGISTRATE_SECRET_KEY" gateway/app gateway/tests docs
cd gateway && PYTHONPATH=. uv run pytest -q gateway/tests -k "encrypt or decrypt or secret or rotation"
cd gateway && PYTHONPATH=. uv run pytest -q
```

## P1 user-facing, operational, and test-foundation packages

### REL-HTTP — status-aware API failures and recoverable UI states

**Boundary and evidence.** Own a shared response decoder in `frontend/src/api/client.ts` and the representative screen error states required by REL-01. Preserve PR #4 terminal behavior and PR #5 GitHub/Home caching/navigation. Do not change authentication semantics. Evidence is the inconsistent `res.ok` handling in the client, prompt clearing in `ChatScreen.handleSend`, and Home's conversion of failures to empty arrays.

**Acceptance criteria.** All client methods reject non-2xx and non-JSON responses with bounded user-safe errors. Chat preserves a failed prompt for retry. Representative data screens distinguish loading, empty, stale/degraded, and error states. Tests cover 401, 403, 500, non-JSON, network failure, and success without duplicating PR #4/#5 cases.

**Verification commands.** `cd frontend && npx tsc --noEmit`; `cd frontend && npm test` (the unified script supplied by `TEST-BASE`); `rg -n "fetch\(" frontend/src/api/client.ts` followed by review that every response passes through the shared decoder.

### A11Y-NAV — semantic shared navigation controls

**Boundary and evidence.** Own only `BottomControls` semantics, state, and effective hit areas from A11Y-01. Do not alter destination screens or PR #3 visuals.

**Acceptance criteria.** Chat, Voice Mode, and Gesture controls expose button roles, stable accessible names, and selected/current state; all effective targets are at least 44 by 44 points without overlap; keyboard focus and activation work on web; screen-reader output and focus styling are verified on one native platform and web.

**Verification commands.** `cd frontend && npx tsc --noEmit`; `cd frontend && npm test -- BottomControls`; `cd frontend && npm run lint`. Record manual native screen-reader and web keyboard/browser results in the implementing PR.

### UPLOAD-BOUNDS — bound and validate uploaded media

**Boundary and evidence.** Own avatar and voice ingestion/storage controls from SEC-05 in `gateway/app/main.py` and related helpers/tests. Do not change transcription behavior beyond safe input handling.

**Acceptance criteria.** Both routes stream with explicit byte limits, reject missing/incorrect media types and malformed content, generate server-controlled filenames, use safe storage permissions, clean partial files, and return consistent 4xx responses. Document any upstream proxy cap as defense in depth. Tests cover boundary size, oversize, spoofed type, malformed media, traversal-like names, interrupted upload, and valid input.

**Verification commands.** `cd gateway && PYTHONPATH=. uv run pytest -q gateway/tests -k "avatar or voice or upload"`; `cd gateway && PYTHONPATH=. uv run pytest -q`.

### OPS-SAFE — fail-closed production configuration

**Boundary and evidence.** Own OPS-01 defaults and deployment validation across Django settings, compose/runtime environment, and deployment documentation. Encryption-key behavior remains in `SEC-SECRETS`.

**Acceptance criteria.** Production mode refuses committed/default Django, database, broker, token, and encryption secrets; disables debug; constrains hosts/origins; and documents TLS termination, secret injection/rotation, network exposure, backup, and rollback. An automated configuration check fails on development defaults and runs in the normal validation path. Local development remains explicit and isolated.

**Verification commands.** `rg -n "DEBUG|ALLOWED_HOSTS|password|SECRET_KEY|0\.0\.0\.0" backend gateway docker-compose.yml *.sh docs`; run the package's production-config check with a known-bad fixture (must fail) and a documented valid fixture (must pass); `docker compose config` with secrets represented only by variable references.

### TEST-BASE — reproducible backend and frontend test foundations

**Boundary and evidence.** Own TST-02/TST-03 harness and command reproducibility, not feature behavior already tested by PRs #3–#5 or the P0 packages. Preserve the live script only as a gated smoke test.

**Acceptance criteria.** A pinned backend test environment makes `python3 -m pytest -q` reproducible; the gateway no longer needs an ad hoc `PYTHONPATH=.` prefix; the frontend has one normal unit/component command with deterministic network, platform, permission, app-state, timer, and audio mocks; existing PR tests run under the combined command. The live protocol documents prerequisites, uses a non-production target, has fixture-owned setup and bounded teardown, and is excluded from default unit runs. CI, when added, runs the three hermetic suites and records blockers distinctly from failures.

**Verification commands.** Run from a fresh documented environment:

```sh
cd frontend && npm test
cd gateway && uv run pytest -q
cd backend && python3 -m pytest -q
```

Also run `git status --short` after setup; test bootstrap must not create untracked configuration or modify lockfiles.

## P2 decision and post-merge packages

### VOICE-CONSENT — decide and enforce capture consent

Own UX-02 only after PR #3. Document whether entering Voice Mode constitutes capture consent. If not, recording must start only after an explicit, labeled action; in either model, permission, recording, transmitting, error, and stopped states must be visible and accessible. Verify with `cd frontend && npm test -- voice`, `cd frontend && npx tsc --noEmit`, plus native denied/first-use/returning-user scenarios.

### WEB-ATTENTION — decide supported web notification parity

First decide whether MWC-02 requires browser notification delivery or intentionally supports in-app Attention only. If parity is required, own web permission, delivery, deep-link, deduplication, and acknowledgement behavior after PR #5 without importing native-only APIs. Acceptance covers disabled, denied, foreground, background, click-through, duplicate, and acknowledgement states on web and native. Verify with `cd frontend && npm test -- notification` and `cd gateway && PYTHONPATH=. uv run pytest -q gateway/tests -k notification`, plus a documented supported-browser matrix. If parity is not required, acceptance is an explicit product/support statement and clear web UI expectation; no implementation package should be invented.

### REALTIME-LIFECYCLE — make reconnect ownership explicit

Before activating `realtimeClient`, own REL-02 in `frontend/src/realtime/socket.ts`: one lifecycle owner, idempotent connect, explicit disconnect, cancellable timer, bounded exponential backoff with jitter, offline/app-state behavior, and no token in the URL. Tests must prove repeated connect and unmount/disconnect cannot leave duplicate sockets or timers. Verify with `cd frontend && npm test -- realtime` and `cd frontend && npx tsc --noEmit`.

### VAL-01 — active-PR post-merge validation

This is validation, not duplicate implementation. After PRs #3–#5 merge, re-run the baseline checks and inspect only residual behavior:

- PR #3: A11Y-02 reduced motion, MWC-01 live viewport resizing, UX-02 consent, and ambient/Voice performance and privacy configuration.
- PR #4: PERF-01 single-flight polling, bounded transcript, viewport preservation, long-session resource use, and phone/web behavior.
- PR #5: UX-01 truthful Home state, PERF-02 provider-call volume and lifecycle behavior, GitHub parser/cache/error/auth states, and merged-notification compatibility.
- Combined: retain PR tests without duplication, resolve shared-file integration in `frontend/package.json`, `frontend/src/api/client.ts`, and `gateway/app/main.py`, and rerun all package commands.

**Acceptance criteria.** The Voice canvas responds to live phone/tablet/desktop viewport changes; reduced motion is honored. Home never invents agent count/task/model data. Terminal polling stays single-flight and bounded while preserving user scroll. Notification polling has measured request duration/provider counts in foreground, background, offline, and failure states, with cleanup and bounded backoff. Any residual defect becomes a narrowly scoped new package with evidence; already-correct behavior creates no task.

**Verification commands.** Run:

```sh
cd frontend && npx tsc --noEmit
cd frontend && npm test
cd frontend && npm run lint
cd gateway && PYTHONPATH=. uv run pytest -q
cd backend && python3 -m pytest -q
```

Also record browser checks at phone, tablet, and desktop widths including resize without reload; a delayed-response terminal session; native foreground/background/offline notification measurements; and `~/.npm-global/bin/gh-axi pr checks 3`, `4`, and `5` before relying on their check status.

## High-finding traceability gate

No P0 package is complete until this mapping is true in its implementation review:

| High finding | Evidence owner | Acceptance-criteria owner |
| --- | --- | --- |
| SEC-01 | `SEC-AUTH` evidence paragraph and audit SEC-01 | `SEC-AUTH`, including endpoint-family negative tests |
| SEC-02 | `SEC-OAUTH` evidence paragraph and audit SEC-02 | `SEC-OAUTH`, including forged/replayed/expired/redirect rejection tests |
| SEC-03 | `SEC-WS` evidence paragraph and audit SEC-03 | `SEC-WS`, including authorization, bounds, origin, and isolation tests |
| SEC-04 | `SEC-SECRETS` evidence paragraph and audit SEC-04 | `SEC-SECRETS`, including key, tamper, migration, and rotation tests |
| TST-01 | Each of the four P0 evidence paragraphs and audit TST-01 | Tests are inseparable acceptance criteria of all four P0 packages |

Package completion requires its exact commands to pass, or a documented environmental blocker with command output. A blocker never counts as acceptance, and active-PR descriptions or unconfigured PR checks are not substitutes for local evidence.
