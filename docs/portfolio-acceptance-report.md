# Portfolio candidate acceptance — 2026-10-17

## Four separate verdicts

| Evidence class | Verdict | What this establishes |
|---|---|---|
| Code validation | **Functional suites pass; mandatory security gate RED** | Disposable Gateway contracts and frontend/browser behavior, not runtime activation. Frontend advisory audit exits 1 with **20 high, 0 critical** findings. |
| Deployed runtime | **NOT RUN / BLOCKED** | No candidate deployment, live DB access/migration, objective, service change or paid provider call. Personal publication is closed before subprocess dispatch. |
| Physical iPhone | **NOT RUN / BLOCKED** | No signed current-candidate build/install, Xcode compilation, phone notification, native streaming voice or shortcut discovery evidence. |
| Portfolio readiness | **Reviewable bounded case study; not a completed personal-assistant RC/demo** | Architecture, runbook, timed walkthrough, acceptance record and career copy are available. The required installed-phone → real-work → decision → verified-artifact demonstration is not passed. |

No advisory waiver, paid subscription fabrication, merge, EAS rebuild, publication,
employer contact or physical-device acceptance is implied by this package.

## Exact composition inputs

Declared default base: `cd47eb169add410da724391bc7fc22724607b2f4`, which retains
https://github.com/calvin-rutherford/Magistrate/pull/123 and its host-side routing,
partial-dispatch and credit-ledger safeguards.

| Review input | Exact integrated head |
|---|---|
| Foundation — https://github.com/calvin-rutherford/Magistrate/pull/124 | `b39e65d77820738dbaba6260c237cb848778f630` |
| Native — https://github.com/calvin-rutherford/Magistrate/pull/125 | `b940c9b14f62246392fda514e38c5dcd20457a72` |
| UI — https://github.com/calvin-rutherford/Magistrate/pull/126 | `05ca3abeb25943b9b88fd7da015dcd095732e511` |

These were **unlanded review branches**, not assumed main. Local non-fast-forward
integration merges preserve their ancestry. Initial inputs were foundation
`6e20d9ab2f8b9e064259be15ace130f49ab946b6` and UI
`5a3521a3896210ec050702554288ae3e28642f6c`; the current heads above were then
merged locally without overwriting integration edits. UI then advanced through
`de4aa39d213e6e27729d96adb1fa4d7c64b6d90e` and canonical answer-flow head
`fb3ecac7fc4b42a866a0773972a390319cb989c1` to the final nullable-project and
retained-outcome correction above. Native did not change.
Integration-only implementation/docs remain separate commits above those merges.
Landing order remains foundation → workstreams → integration; refreshing after
landing requires repeating the contract/full checkpoints. No remote PR was merged
or upstream branch rewritten.

## Executed commands and results

Linux, Python 3.12.3; disposable SQLite/repository fixtures and isolated browser
servers only. No operator DSN or runtime namespace is used. Tests on the recorded
inputs plus this integration's changes:

| Command | Observed result |
|---|---|
| `cd gateway && uv run pytest tests/test_release_journeys.py tests/test_magi_execution_handoff.py` | **3 passed**, 0 failed/skipped; run first and again after integration. |
| Focused Gateway command below (before nine additional canonical HTTP cases) | **168 passed**, 0 failed/skipped; retained as earlier evidence, not the final suite size. |
| `cd gateway && uv run pytest tests/test_portfolio_integration.py tests/test_firstmate_decisions.py tests/test_notification_transitions.py tests/test_push_receipts.py` | **65 passed**, 0 failed/skipped (33 integration cases). |
| `cd gateway && uv run pytest` | **610 passed, 0 failed, 1 skipped**, 611 collected; 5 FastAPI/Starlette deprecation warnings. |
| `cd frontend && npm ci` | Passed; both existing parser compatibility patches applied. Audit warnings are not waived. |
| Focused frontend command below | **25 passed**, 0 failed/skipped; additionally notification **6 native-boundary + 11 browser**, product contracts **7 passed** (49 total across these focused commands). |
| `cd frontend && npm run typegen && npm run typecheck` | Passed. |
| `cd frontend && npm run lint` | 0 errors, **15 existing warnings**. |
| `cd frontend && npm test` | **171 passed, 0 failed, 0 skipped**, including all browser suites. The separate Attention-navigation unit suite is included in the focused command, not this total. |
| `cd frontend && npx expo export --platform web` | Passed; **20 static routes**. There is no `npm run export` script; this is the unchanged Frontend CI command. |
| `cd gateway && uv run python -m scripts.export_client_protocol` | Passed using the exporter's isolated environment/database. |
| `cd gateway && uv run python -m compileall -q app tests scripts ../scripts/magistrate_diagnostics.py` | Passed. |
| `python3 scripts/production_acceptance.py check` | Passed registry/document check only. |
| `python3 scripts/production_acceptance.py run --suite release-foundation --output .release/integration/foundation-final.json` | **18 tests passed** on the clean integration merge; not production approval. |
| `git diff --check` and `test -z "$(git status --porcelain=v1 --untracked-files=all)"` | Passed after final functional pipeline and again after the audit; no generated drift. |
| `(cd frontend && npm ci && npm audit --audit-level=low --json > ../.release/frontend-audit.json)` | **Exit 1; 20 high / 0 critical**. Remaining workflow audit steps are not established by this frontend-failing step. |

Focused commands, reproducible from the repository root:

```sh
(cd gateway && uv run pytest tests/test_portfolio_integration.py tests/test_release_journeys.py tests/test_magi_execution_handoff.py tests/test_firstmate_decisions.py tests/test_firstmate_intake.py tests/test_firstmate_execution.py tests/test_auth_boundary.py tests/test_billing.py tests/test_billing_onboarding.py tests/test_provider_auth.py tests/test_identity_tenancy_projects.py tests/test_project_memory.py tests/test_uploads.py tests/test_notification_transitions.py tests/test_push_receipts.py tests/test_runtime_read_boundaries.py tests/test_hosted_execution.py)
(cd frontend && npx tsx --test tests/attention-navigation.test.ts tests/pending-intents.test.ts tests/notification-native.test.ts tests/native-chat.test.ts)
```

The skipped Gateway test is
`test_pinned_firstmate_producer_integration.py`, requiring an explicitly installed
immutable fixture via `MAGISTRATE_TEST_PINNED_FIRSTMATE_ROOT`. It was not redirected
to an operator's runtime. PostgreSQL multi-instance acceptance, installed pinned
producer execution, external TLS/WSS and device evidence were **not run** here.

Initial development failures are retained honestly: **1 failed/25 passed** in a
focused run exposed an unwanted legacy Attention shape change; it was corrected
without changing the old test. A new integration run had **4 failed/20 passed**
because the decision fixture omitted its constructor's Firstmate dependency;
the fixture was corrected. Subsequent focused/full runs above passed. No failed
assertion was suppressed or made a skip. Local raw logs stay ignored under
`.release/integration/`; they are not public assets or live evidence.

## Acceptance coverage and authority boundaries

| Scenario | Automated evidence | What remains unproved |
|---|---|---|
| Normal conversation, examples, quoted commands, hostile attachment/repository prose | Ten hostile-provider cases in `test_portfolio_integration.py` assert zero dispatch and zero submission rows; routing/native-chat suites retain host tool authority. | Behavior of an actual selected provider on the phone. |
| Owner session restore, wrong owner, revocation | Personal authenticated HTTP and provider/auth suites; frontend auth lifecycle covers renewal, outage/retry, 401, principal change, logout and relaunch caches. | Current signed build's SecureStore/device behavior. |
| Memory save/retrieve/delete | Integration test freezes exact context digest across deletion; project-memory isolation suite. | Live owner/project data persistence. |
| Durable objectives, capacity, uncertainty | Atomic two-slot concurrent fixture, prior-month holds, immutable claims, closed real-pin gate, recovery after mode change, measured settlement after logout and crash window. | Runtime-enforced workers, spend, CPU/memory/time and actual supervisor restart. |
| Repository authority | Owner-bound active GitHub App installation/repository; revocation and arbitrary-project rejection before reservation. | Provisioned external installation and disposable live demo repo. |
| Approval/decline, stale/expiry/duplicate | Gateway exact canonical answer bytes once, revision/scope/run/expiry checks, missing/foreign message rejection, nullable project, and confirmation binding; canonical Attention browser flow retains exact response/answer after queue removal on a pinned route. | Physical installed UI and actual worker continuation/decline behavior. |
| Disconnect/reconnect | Canonical-message/revision/browser recovery; immutable task identity and reserved uncertain work; no cancellation-based capacity release. | Phone/network and installed-runtime disconnect journeys. |
| Notification | Mounted authenticated test route, body-owner rejection, durable dedup, provider evidence buckets, Settings browser tests and native boundary doubles. | APNs credentials, cold-start real push, phone display and viewing. |
| Attachments | 11 Gateway upload tests plus request-cap/security and truthful upload browser suites. | Real device picker/bytes/network behavior. |
| Diagnostics | Explicit existing snapshot, byte-for-byte unchanged DB, no sidecars created, wrong/missing/symlink/WAL refusal, redacted errors and no bearer output. | Live service/auth/provider health; the tool expressly does not probe them. |
| Verified result/artifact | Existing execution-handoff fixture uses real authenticated HTTP and a disposable README artifact with substituted external dispatch. | Real personal-server work, reviewable live commit/PR and phone result. |

## Blocking discrepancies and external gates

1. **Security:** unchanged advisory gate remains red. Published `braces` 3.0.3
   ([GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)) and
   `node-forge` 1.4.0 ([GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv))
   remain affected in this locked install. Foundation's compatible updates reduce
   propagated findings; no Expo downgrade, local audit suppression or risk waiver
   is a fix. Re-run the unchanged complete workflow after upstream remediation.
2. **Personal runtime:** the reviewed Firstmate/tasks-axi bridge has no enforceable
   immutable worker/spend envelope. `personal_runtime_limits_unavailable` is a
   tested pre-publication refusal, not an enabled runtime. Admission accounting
   alone does not supply sandboxing, hard cost limits or bounded execution.
3. **Producer/startup:** the pin does not emit the full run/request/scope/expiry
   decision binding, and the installer requires already-reloaded runtime sessions.
   No unattended personal supervisor service/restart contract was established.
   Request-bound fields are accepted/persisted when actually supplied, never
   fabricated from a read or wall-clock timeout.
4. **Client scope:** additive objective-state projection is server-side; frontend
   compatibility fields remain unchanged, and its shared schema still needs
   inclusion in the client export registry before client adoption. Do not claim
   the new projection has replaced all UI compatibility labels.

   The canonical Attention seam itself is now integrated: it replaces legacy
   action calls with `/api/v1/decisions/{id}/confirmation` and `/answer`, using the
   latest canonical user-message ID and explicit exact-byte preview, never body
   text, owner or task authority. Both reported UI defects were corrected on the
   owning branch, not forked here. Final browser regressions accept nullable
   `target.project` and retain the exact request-bound response/submitted answer
   after the pending item disappears on a notification-pinned route. They do not
   resurrect the item or infer success from list disappearance. Approve, decline,
   stale, duplicate and cancel fixtures pass alongside Gateway expiry, ownership,
   revision and canonical-byte cases. This is hermetic integration evidence, not
   a live producer/worker/phone journey.
5. **Deployment/native:** operator-controlled TLS, actual service readiness/auth,
   backups/rollback, resource isolation, credentials and repo installation need
   separate authorization/evidence. Xcode/Apple signing/APNs/EAS, native streaming
   transport and authorized voice credentials, physical install/shortcuts and
   cold-start push/voice are not established by Linux/browser tests.

See the [runbook](portfolio-runbook.md) for exact owner actions and the
[2–3 minute demo](portfolio-demo-guide.md) for an honest presentation. This report
is not permission to deploy, spend, submit work, merge or distribute a build.
