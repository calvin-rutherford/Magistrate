# Billing model — money and execution authority

Authority: [production matrix](PRODUCTION_STATUS.md). Repository billing/ledger:
**COMPLETE**. Stripe merchant/configuration/reconciliation: **BLOCKED_EXTERNAL**.

## Implemented contract

`gateway/app/billing.py`, `billing_api.py` and `billing_catalog.json` are the
executable authority. The catalog defines Free, Demo, Individual, Pro and a
10,000 Credit Pack, grants, concurrency/spend limits, reservations, grace periods,
rates and entitlements. All committed Stripe price IDs are deliberately null.
The catalog describes code behavior, not approved live pricing or merchant terms.

One Magistrate Credit normalizes USD $0.01 of provider/model/compute cost after
catalog margin. Amounts are integer microcredits (1 credit = 1,000,000), with
explicit rates, margin and upward rounding. `credit_ledger` is append-only and
idempotent; `billing_accounts` is its transactionally updated balance projection.
`credit_reservations` and `execution_usage_ledger` retain charge causality.
Legacy `account_credit_ledger` remains compatible historical schema, not the
current ledger. Provider `quota-axi` display is not customer credit authority.

## Admission, settlement and routing

The host binds principal/project/objective identity. Before queue publication,
`firstmate.submit_objective` reserves credits in one serialized transaction,
checking available funds, entitlements, active reservations and monthly spend.
Rejected admission publishes no task. Intake recovery repeats the same
idempotent reservation, not a second charge.

Production terminal execution events must carry exact bounded
`FirstmateMeasuredUsage`: provider, model, input/output tokens and compute
milliseconds. The configured rate settles the reservation. Under-runs release
unused reservation; over-runs create a truthful adjustment and can leave no
capacity for more work. Cancellation/failure also reports use; an unallocated
hosted cancellation uses the explicit `magistrate/no-worker` zero-use contract,
not an invented provider measurement. Development compatibility without usage is
not production permission. Reviewed refunds are bounded internal ledger entries,
not an end-user minting endpoint.

Native Chat's `magi_model_routes` is a **separate operator micro-USD budget** for
individual model attempts. Unknown usage retains a conservative charge and a
null actual. It does not debit/mint customer Credits. Execution usage settles the
customer objective once. Never add these different units together or claim a
route estimate is a paid invoice. See [MAGISTRATE_ROUTING.md](MAGISTRATE_ROUTING.md).

## Stripe and onboarding

The Gateway uses Stripe HTTPS directly. It idempotently binds one Customer to a
principal, creates Checkout/Portal sessions server-side, accepts catalog IDs from
the client and validates return origins. Browser navigation grants nothing.

`/api/v1/billing/webhooks/stripe` verifies the timestamped HMAC over exact raw
bytes, bounds input, persists event ID/digest and changes ledger/subscription
state transactionally. Changed bytes under an accepted event ID conflict;
duplicate and out-of-order deliveries cannot create extra grants. Supported
facts are paid credit-pack Checkout, subscription create/update/delete, paid
invoice renewal and failed-payment grace. There is no implemented general tax,
dispute/chargeback automation or card-data store; operators must not advertise
unsupported financial workflows.

Provider onboarding requires an active/trialing **observed subscription** with
its bound ID and signed event revision. Free credit-account initialization,
Checkout creation and cancellation back to Free do not satisfy that requirement.
`test_release_journeys.py` composes real OAuth state, catalog Checkout, signed
subscription ingress, resumed onboarding and cancellation to guard this merge
boundary. External HTTP/identity/model edges in that test are explicitly fakes.

## Activation and evidence

The merchant owner configures `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, the
private absolute `MAGISTRATE_BILLING_CATALOG_PATH` with console-generated prices,
and `MAGISTRATE_BILLING_RETURN_ORIGINS`. Exact event list, callback and account
verification are in [PRODUCTION_ACTIVATION.md](PRODUCTION_ACTIVATION.md) and the
component [billing runbook](billing-and-credits.md). Legacy
`MAGISTRATE_STRIPE_*` single-price configuration remains compatibility only.
Never mix test/live account secrets, catalog prices or webhook endpoints.

Mandatory repository suites: `billing-ledger`, `spencer-hermetic`,
`execution-recovery-isolation`, `migrations`, `backup-restore`,
`postgres-persistence`. The restore fixture preserves actual credit/reservation/
usage rows with the rest of the domain state. Actual test-mode subscription,
renewal/failure/cancellation/top-up, replay, budget denial and ledger/provider
reconciliation remain live checkpoints. A synthetic signature is not Stripe
activation. Legal retention/refunds/tax and Apple's actual purchase-policy
approval are operator decisions, not inferred from passing tests.
