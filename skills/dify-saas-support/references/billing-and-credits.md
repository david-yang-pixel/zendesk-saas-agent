# Billing, Education, Credits, and Quota

Read when the ticket asks about education or other discounts, plan entitlement, monthly credits, purchased balance, quota/rate limits, payment, or “the UI says active but usage is blocked.” A current account snapshot and a policy document answer different questions.

## Establish the account and the two time frames

1. Identify the requester, exact workspace/tenant, reported error or missing refresh time (with timezone), and the current time. Do not assume an email belongs to only one workspace or that the ticket's label is the effective billing identity.
2. Separate the customer's policy question from their account question. For education and promotion cases, plan separate `investigation_check` entries for formal Feishu SaaS policy, relevant current public Dify guidance, and account state. Read [Evidence search](evidence-search.md) for the canonical knowledge-base entry, short terms, chat follow-ups, and full-page reads. Check both internal policy and public guidance even if DB records already explain the current plan. Treat chats and older tickets as leads rather than policy. Never infer free credits, refresh cadence, or discount duration merely from “Education,” “Professional,” or another plan name.
3. Read [Database routing](database-routing.md) before customer-specific SQL. Through read-only `dify-db`, inspect the matching `billing` subscription/education/promotion and credit-usage records plus the relevant `prod` workspace records. Resolve schema uncertainty with `information_schema`. Distinguish one-time grants, recurring allowances, purchased balances, reserved credits, consumption, expiration, and plan discounts; compare dated records rather than adding unlike balances together.

## If the customer saw a block or non-refresh

Reconstruct the relevant chain only as far as the claim requires:

```text
payment/invoice -> billing event -> subscription/plan state -> allowance or quota refresh -> runtime check/reservation -> customer error or successful use
```

- Verify what happened at the reported time and whether the workspace works now; these are separate conclusions. For a numeric credit claim, compare the matching `billing.tenant_feature_usages` quota/usage with successful `COMMIT` consumption (and applicable refunds) in `billing.quota_usage_histories`; do not double-count `RESERVE`. A `prod.tenant_credit_pools` row or grant log alone is not a remaining-balance check. None of these persisted records, nor a UI display, is by itself real-time runtime quota truth.
- Correlate payment or subscription transitions, refresh/webhook timing, credit grants and consumption, and the affected run/API error with absolute timestamps. Search `dify-signoz` narrowly by workspace, app/run/request, exact error, and UTC window if runtime rejection matters.
- If Stripe, cache/runtime quota, reservation, or an owner-only billing hop is unavailable through authorized reads, mark that hop `unknown` and name the exact record/time window needed. Do not replace it with a guessed explanation or a neighboring tenant's history.
- For a reported fix, require a matching post-change customer-path success and current effective entitlement or balance before saying “resolved.” A DB update, successful webhook, or HTTP 200 alone is insufficient.

## Reply gate

For every statement about this customer's membership, discount, grant, or credit refresh, cite the matching account records and dated policy in the internal note. In the English customer reply, state only confirmed status, what was observed at the relevant time, whether present recovery is verified, and a safe next step. Do not expose raw SQL, payment IDs, internal policy links, or unverified credit amounts.
If the customer reports zero credits while one checked record appears to show an available balance, treat that as a material contradiction. Do not declare the UI wrong or claim a numeric balance until the billing usage counter, consumption ledger, and applicable runtime/UI path are reconciled; report the remaining gap if they cannot be reconciled.
If those records or the current policy cannot be verified, do not fail silently or infer the answer from a plan label. Give the customer a concise English reply identifying the missing verification and the specific customer detail or human-support check needed. For an operator mention, write that same customer-sendable reply as the internal draft, never as a public comment.
