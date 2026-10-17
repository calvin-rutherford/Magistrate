# Personal engineering assistant — observed foundation

Evidence collected **2026-10-17 on the selected personal server**, from isolated branch
`fm/magistrate-personal-contracts-p1`. This is a completion checklist, not a
production-complete declaration. Historical `COMPLETE` labels are not evidence.

## Completion checklist

| Capability | Observed condition | Required change | Acceptance evidence |
| --- | --- | --- | --- |
| Preserve the actual baseline | Local main, remote main and clean deployment checkout were `cd47eb169add410da724391bc7fc22724607b2f4`; https://github.com/calvin-rutherford/Magistrate/pull/123 is merged, not draft. Forge reports 3 passed/1 failed checks on that historical change; its body reports a prior 481-pass Gateway run, not fresh evidence. | Additive branch only; preserve host-computed tool routing and failed-dispatch reservation release. | Fresh intake/execution, routing/billing/auth suites below; no reset, migration or deployment. |
| Preserve parallel/local work | `git worktree list --porcelain` showed primary, deployment and 15 pool worktrees. All were clean at inspection except slot 2's untracked `.audit/`; slot 3 retained its distinct branch. This task began clean in slot 1. | Leave every other worktree, branch and untracked artifact intact. | Read-only `git status --porcelain=v1` in each listed checkout; only the four allocated files changed here. |
| One dependable runtime | Installed pinned Firstmate matches the repository lock; Codex and OpenCode have documented structured APIs, but no Magistrate adapters for those direct paths. | **Keep the existing Firstmate bridge as the one default startup path**; no runtime selector or deletion. | Installed versions, official interfaces and rejection rationale below; existing intake/execution tests. No new live submission performed. |
| Exact personal authority | Baseline has normal authenticated principals and commercial execution gating, but no explicit personal deployment contract. | Added server-only `DeploymentMode` and `magistrate.personal-deployment.v1`; exact configured owner only. Terminal lane must compose it after auth. | `test_personal_deployment.py`: exact owner, forged owner-like IDs/body identity, scope, expiry, real session restore/revocation. **Not yet a wired route.** |
| No subscription prerequisite | Baseline intake/recovery/settlement directly use `CreditLedger`; onboarding can require subscription state. | Added personal-only entitlement/checkout policy; integration must detach normal personal onboarding/checkout and use independent durable resource holds, not fake plans or credit grants. | Tests demonstrate policy refusal/allowance and unchanged financial rows; public-SaaS ledger still enforces concurrency. End-to-end billing detachment remains an integration gate. |
| Small execution/spend envelope | Existing provider router reserves estimated costs and enforces budgets; commercial concurrency currently lives in the credit ledger. | Added maximum 2 implementation workers (default 1), equally bounded outstanding objective slots, mandatory per-objective/monthly micro-USD ceilings and full-ceiling admission guard. Integrator must atomically persist holds and enforce runtime hard stops. | Negative/unknown usage, cross-owner snapshot, worker overflow and spent+reserved budget tests. No claim of a new durable scheduler or live spend limit. |
| Durable truthful objective state | Submission rows, structured execution events and decisions exist; legacy projection uses `done/working/blocked` and can default absent phases to queued. | Added `magistrate.objective-state.v1` with explicit unknown/recovering, accepted vs running, waiting_for_input, succeeded, and observed cancellation; integrate additively, without rewriting old rows. | Projection, monotonic-revision, terminal stability, replay and cancellation tests; mapping below. |
| Canonical conversation/approvals | Native Chat is the sole human-conversation path; host route categories constrain tools; high-impact dispatch and decision answers have explicit confirmation boundaries. | Preserve these paths and request-bound confirmations in personal mode. No runtime transcript becomes chat or approval authority. | Existing routing/intake/execution checkpoints; contract rejects request-body identity. Full chat/decision regression suite remains a terminal-lane gate. |
| Session and transport security | Server-issued bearer/refresh revocation exists; local `/livez` and `/readyz` returned 200. External TLS, firewall reachability and WSS were **not verified**. | Preserve SecureStore/HttpOnly restoration and first-frame socket auth; verify HTTPS/WSS and restrict direct backend exposure before activation. | Local HTTP status only, plus auth regression suite. No claim of externally secure deployment from readiness alone. |
| Upload/resource safety | Existing private owner-bound uploads and actual-byte caps remain in `uploads.py`/ASGI middleware. | Do not bypass caps, content validation, idempotency or provider budgets with personal entitlement. | No changes to those boundaries; integration must run upload/security suites. Foundation admission tests are not upload evidence. |
| Running service/data ownership | Dedicated Gateway user unit is active/running under the inspected service account; executable is `uv`; working directory is the separate deploy checkout's `gateway`. SQLite has private file permissions. | No service or live database edits. Verify any eventual activation under operator change control. | Selected systemd properties, executable/port inspection and read-only schema existence checks below. No secrets or row contents read out. |
| Personal UI/native polish | Chat shell, voice, push and App Intents already exist; this lane did not exercise them on a device. | Allocated UI/native lanes finish personal-first presentation and decision-return loop. | Their browser/native test reports and later physical-device evidence; **not established here**. |

## Baseline and operational evidence

- Repository authority read: root `AGENTS.md`, existing contracts, auth, billing,
  native-chat architecture, runtime observation boundary, production-security
  runbook, objective intake/execution/cancellation and provider routing code.
  Frontend files were not changed.
- Unit identity/ownership, executable, working directory, process identity and
  health were inspected through explicit non-secret property allowlists. No
  environment or command-line dump was emitted. Host/user/path/bind details are
  deliberately omitted from this public document; exact non-secret observations
  are retained locally in gitignored
  `build/validation/personal-contracts/host-evidence.md`.
- Deployment checkout: **[redacted deployment path]**, separate from this task.
  Its HEAD is observed, but a checkout revision alone does not prove every
  module in the already-running interpreter was loaded from that revision.
- An allowlisted configuration inspection emitted only feature **names**
  `MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED`, `MAGISTRATE_FRIEND_BETA_ENABLED`
  and the two non-secret path settings below. Neither name proves a feature is
  enabled. `MAGISTRATE_DEPLOYMENT_MODE`, `MAGISTRATE_HOSTED_EXECUTION_ENABLED`
  and `MAGISTRATE_DATABASE_URL` were absent from that selected file; overrides
  elsewhere were not inspected. No `.env` contents or secret values were dumped.
- Selected `MAGISTRATE_FIRSTMATE_ROOT`: **[redacted pinned runtime path]**.
  Read-only Git revision equals the repository lock; **11/11** lockfile artifact
  digests match `runtime/firstmate-producer.lock.json`. No runtime/Herdr control
  invoked, and no exact deployment pin is repeated in this public checklist.
- Selected `MAGISTRATE_DB_PATH`: **[redacted database path]**. Inspection required
  that exact existing regular non-symlink path, then opened SQLite URI `mode=ro`
  with `query_only=ON`; missing/wrong paths would fail, never create a database.
  Only schema presence was queried for `magi_objective_submissions`,
  `firstmate_execution_events`, `gateway_sessions`, `billing_accounts` and
  `credit_reservations` (all present). No row contents, migration, backup,
  financial rewrite or credential-store inspection was performed.

## Runtime decision: retain Firstmate as the default

Installed evidence: `codex --version` → **codex-cli 0.162.1**;
`opencode --version` → **1.18.32**; `tasks-axi --version` → **0.2.6**;
Firstmate installed commit/artifact match above. `codex app-server --help`,
`codex exec --help`, and `opencode serve --help` were inspected, not started.
`codex app-server generate-json-schema --out <worktree build directory>`
confirmed the installed non-experimental protocol exposes `thread/start`,
`thread/resume`, `turn/start`, `turn/interrupt`, `account/login/start`, and
command/file approval requests. Schema generation made no model request.

Official documentation retrieved successfully on the evidence date:

- [Codex app-server](https://developers.openai.com/codex/app-server)
  (redirected to <https://learn.chatgpt.com/docs/app-server>).
- [Codex authentication](https://developers.openai.com/codex/auth)
  (redirected to <https://learn.chatgpt.com/docs/auth>).
- [Codex non-interactive](https://developers.openai.com/codex/noninteractive)
  (redirected to <https://learn.chatgpt.com/docs/non-interactive-mode>).
- [OpenCode server](https://opencode.ai/docs/server/) and
  [OpenCode providers](https://opencode.ai/docs/providers/).

| Option | Submission and structured progress | Approvals, cancellation, recovery | Authentication, dependencies, disposition |
| --- | --- | --- | --- |
| **Existing Firstmate bridge — selected** | Canonical authenticated chat → deterministic objective/task identity → bounded `tasks-axi add` receipt → native inbox wake. Persisted `firstmate.execution-event.v1` pushes supply progress, not terminal scraping. | Owner/revision-bound decisions and canonical answer bytes; confirmed high-impact dispatch. Durable cancellation request stays requested until `objective.cancelled`. Bounded startup replay of deterministic identities; read paths never reconcile. | Requires pinned producer, trusted tools/runtime home, durable backlog/inbox and separately authenticated model/provider access. Most dependencies already exist and match the installed pin. Smallest code/operations change; no live submission/approval/cancellation test here. Personal resource adapter still required. |
| Direct Codex app-server — defer | JSON-RPC initialize/initialized, thread/start or thread/resume, turn/start; thread/item/turn notifications and token usage. Real supported integration interface, not CLI screen scraping. | Server requests `item/commandExecution/requestApproval` / `item/fileChange/requestApproval`; turn/interrupt requests cancellation and turn/completed reports interrupted. Resume stored thread IDs; Gateway would still need durable objective mapping, event replay/dedup, ambiguous-dispatch recovery and approval ownership. | Official account/login/start/read/logout; documented ChatGPT or API-key authentication (API billed separately). Prefer private stdio; docs label WebSocket transport experimental/unsupported. Would replace working bridge logic with a new supervised adapter, sandbox/worktree policy and usage gates. Strong future simplification candidate, **not** a drop-in startup switch. |
| Direct `codex exec --json` — reject as default interactive bridge | Real one-shot submission, JSONL thread/turn/item events and final output schema; installed CLI supports explicit session resume. | No equivalent interactive app-server approval request/response channel. Host process termination is not proof of durable objective cancellation; explicit session IDs and external recovery remain necessary. Never use bypass-approvals flags to make this fit. | Reuses Codex auth; official docs recommend API keys for programmatic workflows. Useful for bounded CI jobs, not the shortest dependable approval-capable personal app integration. |
| OpenCode server — reject for this slice | Documented create session, POST message or prompt_async, SSE `/event` and session status/message reads. | Documented permission response route, session abort, persisted session/message retrieval. These do not supply Magistrate owner binding, cancellation observation, event durability or exactly-once objective recovery automatically. | Installed serve defaults to loopback; documented HTTP Basic auth via server password must be explicitly configured plus TLS at the boundary. Provider docs offer provider-specific connection/auth choices including OpenAI ChatGPT or API key; that is **not** evidence of cross-provider subscription entitlement. Adds another server/auth/provider/permission adapter without removing the current integration cost. |

The recommendation concerns **execution**, not replacing provider-native Magi
Chat with harness history. No cookies, private endpoints, auth files, tokens or
terminal text were used. Installed interfaces and official docs establish
capability, not this host's logged-in account, purchased entitlement, remaining
quota, model availability, or successful execution. Do not infer OpenAI API,
Anthropic, or another provider access from a ChatGPT/OpenCode subscription.

## Exact terminal-lane integration seam (not wired by this slice)

### Server-controlled deployment policy

Load `DeploymentMode.from_env()` once during startup in `gateway/app/main.py`
before marking startup complete. Default is `MAGISTRATE_DEPLOYMENT_MODE=public-saas`;
no personal bypass exists unless the value is exactly `personal`. Required in
personal mode:

- `MAGISTRATE_PERSONAL_OWNER_PRINCIPAL_ID`: exact opaque auth `Principal.user_id`,
  no bootstrap fallback, prefix match, normalization or body-supplied identity.
- `MAGISTRATE_PERSONAL_MAX_IMPLEMENTATION_WORKERS`: integer 1–2, default 1.
  The same ceiling bounds unresolved objective slots to keep intake small.
- `MAGISTRATE_PERSONAL_OBJECTIVE_SPEND_LIMIT_MICRO_USD` and
  `MAGISTRATE_PERSONAL_MONTHLY_SPEND_LIMIT_MICRO_USD`: explicit positive safe
  integers; objective ceiling cannot exceed monthly ceiling. No unlimited or
  free-plan default. These are cost limits, **not purchased credits**.

Invalid enabled configuration fails startup with a content-free 503 policy
error. Public-SaaS mode ignores stale personal settings and retains all existing
commercial behavior. Disabling personal mode does not mutate old accounts.

After existing HTTP/session/WS verification, call
`policy.require_principal(principal, scope=...)`; it additionally rejects every
non-owner in personal mode. Never construct `Principal` from a request body.
This function is **not** a revocation verifier: keep the existing normal auth
boundary, session renewal/logout and socket revocation checks. Long-running and
recovery writes need their existing durable authorization provenance; do not
manufacture a perpetual owner session to run them.

- `requires_commercial_entitlement(principal)` is false only for the verified
  configured personal owner. `checkout_allowed(principal)` is false there too.
  Use these at personal onboarding/account/checkout seams; do not create an
  `active` subscription, grant purchased credits, drop billing tables, or change
  webhook processing/history to implement the distinction.
- `check_personal_admission(principal, usage=PersonalResourceUsage(...),
  implementation_workers=N)` requires command scope and returns the **full
  objective ceiling** to reserve, or a closed policy error. The snapshot must
  be owner-bound, complete and taken under the same durable write transaction
  as reservation; unknown observations are not zero. Count queued, waiting,
  unknown/recovering and cancellation-requested work, including outstanding
  worker leases and prior-month holds. Apply the returned ceiling as a runtime
  hard limit and retain `magi_routing.py` provider/cost/failover budgets.
- This pure guard **does not reserve capacity**. Before enabling personal
  dispatch, implement an independent durable cost/capacity reservation adapter,
  atomic idempotent claim/replay, measured settlement and bounded worker/resource
  enforcement. Do not wrap `CreditLedger` with a fabricated unlimited account.
  CPU/memory/time isolation and external runtime budget enforcement remain
  activation dependencies, not capabilities of this policy module.
- Injection points already present: `FirstmateObjectiveTools(credit_ledger=...)`
  for normal intake. `reconcile_pending_objective_intake` currently constructs
  `CreditLedger` internally; `FirstmateExecutionService` settlement also needs
  the same durable policy/adapter selection. Coordinate ownership before
  changing those modules. Preserve the existing failed-dispatch release rules,
  accepted-race protection and deterministic objective IDs. Unknown dispatch
  outcomes must retain/reconcile resource holds, not blindly release/re-submit.
  Do not bypass billing only in the HTTP handler and leave recovery commercial.
- Financial reservations made before a mode switch retain their original
  settlement authority. Personal work must not settle against historical credit
  rows. Store admission provenance; never infer it from the current flag.

### Durable objective-state mapping

`ObjectiveStateContract` is an additive, strict, immutable projection. It does
not migrate existing Firstmate event names or replace the canonical conversation.
`project_objective_state(...)` consumes **owner-filtered persisted facts** and a
persisted monotonic revision. `advance_objective_state(current, incoming)` checks
identity/revision/replay/terminal stability under the caller's write lock; the
caller must persist the returned record with compare-and-set, not treat this
pure function as a DB. Its explicit forward-transition policy rejects
running→queued/submitting and accepted→queued/submitting even at a newer
revision. The persisted `progress_stage` high-water mark survives unknown,
recovering and input waits, preventing an uncertainty round-trip from laundering
a backward retry or resurrecting terminal work.

- No facts, incomplete observation or unknown event vocabulary → `unknown`.
- Submission `submitting` → `submitting` (strictly pre-acceptance).
- Durable queue publication (`submission_status=accepted`) → `queued`;
  structured `objective.accepted` → `accepted`. Neither implies a running worker.
- Worker/implementation/test/review phases → `running`; tests passing alone
  never means objective success, and tests failing alone never means terminal
  objective failure.
- Persisted pending input/decision → `waiting_for_input`; approval is one reason,
  not the only possible input. Explicit persisted recovery intent → `recovering`.
  A failed submission alone yields `unknown`, not fictional terminal failure or
  an unsupported claim that a recovery attempt is running.
- Validated `objective.completed` evidence → `succeeded`; `objective.failed` →
  `failed`; **observed** `objective.cancelled` → `cancelled`.
- Cancellation is separately `none/requested/observed`; requesting does not end
  work. Terminal execution truth wins over missing observation/recovery/late
  progress. Equal revision must replay identical facts; older revisions,
  identity changes, terminal rewrites and forgotten cancellation are rejected.

Export the new schemas through the existing protocol-export owner when the
integration actually exposes them. Existing frontend `done/working/blocked`
compatibility fields must not be silently reinterpreted by this foundation.
No runtime probes, polling supervisor, terminal scraping or read-triggered
execution belongs in these projections.

## Validation evidence and limits

Run from this branch using the plan's exact commands:

```sh
cd gateway
uv run pytest tests/test_personal_deployment.py tests/test_firstmate_intake.py tests/test_firstmate_execution.py
uv run pytest tests/test_magi_routing.py tests/test_billing.py tests/test_auth_boundary.py
```

Observed after the final transition-policy additions: **103 passed** (89 personal
policy/projection + 14 intake/execution), then **47 passed** (routing/billing/auth). Existing FastAPI lifecycle and Starlette
TestClient deprecation warnings remain. An initial new billing test failed
because its disposable fixture lacked a user profile; fixed the fixture, not the
ledger or any live data. `uv run python -m compileall -q app/deployment_mode.py
app/contracts.py tests/test_personal_deployment.py` and `git diff --check` passed.

This slice neither composes startup/routes nor claims activated personal
execution. No service changes, real objective dispatch, provider spending,
external deployment, live DB writes or physical-device tests were performed.
