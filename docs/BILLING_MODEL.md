# Billing model — authority and integration contract

Authority: [production matrix](PRODUCTION_STATUS.md). **Billing implementation:
FAILED. Stripe account activation: BLOCKED_EXTERNAL.**

## What exists, and what does not

`gateway/app/usage.py` projects read-only `quota-axi` observations. A provider's
remaining quota, plan label or nullable usage window is not a customer balance,
invoice, entitlement, credit ledger or settled payment. The baseline has no
Stripe SDK/route, signed webhook ingress, customer/subscription mapping, charge
ledger, reservation or budget admission. There are no working Stripe environment
variables in this candidate. Do not treat an unavailable quota as zero cost or
an unlimited account.

A4 owns billing/ledger code and tests; A1 owns customer-to-principal/tenant
identity; A7 owns observed model usage and routing admission; A5 owns execution
charge causality; A11 reviews signature/secret/authorization boundaries. A12 owns
composition and release evidence, not pricing or merchant legal choices.

## Shared production requirements

1. **Owner binding:** server-derived principal/tenant maps to the approved
   customer account. A client-supplied Stripe customer/payment ID cannot select
   another tenant's credit or grant entitlement.
2. **Amounts:** explicit currency/unit and integer amounts with documented
   rounding/precision; never floating-point money. Pricing/rates, credits versus
   currency, taxes, subscription/prepaid rules and refund policy need owner
   approval and versioned terms. This document does not select them.
3. **Payments:** verify signature against the exact raw body, enforce bounded
   payload/time policy, persist provider event identity and immutable facts
   before applying effects. Duplicate delivery is idempotent; conflicting facts
   and out-of-order transitions cannot create credit. Client return URLs are not
   payment proof.
4. **Ledger:** append-only auditable entries with causality/idempotency keys and
   explicit corrections/reversals. Balance and entitlement derive from accepted
   facts, not a writable client balance or a mutable display counter. Separate
   test/live accounts and identifiers.
5. **Admission:** reserve permitted budget before a chargeable call/run, then
   settle observed usage or release/refund according to durable outcome. Concurrent
   calls cannot overspend the same available credit. Provider or process timeout
   cannot charge twice or silently mark unknown usage as free.
6. **Recovery:** bind request/objective/provider usage/payment-event identities;
   replay after restart converges to the same reservation/settlement. Handle late
   usage, cancellation, failed dispatch, duplicate completion, refund and any
   supported dispute/subscription lifecycle with explicit states.
7. **Reconciliation:** compare ledger to authoritative provider/Stripe event
   records, record discrepancies without inventing values, alert an owner and
   expose honest user-visible states. Retention/financial-record deletion must
   match the approved legal policy.

These are acceptance requirements, not fabricated implemented table or endpoint
names. A4 must publish its actual schema/version, units, webhook event set,
callback/portal URLs, variables, and migration/backfill order. A12 composes those
with tenant/context/execution changes and seeds restore fixtures. Never store
card data or put Stripe API/signing secrets in the frontend.

## Executable gate and external acceptance

```sh
mkdir -p .release
python3 scripts/production_acceptance.py run --suite billing-ledger --output .release/billing.json
```

This exits nonzero with `FAILED: billing-ledger` on the baseline because no
implementation suite is registered. It is not a skipped test or a fake Stripe
success. After A4 merges, replace the registry gap with its real commands and add
cross-domain tests for two tenants, concurrent budgets, webhook replay/conflict,
settlement after restart and backup/restore. Unit mocks cannot pass the separate
`activation/stripe-reconciliation`, Spencer billing-budget or moat cost-routing
live checkpoints.

The merchant owner uses the actual Stripe console/account, creates approved
products/prices and secrets, and configures the **merged** routes as described in
[PRODUCTION_ACTIVATION.md](PRODUCTION_ACTIVATION.md). Record real generated IDs;
there are no invented prices or legal entity defaults. Live charging is not
authorized by a passing test-mode fixture. Apple purchase-policy review remains
required for the actual distributed mobile billing UX.
