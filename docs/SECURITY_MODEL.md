# Security model — release authority boundaries

Authority: [production matrix](PRODUCTION_STATUS.md). Repository controls and
advisory remediation: **COMPLETE**. Deployed isolation, legal/security review
and physical-device evidence: **BLOCKED_EXTERNAL**. No compliance certification,
penetration test or production threat-model approval is inferred from unit tests.

## Trust boundaries

| Boundary | Executable enforcement | External proof still required |
|---|---|---|
| Client → Gateway | Scoped bearer, verified provider issuer/audience/nonce/time, rotating channel-bound refresh, owner-qualified opaque IDs | Console audiences/redirects, actual login/logout/reuse and device storage |
| Model → host | Closed tools, host identity/idempotency, bound confirmation, bounded inert context; attachment turns have no execution tools | Provider egress/data terms and hostile-input review |
| Payments → entitlement | Exact raw-body signature/time checks, immutable webhook identity, integer reservations/settlement | Merchant/price approval, real test-mode reconciliation, legal/mobile purchase rules |
| GitHub → repository | Owner-bound App installation, current selected-repository projection, scoped server-only tokens, signed webhooks | Console permissions/installations/revocation and broker scope |
| Gateway → worker | Exclusive hosted intake, per-objective bearer, mTLS receipt validation, durable leases/events/decisions | Enforced process/filesystem/network/resource isolation and cleanup under hostile workloads |
| Files → consumer | Actual-byte caps, MIME/digest/owner checks, private paths/quotas, optional fail-closed scanner, authenticated signed access | Provisioned volumes, scanner operation, disk quotas, backup/encryption/retention |
| Push → device | Durable ticket/receipt ledger; owner/fingerprint/token-hash binding, delayed bounded polling, exact invalid-token retirement | APNs/FCM credentials, receipt and physical arrival/tap evidence |
| Operations | Content-free correlation/telemetry, independent metrics token, fail-closed production preflight and HTTP-200 readiness | Private monitoring/alerts, public TLS/WSS, isolated restore, incident ownership |

The authenticated principal owns one personal workspace. This is not an
enterprise organization/team membership product. Hosted infrastructure cannot
be certified by Gateway receipt validation: a shared UID/HOME or Docker socket
is not a sandbox. The read-only GitHub App and privileged worker token broker
have distinct permission authorities; never mount App keys into a repo worker.

## Fail-closed invariants

- Unknown/foreign owner, repository selection, entitlement or capability denies;
  no fallback to operator identity. Email never auto-links login accounts.
- Human grants never receive producer `response` scope. Hosted mode rejects
  legacy user-scoped producer ingress. Restricted local Friend Beta remains an
  explicitly acknowledged shared-runtime cohort, not public-SaaS isolation.
- Reads do not drive workers/Herdr. Configured readiness is not a live upstream
  probe. Only explicit write-side controllers own execution reconciliation.
- Accepted work, requested cancellation, accepted push ticket, provider receipt,
  device arrival and viewing are different facts. No prose or HTTP 200 fabricates
  completion. Exact decision answers remain outside model-selectable arguments.
- Prompts, raw provider errors, keys, private answer bytes and terminal text do
  not belong in logs, public diagnostics, push copy or release artifacts.
- Received-byte/time/in-flight limits are per-process; shared upload quotas use
  DB serialization. Edge rate limits and filesystem quotas remain defense in depth.
- Provider HTTPS/host allowlists and redirect/proxy refusal are not an egress
  firewall; infrastructure must deny metadata/private/link-local destinations.

## Dependency reachability and compatible remediation — 2026-09-28

The older four-finding report in the component security runbook is historical.
This reconciliation resolves it without an Expo downgrade or an unreviewed
parser fork:

- **image-size**, GHSA-5p2g-fcmc-qvqq / GHSA-w3rx-r6r6-pgpr: reachable at build
  time through Metro asset parsing, including hostile headers disguised as PNG.
  Metro 0.84.4's synchronous string-path call is incompatible with patched 2.x's
  byte-only default; no patched 1.x exists. The scoped override selects **2.0.4**;
  `patches/metro+0.84.4.patch` uses its documented async `imageSizeFromFile` for
  paths and retains the byte API for buffers/ZIP assets. No parser code is copied.
- **decode-uri-component**, GHSA-vcc3-ghjq-m6fr: reachable at runtime through
  Expo Router → query-string on malformed incoming query/deep-link text.
  Patched **0.5.0** is ESM, unlike the old callable CommonJS export.
  `patches/query-string+7.1.3.patch` selects its default export under the supported
  Node 22 / Metro toolchain. No decoding algorithm is forked.
- The already merged `xcode → uuid@11.1.1` scoped override remains; its v4-only
  CommonJS compatibility regression remains mandatory.

`npm ci` applies versioned `patch-package --error-on-fail` patches. The release
suite asserts the exact reviewed versions, exercises real Metro file/buffer assets
for web/iOS/Android, Unicode/duplicate/malformed query semantics and timeout-bound
malicious ICNS/HEIF/JXL/query fixtures. Full browser tests and export are still
mandatory; a lockfile-only audit is insufficient. Re-review/remove the bridges
when supported upstream consumers adopt these APIs; never blindly bump a patch.

Current local `npm audit` reports **zero** advisories for frontend and Pi adapter;
`pip-audit 2.10.1` reports **zero known vulnerabilities** in the installed locked
Gateway and installed retained backend environments. Editable project source is
not an advisory package. These are point-in-time database lookups, not proof of
absence of vulnerabilities. CI's `Locked dependency advisory checks` reruns all
four scans; reports are separate from hermetic receipts. No risk waiver or
unresolved upstream exception is being claimed. Isolate CI/build credentials
from untrusted repo code even with a clean scan.

## Lifecycle, secrets and recovery

Production keys live in a private secret manager/environment, never Git,
`EXPO_PUBLIC_*`, URLs, browser storage or CI logs. Rotate formerly exposed
historical authority: deletion of committed DB/log files did not purge Git history.
Keep independent bootstrap, Fernet, metrics, upload-signing and worker-identity
keys and the key versions needed for approved restores.

Account erasure covers owned current/historical content, scoped context, credits,
sessions, GitHub bindings and receipt rows; file quarantine rolls back with the
DB failure. Hosted erasure first fences/cancels external work and fails closed if
cleanup cannot be confirmed. External provider retention and backup expiry need
operator/legal approval. Restoring an old snapshot may resurrect revoked access:
keep traffic closed and review/reissue authority before reopening.

Django/Compose are lab-quarantined, legacy launch/rsync paths refuse production,
and unauthenticated AR is removed. Pi is retained regression code, never a
human Chat or rollback path. See [PRODUCTION_ACTIVATION.md](PRODUCTION_ACTIVATION.md)
and [SAAS_ARCHITECTURE.md](SAAS_ARCHITECTURE.md).

Mandatory adversarial gates include `tenant-authorization`, `security-boundaries`,
`provider-auth`, `github`, `billing-ledger`, `uploads`, `push-deep-links`,
`execution-recovery-isolation` and full `integration`. Release admission rejects
dirty/stale/failed/missing/wrong-class evidence; it validates hashes and structure,
not the truth of a human attestation. An independent release authority must review
the real artifacts. No waiver field can bypass a mandatory checkpoint.
