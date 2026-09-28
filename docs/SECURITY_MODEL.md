# Security model — release authority boundaries

Authority: [production matrix](PRODUCTION_STATUS.md). **Production multi-tenant
security convergence: FAILED.** Principal scoping and provider verification are
implemented; shared operator resources are not a SaaS isolation boundary.

## Trust boundaries

| Boundary | Existing enforcement | Required production evidence / owner |
|---|---|---|
| Client → Gateway | Opaque scoped bearer; provider signature/issuer/audience/time/nonce verification; one-time challenge and refresh rotation | A1/A2/A11: per-route tenant/project membership and revoked/disabled identity tests |
| Native/web session storage | Native SecureStore; web HttpOnly refresh cookie; principal-qualified chat caches | A2/A8: physical logout, refresh reuse, offline expiry, cross-device and cross-account clearing |
| Gateway → provider | Server-only credentials; encrypted stored OAuth/execution secrets; bounded request/result adapters | A7/A11: least-egress/provider policy, secret rotation and redacted failure telemetry |
| Model → host tools | Closed named tools; host-owned identity; strict bounded JSON; confirmation outside model authority | A5/A7/A11: untrusted context cannot choose tenant, command, key or arbitrary tool |
| Gateway → Firstmate | Deterministic intake and pinned producer; authenticated structured events; immutable correlation/evidence | A5/A11: isolated runtime credentials/files/network and producer ownership |
| Worker → product | Durable typed events, not terminal text; no inferred completion | A5/A12: replay/conflict/foreign-owner/out-of-order and crash recovery |
| Files → consumers | Bounded/type-validated chat files and owner-qualified access | A10/A11: durable private avatar/blob policy, malicious content handling, quotas, retention and backups |
| Payments → entitlement | Absent in baseline | A4/A11: raw-body signature verification, replay-safe ledger and tenant customer mapping |
| Notifications → authority | Permission/mode affects alert volume only; pending-intent routing; acknowledgement separate from decision | A9/A11: real receipts, authenticated deep links, exact confirmed decision answer |

## Fail-closed invariants

- No client/model-provided principal field grants access; opaque IDs do not
  substitute for owner checks. Unknown tenant membership, repository selection,
  capability, entitlement or policy must deny, not fall back to operator state.
- Never merge provider subjects by email. Login accounts and integration OAuth
  accounts have different `account_kind` and cannot grant each other's authority.
- Scope `response` is producer authority, not a human provider/Friend Beta grant.
  Generic `command` exposes shared runtime controls today; a scoped bearer alone
  is not a safe multi-tenant command grant. Friend Beta elevation remains explicit
  restricted-cohort risk, not production tenancy.
- Reads cannot schedule, reconcile execution lifecycle, signal workers or scrape
  Herdr. Health configuration flags are not proof a provider/runtime is reachable.
- Persist causality/evidence before generating completion prose. Reject changed
  facts under an accepted event/key. Pending cancellation, push acceptance and
  model timeout each remain distinct from observed external success.
- Never carry prompts, keys, terminal transcripts, raw decision answers or full
  provider failures into public Activity, push, diagnostics or release receipts.
- No executable context, retrieved text, repository file, upload or model answer
  may register tools or change routing/confirmation policy.

## Known repository security gaps, not external excuses

A1/A11 must finish tenant/project authorization; A3 must replace shared GitHub
service access for tenant repository authority; A5 must prove worker isolation;
A4 must implement payment/ledger boundaries; A10/A11 must converge public
checkout-local avatar storage and complete file lifecycle; A9 must converge push
receipt semantics. Missing deletion/retention and provider-context egress policy
must not be described as GDPR compliance or complete account deletion. No
penetration test, SOC report, regulatory certification, physical attestation or
App Store approval is claimed.

The root Docker compose mounts the host Docker socket in the retained worker.
That configuration is not an approved SaaS sandbox. Neither a separate principal,
worktree, provider key label nor bearer scope proves process/filesystem/network
isolation. Operator worker activation is outside this foundation.

## Secret handling and recovery

Production bootstrap/Fernet/provider secrets stay in the approved server secret
store and private environment, never `EXPO_PUBLIC_*`, Git, browser storage, URLs,
logs or Actions artifacts. Generate independent high-entropy keys; preserve
versioned Fernet keys needed for retained backups. Restrict state directories and
backup files; reject symlink/ownership/checkout-local deployment state per the
guarded deploy contract. Restored sessions and external grants need an explicit
revocation review: restoring an old DB must not accidentally restore retired
access. Backups contain sensitive user data even when provider secrets inside
are encrypted. File and DB backups require coordinated retention/deletion policy.

Operator account/console configuration is in
[PRODUCTION_ACTIVATION.md](PRODUCTION_ACTIVATION.md). A12 cannot choose legal
identity, pricing, data-retention promises or destructive migration without owner
approval. Record external ownership and verification, not fabricated values.

## Required adversarial acceptance

Run the complete `tenant-authorization`, `provider-auth`, `uploads`,
`execution-recovery-isolation`, `billing-ledger` and `integration` suites. Each
new domain adds explicit unauthenticated/wrong-scope/wrong-tenant/expired/revoked
read and write cases, guessed IDs, replay/conflict and malformed/bounded-body
cases. Cover websocket reconnect and replay, OAuth redirect and audience
confusion, refresh family reuse, webhook duplicate/out-of-order facts, foreign
blob/context/repository access and cross-runtime credential leakage.

Release admission rejects missing suites, ambiguous JSON, dirty/stale commits,
wrong evidence classes, missing checkpoints and artifact hash mismatches.
Receipts are not cryptographic proof that a human external attestation is true:
a named independent release reviewer must inspect evidence and approve the final
RC. Require the CI checks and environment approvals in forge settings as an
external activation item. No bypass/waiver field exists in the evidence packet.
