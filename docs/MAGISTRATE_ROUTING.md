# Magistrate routing authority

Authority: [production matrix](PRODUCTION_STATUS.md). Repository routing contracts:
**COMPLETE**. Real provider/harness replacement and cost evidence:
**BLOCKED_EXTERNAL**.

## Distinct authorities

1. **Conversation transport** is fixed: Chat and transcribed Voice use
   `/api/v1/magi/messages`, canonical reads/replay and `magi_messages` events.
   No terminal/Pi fallback, transport flag or worker target selector exists.
2. **Magi inference** uses `MagiModel.complete`, `RoutedMagiModel` and native
   OpenAI Responses, Anthropic Messages and Google generateContent adapters.
   Credentials are server-only. A provider-compatible URL is not an adapter.
3. **Execution harness selection** uses a separate verified inventory and
   `HarnessRoutingStrategy`. `POST /api/v1/execution/route-recommendation`
   explicitly returns `execution_started: false`; recommendations do not migrate,
   start or inspect workers. Firstmate/hosted execution own the admitted lifecycle.
4. **Speech input/output** uses foreground capture, STT and local TTS. Speech
   modes are neither model-routing policy nor a new conversation architecture.

`magi_routing.py` typed defaults or the full closed
`MAGISTRATE_MODEL_ROUTING_CONFIG` document are policy authority. The retired
single-provider selectors are not routing controls; only the transitional value
`MAGISTRATE_MAGI_MODEL_PROVIDER=routed` is accepted. See the detailed
[selection/failover contract](model-routing-cost-failover.md).

## Policy, cost and failover

Host policy closes each human turn into direct conversation, read-only
investigation, execution, a **bound** decision response, or high-impact action.
The high-impact classifier is a conservative confirmation gate, not permission
to execute; the objective executor repeats the check on validated tool arguments.
A new request with `explicit_confirmation: true` authorizes only that invocation.
Prose never binds a decision or creates standing authority.

Selection requires configured credentials, enabled/available candidates,
sufficient reasoning/context/tool/multimodal/reliability/safety capability, and
per-request, monthly and provider-allocation budget. It chooses lowest estimated
marginal cost with explicit preference/reliability/latency/stable-ID tie breaks.
Unknown capability, price or usage never means free or unlimited.

Before each paid attempt, `magi_model_routes` reserves integer micro-USD. Actual
cost requires observed token usage; uncertain billing retains the conservative
estimate. Content-free records expose provider/model, policy category, attempt,
fallback, estimated/actual/charged amounts and safe errors, not prompts or keys.
`magi_turn_closures` records one closure per canonical turn even without a call.

Retry/failover is bounded and allowed only for retryable failures **before any
tool call**. Expensive fallback requires explicit confirmation; hard budgets are
not overridable from chat. Do not silently lower capabilities or choose another
objective after a tool was observed. Operator route budgets and customer
Magistrate Credits are separate ledgers: the latter reserves/settles one execution
objective from terminal measured use. See [BILLING_MODEL.md](BILLING_MODEL.md).

## Closed tools and durable execution

`magi_chat_service.py`, `magi_firstmate_tools.py` and `firstmate_decisions.py`
own the offered closed tool set: direct response, explicit memory, objective
submission and authenticated decision answer. Unknown/ambiguous tool calls,
extra authority and oversized arguments fail closed. The host supplies owner,
conversation, canonical rows and idempotency identity. Command-less principals
cannot submit work; attachment-bearing turns deliberately receive no execution
tools. Model/private reasoning/tool envelopes never become chat prose.

`firstmate.submit_objective` persists the exact validated contract and frozen
scoped context, reserves credits, then admits deterministic local intake or the
hosted durable queue. Acceptance is not completion. Decisions accept opaque
ID/revision only; exact answer bytes come from the owner's canonical user row
with out-of-band confirmation. Hosted workers use objective-bound credentials;
local producer routes are rejected in hosted mode.

Normal reads never query an isolation backend, snapshot Herdr or reconcile
execution. Hosted write-side reconciliation is explicit lifecycle authority,
not a read side effect. Live harness replacement still needs a safe durable
handoff and verified context/evidence continuity; a recommendation or saved
preference is not proof that any running harness changed.

## Acceptance

Run `unit`, `magi-context-routing`, `execution-recovery-isolation`, `moat-hermetic`
and `client-contract`. These include the merged routing, memory, hosted and
closed-tool suites, not only the original injectable model fixture. External moat
checkpoints independently require actual provider replacement, harness
replacement, cost routing, context continuity and background autonomy. No
hermetic adapter test can satisfy them. See [CONTEXT_PLANE.md](CONTEXT_PLANE.md)
and [PRODUCTION_ACTIVATION.md](PRODUCTION_ACTIVATION.md).
