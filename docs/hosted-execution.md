# Hosted Firstmate execution

Status: production contract implemented; backend activation is **BLOCKED_EXTERNAL** until an isolation service, worker image, controlled network policy, and GitHub App broker pass the release gates below.

## Boundary

`MAGISTRATE_HOSTED_EXECUTION_ENABLED=true` replaces the shared local
`tasks-axi`/Firstmate-home intake path for normal users. The existing
`firstmate.submit_objective` contract and idempotency ledger remain the public
entry point. Its accepted row is the durable queue. The write-side controller
in `gateway/app/hosted_execution.py` automatically:

1. claims global and per-tenant capacity with an expiring database lease;
2. persists one deterministic `objective.accepted` event;
3. idempotently requests one digest-pinned ephemeral worker from the configured
   mTLS isolation backend and publishes `worker.started` only after the backend
   reports real running/finished capacity; and
4. reconciles it until a structured terminal fact is durable, then requests
   cleanup.

The app may close and either service may restart. Queued rows remain durable. A
crash-expired launch is retried with the same opaque backend execution id, same
worker bearer, and same event id, so retries converge instead of creating a
second worker. Product Fleet, Activity, Attention, and completion continue to
read persisted structured ledgers; they never query the isolation backend.
Hosted mode never invokes Herdr or drives Herdr lifecycle.

## Provider-neutral isolation API

The backend may use a rootless container runtime, microVMs, managed jobs, or a
stronger mechanism. Magistrate depends only on this mTLS API:

```text
PUT    /v1/executions/{opaque_execution_id}  Idempotency-Key: {same id}
GET    /v1/executions/{opaque_execution_id}
POST   /v1/executions/{opaque_execution_id}/cancel
DELETE /v1/executions/{opaque_execution_id}
```

`PUT` receives `magistrate.isolated-execution.v1` with an immutable image
digest, fixed worker command, opaque tenant/isolation/execution ids, callback
bearer, and mandatory controls. It must return exactly
`magistrate.isolation-receipt.v1` with the requested id and
`accepted|existing`. `GET` returns exactly `magistrate.isolation-status.v1` and
`queued|running|succeeded|failed|cancelled`; every terminal response also
carries a strict `FirstmateMeasuredUsage` record. A terminal status without
measured usage is retried and cannot manufacture a product failure or billing
settlement. A cancellation that wins before launch uses the catalogued
`magistrate/no-worker` zero-usage measurement because no worker or provider was
allocated. `404` means absent and causes an idempotent recreate. Cancellation
uses a deterministic idempotency key and
must create a cancellation tombstone even if it races the initial `PUT`. A
queued cancellation is terminally recorded without allocating capacity. No
cloud-provider or orchestrator resource appears in the Gateway contract.

The backend must enforce, rather than merely record:

- a dedicated process and ephemeral filesystem per objective;
- non-root execution, read-only base filesystem, no-new-privileges, no ambient
  capabilities, and a 256-process ceiling;
- configured CPU, memory, workspace, and wall-time limits;
- no ingress and default-deny egress, with HTTPS allowed only to the exact
  configured host list; and
- bounded teardown after exit or Gateway `DELETE`.

Opaque HMAC identities prevent raw principal ids, repository aliases, and
objective text from entering backend resource names. Objective text is not in
the launch request; the authenticated worker fetches it after startup.

## Worker identity and contract

The Gateway creates a 384-bit random bearer once, stores its encrypted value
and SHA-256 lookup binding only while the run is active, and sends it only
inside the mTLS launch request. Every worker claim, event, decision, answer,
and credential request hashes that bearer and checks it against the exact
objective; terminal transition erases the encrypted bearer. A bearer from one
objective or tenant cannot address another. Hosted mode rejects the legacy
user-scoped execution/decision producer routes, so a normal command bearer
cannot forge worker evidence.

The digest-pinned image must provide
`/opt/magistrate/bin/firstmate-worker`. It must:

1. fetch `GET /api/v1/hosted-execution/objectives/{objective_id}` with its
   bearer and run one Firstmate objective in its ephemeral workspace;
2. publish closed `firstmate.execution-event.v1` facts in an `event` envelope
   to `POST .../{objective_id}/events`;
3. publish complete run-specific `firstmate.decision-events.v1` projections to
   `POST .../{objective_id}/decision-events`, using source id
   `firstmate:hosted:<opaque-execution-suffix>`;
4. poll `GET .../{objective_id}/decision-answers`, apply an answer to the exact
   lifecycle identity, and acknowledge it at
   `POST .../{objective_id}/decision-answers/ack`;
5. request GitHub authority only through
   `POST .../{objective_id}/github-credential`; and
6. emit `objective.completed` only with the existing verified completion
   evidence. Exiting without a terminal fact becomes `objective.failed`, never
   inferred completion.

Request bodies remain bounded. Structured contracts have no transcript,
terminal output, environment, or arbitrary evidence field. The worker must not
log claim, answer, bearer, or GitHub-token bytes.

## Scoped GitHub credentials

No GitHub credential is stored in an objective row or launch request. The
worker's authenticated request makes Gateway call the external broker over
mTLS:

```text
POST /v1/github/credentials
```

The Gateway first resolves exactly one repository by joining the objective's
durable project id to a current repository in an active GitHub App installation
owned by the same principal. The request carries opaque execution/tenant
identities plus that exact installation id, provider repository id, canonical
`owner/repository`, explicit operator-configured permissions, and a lifetime no
longer than the worker. An arbitrary model-supplied project alias is never
credential authority. The broker returns one repository-scoped installation
token expiring within one hour and the exact requested repository and
permissions. Gateway rejects a conflicting receipt and relays valid bytes
without persisting them. The isolation backend's host policy limits where the
worker can use the token.

## Required configuration

All values are server-only:

```dotenv
MAGISTRATE_HOSTED_EXECUTION_ENABLED=true
MAGISTRATE_WORKER_IMAGE=registry.example/firstmate-worker@sha256:<64-hex-digest>
MAGISTRATE_WORKER_GATEWAY_URL=https://gateway.internal.example
MAGISTRATE_ISOLATION_BACKEND_URL=https://isolation.internal.example
MAGISTRATE_GITHUB_TOKEN_BROKER_URL=https://github-broker.internal.example
MAGISTRATE_ISOLATION_CLIENT_CERT=/run/secrets/magistrate/client.crt
MAGISTRATE_ISOLATION_CLIENT_KEY=/run/secrets/magistrate/client.key
MAGISTRATE_ISOLATION_CA=/run/secrets/magistrate/ca.crt
MAGISTRATE_WORKER_IDENTITY_KEY=<at-least-32-random-bytes>
MAGISTRATE_WORKER_NETWORK_HOSTS=gateway.internal.example,github.com,api.github.com
MAGISTRATE_GITHUB_PERMISSIONS=contents:write,pull_requests:write
MAGISTRATE_WORKER_MAX_GLOBAL=20
MAGISTRATE_WORKER_MAX_PER_TENANT=2
MAGISTRATE_WORKER_CPU_MILLIS=2000
MAGISTRATE_WORKER_MEMORY_MIB=2048
MAGISTRATE_WORKER_WORKSPACE_MIB=8192
MAGISTRATE_WORKER_DEADLINE_SECONDS=3600
MAGISTRATE_WORKER_CLEANUP_SECONDS=300
MAGISTRATE_WORKER_POLL_SECONDS=5
```

Startup fails closed on a mutable image tag, non-HTTPS service, missing/private
key material with unsafe metadata, short identity key, host list that omits the
Gateway, unknown GitHub permission, or invalid resource/concurrency bound. The
Friend Beta issuer performs the same complete validation before treating hosted
mode as authority to omit the shared-runtime warning. It never downgrades to
the shared operator runtime.

## Activation gates

Before enabling production, security/release ownership must prove with the
selected backend (not with a mocked receipt):

- filesystem and process boundaries prevent two hostile workers from reading,
  signalling, tracing, mounting, or retaining each other's data;
- CPU, memory, process, workspace, wall-time, ingress, egress, image-digest,
  and cleanup controls are enforced under adversarial workloads;
- mTLS identities authorize only the Gateway and certificate rotation/revocation
  works without plaintext keys in logs or images;
- the network host policy denies direct internet and all hosts not explicitly
  approved for Gateway, Git, and model/provider use;
- the GitHub broker issues only mapped-repository, requested-permission,
  short-lived installation tokens and rejects cross-tenant/project reuse;
- abrupt Gateway/backend/worker termination recovers queued work without a
  duplicate execution; and
- two real tenants cannot fetch claims, decisions, events, files, processes, or
  credentials across boundaries.

PostgreSQL is the production multi-instance queue authority; all hosted store,
decision, cancellation, lease, and cleanup writes use the shared persistence
adapter. Account deletion first fences workload bearers and idempotently
cancels/deletes external executions; if cleanup is unavailable, deletion fails
closed and preserves account data for a safe retry.

Runtime/health reads report the interface as configured with
`activation: not-observed`; they never probe or start the backend. Until these
external controls pass, activation remains truthfully `BLOCKED_EXTERNAL`; this
is the only permitted production-readiness blocker in this execution slice.
