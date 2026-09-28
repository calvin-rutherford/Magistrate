# Model, cost, and harness routing

## Authority and boundaries

The typed defaults in `gateway/app/magi_routing.py` are the repository authority
for Native Magi model routing. Deployments may replace the whole document with
`MAGISTRATE_MODEL_ROUTING_CONFIG`; it is validated as a closed v1 contract at
startup and request time. A malformed catalog, unknown non-custom provider,
negative price, impossible capability, or incomplete policy fails closed.
There is no provider alias and no OpenAI-compatible assumption for other
vendors.

The provider-independent boundary remains `gateway/app/magi_model.py`.
Concrete wire adapters are:

- OpenAI Responses: `OpenAIMagiModel`;
- Anthropic Messages: `AnthropicMagiModel`;
- Google `generateContent`: `GoogleMagiModel`.

All implement the same complete-message and closed-tool contract. Provider
reasoning/private blocks and error bodies never become conversation text.
`RoutedMagiModel` accepts injected providers and factories, so adding or
replacing a provider does not change chat orchestration. Credentials come only
from each catalog entry's named server environment variable and are never
stored in route records.

## Turn categories and authority

The host closes each native request to one of:

- `DIRECT_CONVERSATION`;
- `READ_ONLY_INVESTIGATION`;
- `EXECUTION`;
- `DECISION_RESPONSE` (only with an out-of-band canonical decision binding);
- `HIGH_IMPACT_ACTION`.

Free-form text cannot bind a decision. Command-less principals get no objective
tool and therefore remain direct conversation. Production/data/credential/
spend/release operations classified as high impact do not make a provider call
or submit an objective until a new idempotent message carries
`explicit_confirmation: true`. The closed objective executor repeats this check
against the validated model-authored objective and acceptance criteria before
claiming or publishing anything, so a benign-looking prompt or hallucinated
escalation cannot bypass it. Confirmation authorizes that request only; it is
not inferred from prose and is not retained as conversational standing
authority.

The existing closed `firstmate.submit_objective` tool remains the sole Native
Chat execution edge. Its principal and idempotency key are host supplied. The
router never retries or falls back after observing any tool call, so provider
failover cannot select a second objective. Objective-store and tasks-axi
idempotency remain the final exactly-once acceptance boundary.

## Selection

For each paid completion, the router first excludes models that are not:

1. enabled, statically available, and credentialed;
2. sufficiently reliable and safe for the category;
3. large enough for estimated complete context plus the configured output
   reservation;
4. capable of required tools and multimodal input;
5. inside the configured provider/model credit allocation (decremented by the
   conservative route ledger), per-request budget, and monthly reservation
   budget.

It then selects the lowest marginal estimated cost. Subscription models have
zero marginal route cost but are still metered at their configured reference
price. Explicit model/provider preference breaks cost-equivalent choices;
observed success history (conservatively capped by configured reliability),
then configured latency and stable route id break remaining ties. Unknown
capability, price, credential, or actual usage is never treated as zero.

Prices are explicit USD per million tokens in the catalog. They are operational
configuration, not a live provider-price claim; operators must review them when
provider pricing changes. Route estimates reserve the configured output-token
ceiling. Actual cost is recorded only from provider-reported token usage. If a
provider omits usage or a response is interrupted after possible billing, the
actual remains `null` and the estimate remains charged against the budget.

## Retry and failover

The default policy performs no same-model retry. A deployment may permit at
most two. Retry/fallback is allowed only for retryable provider failures and
never after a tool call. Candidates in the selected capability class precede
other classes; alternate providers use their native adapter.

Before every attempt, the shared persistence transaction (`BEGIN IMMEDIATE` on
SQLite, translated to a transaction-scoped lock on PostgreSQL) reserves integer
micro-USD in `magi_model_routes`. A monthly or per-request limit blocks the call
before any provider traffic. A fallback above `max_automatic_fallback_cost_usd` pauses with
`route_fallback_confirmation_required`; retrying the saved message with
explicit confirmation authorizes that material fallback. Hard budget exhaustion
is never overridable from chat. There is no silent downgrade of reasoning,
tool, context, multimodal, reliability, or safety requirements.

Authenticated, principal-scoped `GET /api/v1/magi/model-routes` returns up to
100 content-free paid-call records: category, selected provider/model/capability
class, attempt/fallback edge, estimate, usage-derived actual at configured prices,
conservative budget charge, token counts, status, and safe error code. Its `turn_closures`
section records exactly one policy category/outcome per canonical human turn,
including confirmation-required, failed, and cancelled turns that made no paid
call. Neither ledger stores prompts, responses, tool arguments, credentials, or
chain-of-thought.

This operational model-cost ledger and the customer credit ledger have distinct,
non-overlapping authority. `magi_model_routes` meters each Native Chat provider
call and enforces the operator's model-routing USD budgets. `gateway/app/billing.py`
reserves and settles customer Magistrate Credits once for the resulting execution
objective, using terminal execution usage and the billing catalog. A catalog
model's `credits_remaining_usd` is an operator provider allocation, not a customer
credit balance; routing never mints, debits, or reports customer credits. See
`docs/billing-and-credits.md` for that accounting contract.

## Execution harness strategy

`gateway/app/execution_routing.py` is a separate, replaceable strategy for
verified execution profiles. Optional closed `routing` metadata on a
`MAGISTRATE_EXECUTION_INVENTORY` model supplies reasoning, context, tools,
multimodal, reliability, latency, and estimated cost. Missing metadata is
unknown and cannot be automatically selected.

`POST /api/v1/execution/route-recommendation` requires `command`, applies the
cheapest-reliably-capable strategy, and returns `execution_started: false`.
This route is process-free: it does not inspect, start, stop, migrate, or signal
a harness. Firstmate remains the explicit launch consumer. A different
`HarnessRoutingStrategy` can replace selection without changing inventory or
lifecycle authority.

## Configuration checklist

1. Set only the credentials for providers that may be selected:
   `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and/or `GOOGLE_API_KEY` by default.
2. Review model availability, capabilities, reliability, latency, safety tier,
   plan/credits, and token prices in the typed default or override catalog.
3. Set monthly, per-request, output-reservation, retry, reliability, and
   automatic-fallback limits.
4. If using an inline catalog, validate it in a staging startup before
   production. Do not retain the retired `MAGISTRATE_MAGI_MODEL_PROVIDER`
   single-provider selector; an explicit `routed` value is accepted only for
   migration clarity.
5. Give execution inventory profiles complete `routing` metadata before asking
   for automatic recommendations.

The deterministic coverage is in `gateway/tests/test_magi_routing.py`; existing
Native Chat tool tests continue to prove one objective per accepted request.
