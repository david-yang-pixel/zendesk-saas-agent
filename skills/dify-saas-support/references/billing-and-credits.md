# Billing, Education, Credits, and Quota

Read when the ticket asks about education or other discounts, plan entitlement, monthly credits, purchased balance, quota/rate limits, payment, or “the UI says active but usage is blocked.” A current account snapshot and a policy document answer different questions.

## Establish the account and the two time frames

1. Identify the requester, exact workspace/tenant, reported error or missing refresh time (with timezone), and the current time. Do not assume an email belongs to only one workspace or that the ticket's label is the effective billing identity.
2. Separate the customer's policy question from their account question. For education and promotion cases, check the formal Feishu SaaS knowledge base/current dated runbook *and* the relevant current public Dify page. Treat chats and older tickets as leads rather than policy. Never infer free credits, refresh cadence, or discount duration merely from “Education,” “Professional,” or another plan name.
3. Through read-only `dify-db`, inspect the matching `billing` subscription/education/promotion/payment state and `prod` workspace/credit/usage state as needed. Resolve schema uncertainty with `information_schema`. Distinguish one-time grants, recurring allowances, purchased balances, reserved credits, consumption, expiration, and plan discounts; compare dated records rather than adding unlike balances together.

## If the customer saw a block or non-refresh

Reconstruct the relevant chain only as far as the claim requires:

```text
payment/invoice -> billing event -> subscription/plan state -> allowance or quota refresh -> runtime check/reservation -> customer error or successful use
```

- Verify what happened at the reported time and whether the workspace works now; these are separate conclusions. A present `tenant_feature_usages.usage` value, credit-pool row, plan label, or UI display is not by itself real-time runtime quota truth.
- Correlate payment or subscription transitions, refresh/webhook timing, credit grants and consumption, and the affected run/API error with absolute timestamps. Search `dify-signoz` narrowly by workspace, app/run/request, exact error, and UTC window if runtime rejection matters.
- If Stripe, cache/runtime quota, reservation, or an owner-only billing hop is unavailable through authorized reads, mark that hop `unknown` and name the exact record/time window needed. Do not replace it with a guessed explanation or a neighboring tenant's history.
- For a reported fix, require a matching post-change customer-path success and current effective entitlement or balance before saying “resolved.” A DB update, successful webhook, or HTTP 200 alone is insufficient.

## Reply gate

For every statement about this customer's membership, discount, grant, or credit refresh, cite the matching account records and dated policy in the internal note. In the English customer reply, state only confirmed status, what was observed at the relevant time, whether present recovery is verified, and a safe next step. Do not expose raw SQL, payment IDs, internal policy links, or unverified credit amounts.
If those records or the current policy cannot be verified, do not fail silently or infer the answer from a plan label. Give the customer a concise English reply identifying the missing verification and the specific customer detail or human-support check needed. For an operator mention, write that same customer-sendable reply as the internal draft, never as a public comment.
