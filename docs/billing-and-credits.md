# Billing, entitlements, and Magistrate Credits

## Credit definition

A **Magistrate Credit** buys USD $0.01 of normalized provider/model/compute cost after the catalog margin. The database records integer microcredits (1 credit = 1,000,000 microcredits), never floats. For a configured provider/model rate:

1. input and output token cost are calculated from their USD-micro rates per million tokens;
2. measured compute milliseconds are calculated from the USD-micro hourly rate;
3. the rate's `margin_bps` is applied; and
4. the result is rounded up to microcredits using `usd_micros_per_credit`.

The repository-controlled default catalog is `gateway/billing_catalog.json`. It defines Free, Demo, Individual, Pro, and the 10,000 Credit Pack, plan grants, hard monthly spend and concurrency limits, warning thresholds, entitlements, reservation sizes, grace periods, and model rates. It deliberately contains `null` Stripe price IDs. No unverified live identifier is shipped.

Set `MAGISTRATE_BILLING_CATALOG_PATH` to an absolute, service-owned JSON file outside the release checkout to activate operator-specific prices/rates without committing business-account identifiers. Keep the same `magistrate.billing-catalog.v1` schema. Price values must be real `price_...` IDs created in the target Stripe account.

## Execution accounting

`firstmate.submit_objective` creates an owner-qualified reservation before it publishes to Firstmate. Available balance, entitlement, concurrent reservations, and monthly spend plus reservations are checked in one serialized database transaction (`BEGIN IMMEDIATE` on SQLite and a transaction-scoped advisory lock on PostgreSQL). A failed check publishes no task, so no worker starts. Startup intake recovery repeats the same idempotent reservation gate.

A production terminal `firstmate.execution-event.v1` must include:

```json
"usage": {
  "provider": "openai",
  "model": "gpt-4o-mini",
  "input_tokens": 1200,
  "output_tokens": 300,
  "compute_milliseconds": 4500
}
```

The exact catalog rate settles the reservation. Under-runs release the difference; over-runs create a truthful negative adjustment and can leave the account unable to start more work. Failed/cancelled work also reports measured use. Development/test compatibility permits a terminal event without usage; failed/cancelled work then releases its reservation. Production does not.

`credit_ledger` is immutable and idempotent. `execution_usage_ledger` separately retains the bounded measured provider/model/token/compute dimensions and normalized cost for each terminal event. `billing_accounts` is the transactionally updated balance projection. Reservations, releases, measured settlements, reviewed refunds, top-ups, and monthly included grants are independently typed entries. `CreditLedger.refund_objective` is an internal bounded seam with no end-user minting endpoint.

## Stripe behavior

The Gateway uses Stripe's HTTPS API directly. It creates one idempotent Stripe Customer mapping per Magistrate principal. Authenticated account scope is required for Checkout and Billing Portal creation. The client submits a catalog ID, not a Stripe price. Return URLs must match an origin in `MAGISTRATE_BILLING_RETURN_ORIGINS`.

Checkout success does **not** grant credits or entitlements. `/api/v1/billing/webhooks/stripe` verifies Stripe's `t=...,v1=...` HMAC-SHA256 signature over the exact request bytes, rejects events older than five minutes, and processes each immutable event ID once. A reused event ID with different bytes is rejected.

Handled events:

- `checkout.session.completed`: a paid, server-recorded Credit Pack checkout grants its configured credits;
- `customer.subscription.created|updated`: maps the configured price/metadata to a plan and records period/cancel state;
- `invoice.paid`: activates/renews and idempotently grants monthly included credits;
- `invoice.payment_failed`: enters catalog-configured grace; execution is disabled after grace expires;
- `customer.subscription.deleted`: returns the account to the configured free plan.

The Account Usage UI shows plan/status, spend, reservations, balance, low-credit warning, recent ledger entries, and Stripe Checkout/Portal actions. Provider quota evidence remains separately labeled.

## Exact activation runbook

Stripe/business-account activation is **BLOCKED_EXTERNAL** until an operator completes all steps:

1. In the production Stripe account, create recurring prices for Individual and Pro and a one-time price for the Credit Pack. Record Stripe's actual `price_...` values.
2. Copy `gateway/billing_catalog.json` to a root/service-owned path outside the checkout. Insert only those actual price IDs, review plan amounts/rates/margins/limits, set mode `0600`, and configure `MAGISTRATE_BILLING_CATALOG_PATH` to that absolute path.
3. Generate a restricted production Stripe secret with Customer, Checkout Session, Billing Portal Session, Subscription, and Invoice access. Set it as `STRIPE_SECRET_KEY`; never expose it through `EXPO_PUBLIC_*`.
4. In Stripe Workbench, register `https://<gateway>/api/v1/billing/webhooks/stripe` for the handled event list above. Set its signing secret as `STRIPE_WEBHOOK_SECRET`.
5. Set `MAGISTRATE_BILLING_RETURN_ORIGINS` to comma-separated exact origins, for example `https://app.example.com,magistrate://chat`. HTTPS is required except for the app scheme.
6. Configure and activate the Stripe Customer Portal in that same account, including cancellation timing and payment-method updates.
7. Restart the Gateway. It fails startup if only one Stripe secret is present or either secret has an invalid prefix. `GET /api/v1/billing/catalog` must report `activation: active`, and paid items must report `checkout_available: true`.
8. In Stripe test mode first, execute one subscription checkout, renewal invoice, payment failure/grace transition, cancellation, and Credit Pack checkout. Replay each webhook and verify `duplicate`; mutate a replay and verify rejection. Confirm balances and ledger entries in Account Usage.
9. Repeat with a low-balance account and verify objective submission returns insufficient credits and no Firstmate task/worker is created. Submit concurrent objectives at the plan cap and verify the next is rejected.
10. Back up the configured persistent database (SQLite or PostgreSQL) under the existing deployment procedure before enabling live mode. Switch the catalog and both secrets to live values together; never mix test and live identifiers.

Without steps 1–6, Free/Demo repository behavior and credit enforcement remain real, while every paid item truthfully reports externally blocked.
