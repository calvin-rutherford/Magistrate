# Magistrate routing authority

Authority: [production matrix](PRODUCTION_STATUS.md). **Closed native routing:
COMPLETE. Production cost/provider/harness routing: FAILED.**

## Three distinct decisions

1. **Human conversation transport** is fixed: Chat and transcribed Voice call
   `/api/v1/magi/messages`, read `/api/v1/magi/conversations/*`, and reconcile
   `magi_messages` events. There are no runtime transport flags or selectable
   worker targets. Old deployment documents mentioning a native/legacy toggle
   do not authorize reintroducing one.
2. **Magi inference** uses the `MagiModel.complete` boundary. Baseline has one
   concrete adapter, `OpenAIMagiModel`, using non-streamed Responses. Provider,
   model, endpoint, reasoning effort, timeout and maximum output are server
   configuration. An unknown configured provider fails instead of falling back
   to a terminal or another credential.
3. **Execution routing** is a validated harness/provider/model/variant inventory
   and saved preference, separate from inference and speech input mode. It is
   not proof that a worker migrated or that a provider key is available. Firstmate
   owns scheduling and lifecycle; Gateway only accepts and projects contracts.

## Closed model tool contract

`magi_chat_service.py` and `magi_firstmate_tools.py` own the tool turn. A
command-authorized routing call must choose an offered closed tool. Ordinary
conversation selects `magi.respond`; actionable work uses
`firstmate.submit_objective`. Pending authenticated decision context may offer
`firstmate.answer_decision`. Unknown names, extra authority, malformed/oversized
arguments and ambiguous calls fail closed. Natural-language keyword classifiers
are not routing authority. Provider-private reasoning and tool protocol bytes
never become user-visible chat prose.

The host, not model arguments, binds principal, conversation, user row, assistant
row and invocation identity. Objective arguments are bounded objective/project/
constraints/acceptance criteria/opaque context references. The queue publishes a
deterministic task and wakes once; `accepted` means intake accepted, not work
completed. Decision arguments contain only opaque `decision_id` and
`decision_revision`. Exact answer bytes come from the owner's canonical user
message with bound confirmation, never a model rewrite or terminal transcript.

A voice-only principal may chat but is not offered execution tools. Generic
`command` remains broader than isolated chat in the baseline. Tenant/entitlement
policy must be added before ordinary SaaS users receive shared runtime access.

## Production routing requirements (A7 with A4/A5/A6)

- Resolve authenticated principal/project and verified provider capabilities;
  apply explicit policy, tenant entitlement and budget before dispatch.
- Keep provider, model, execution harness and variant distinct. Never treat a
  display label or saved preference as actual activation.
- Record a durable policy/version/selection and reservation identity sufficient
  for deterministic retry and cost reconciliation, without logging prompts or
  keys. Provider usage may arrive late; unknown usage is not zero cost.
- Permit replacement only through reviewed adapters and safe execution handoff.
  Preserve canonical conversation/context/objective identities. A fallback cannot
  widen authority, data egress, budget or change an already accepted invocation.
- Bound retries and tool rounds. Do not replay non-idempotent execution because a
  model socket timed out. Preserve an explicit failure when delivery is uncertain.
- Test cheap/default/escalated routes, unavailable capability, budget denial,
  ambiguous usage, wrong-tenant credentials, replacement continuity and explicit
  operator migration confirmation. Do not fabricate savings from token estimates.

No cost policy, second concrete Native Magi provider, automatic live worker
migration, or cross-harness context portability is proved by baseline preferences.
The new synthetic replacement test proves only the injectable provider seam.

## Acceptance

Run registry suites `magi-context-routing`, `unit`, `execution-recovery-isolation`,
`moat-hermetic`, then the **live** moat provider-replacement, harness-replacement,
cost-routing and context-continuity checkpoints. These are distinct evidence
classes. The current provider's successful response cannot pass another
provider's replacement test. See [CONTEXT_PLANE.md](CONTEXT_PLANE.md) and
[BILLING_MODEL.md](BILLING_MODEL.md).
