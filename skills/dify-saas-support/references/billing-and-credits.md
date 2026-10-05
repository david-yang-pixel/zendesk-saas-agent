# Billing, Education, Credits, and Quota

Read when the ticket asks about education or other discounts, plan entitlement, monthly credits, purchased balance, quota/rate limits, payment, or “the UI says active but usage is blocked.” A known policy answers what a plan grants; account records answer which plan this workspace is on and what it used.

## Known current policy: Dify for Education

Checked against the public page on 2026-10-05: <https://docs.dify.ai/en/cloud/use-dify/workspace/subscription-management#dify-for-education> (Chinese: `/zh/cloud/...`, Japanese: `/ja/cloud/...`, same path).

- Verified students, teachers, and school staff can use the **Yearly Professional plan for free**, for one workspace per verification. Re-verification is required every year. The education subscription does not auto-renew; when it ends, the workspace returns to the free Sandbox plan.
- An education workspace has all Professional features and resource quotas **except AI credits, which stay at the Sandbox level: 200 one-time credits for built-in models, with no monthly refill.** When they run out, the customer configures their own model provider API key under **Integrations → Model Provider** (<https://docs.dify.ai/en/cloud/use-dify/workspace/model-providers>).
- The discount applies only to the **Yearly** plan. A Monthly Professional subscription is charged full price, and the discount cannot be applied to an existing paid plan: cancel, let the paid period end, then subscribe to the Yearly plan with the discount.

Apply it directly:

- `plan = 'professional'` looks the same for paid and education workspaces. Check `billing.education_promotion_logs` by `tenant_id` (active when the current time is between `valid_starts_at` and `valid_ends_at`); `education_identities` shows only that the account was verified.
- If the workspace has an active education promotion, say so plainly: its AI credits are the one-time 200, they do not refresh monthly, and a skipped monthly reload (for example `skip_reason: active_education`) is the expected behaviour, not a fault. Give the API-key next step with the public link.
- This policy is already verified, so you do not need to find it again in Feishu for each ticket. Search Feishu only when the account records contradict it (a legacy cohort, a manual grant, an exception) or the question is about something this section does not cover.

## Establish the account and the two time frames

1. Identify the requester, exact workspace/tenant, reported error or missing refresh time (with timezone), and the current time. Do not assume an email belongs to only one workspace or that the ticket's label is the effective billing identity. If the requester owns several workspaces and the question does not say which, check them all and answer for the one that matches the question, rather than asking the customer first.
2. Separate the customer's policy question from their account question. Use the known policy above when it applies. For other discounts, promotions, or policies, read [Evidence search](evidence-search.md) for the canonical knowledge-base entry and current public guidance. Treat chats and older tickets as leads rather than policy.
3. Read [Database routing](database-routing.md) before customer-specific SQL. Through read-only `dify-db`, inspect the matching `billing` subscription/education/promotion and credit-usage records plus the relevant `prod` workspace records. Resolve schema uncertainty with `information_schema`. Distinguish one-time grants, recurring allowances, purchased balances, reserved credits, consumption, expiration, and plan discounts; compare dated records rather than adding unlike balances together.

## If the customer saw a block or non-refresh

Reconstruct the relevant chain only as far as the claim requires:

```text
payment/invoice -> billing event -> subscription/plan state -> allowance or quota refresh -> runtime check/reservation -> customer error or successful use
```

- For a numeric credit claim, compare the matching `billing.tenant_feature_usages` quota/usage with successful `COMMIT` consumption (and applicable refunds) in `billing.quota_usage_histories`; do not double-count `RESERVE`. A `prod.tenant_credit_pools` row or grant log alone is not a remaining-balance check.
- Correlate payment or subscription transitions, refresh/webhook timing, credit grants and consumption, and the affected run/API error with absolute timestamps. Search `dify-signoz` narrowly by workspace, app/run/request, exact error, and UTC window if runtime rejection matters.
- If Stripe, cache/runtime quota, reservation, or an owner-only billing hop is unavailable through authorized reads, name that hop in the internal note. Do not replace it with a guessed explanation or a neighboring tenant's history.
- For a reported fix, require a matching post-change customer-path success before saying “resolved.” A DB update, successful webhook, or HTTP 200 alone is insufficient.

## Reply gate

Lead with the answer: which plan the workspace is on, what that plan grants, and why the customer sees what they see. State the known policy and the matching account records plainly; cite the public documentation link, never internal policy links, raw SQL, or payment IDs.
Only a genuine contradiction needs a qualification: for example the customer reports zero credits while the usage counter shows a balance. Then state what the records show and the one check still needed. If a billing correction, refund, or manual credit grant is required, say once that a human will handle it; do not promise the outcome.
