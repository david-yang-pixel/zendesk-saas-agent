# Dify SaaS Database Routing

Read only when a ticket's answer depends on customer-specific persisted state. This is a route map, not a current schema or proof that a value is effective in the UI/runtime. Select only the rows relevant to the customer's question.

Use OpenConnector `custom_mcp` connection `dify-db`, remote tool `db_gateway_query`, with separate `database` (`prod`, `billing`, or `plugin`) and `sql` fields. Query only with bounded read-only `SELECT`/`WITH`; pass the ticket ID for audit when available. Confirm the live tool schema and use `information_schema` before relying on unfamiliar tables or columns. Do not assume `prod.table_name` selects a database: choose the `database` input explicitly.

| Question | Database and candidate tables | What to verify |
| --- | --- | --- |
| Which account and workspace? | `prod`: `accounts` → `tenant_account_joins` → `tenants` | Resolve the requester's account ID, every matching tenant ID, membership/role, and the workspace the customer actually used. Email or a ticket plan tag alone does not select the affected tenant. |
| Current subscription or education promotion? | `billing`: `tenant_subscription_settings`, `tenant_stripe_status`, `education_identities`, `education_promotion_logs` | Match tenant/account and effective dates. A plan or historical promotion row is not proof of a current paid subscription, renewal state, or credit balance; use Stripe evidence only when available and relevant. |
| Credit grant, balance, or consumption? | `billing`: `tenant_feature_usages`, `quota_usage_histories`, `tenant_message_credit_logs`; `prod`: `tenant_credit_pools` | Separate the persisted quota/usage counter, successful consumption, grant history, and auxiliary pool record. Follow the credit reconciliation below before stating a numeric balance. |
| App/workflow execution? | `prod`: `apps`, `workflow_runs`, `workflow_node_executions` and other schema-confirmed records for the affected object | Match tenant, app/run/node IDs and time. A persisted run status does not prove a customer-visible result or the cause of a runtime failure; use matching traces/logs when necessary. |
| Installed plugin/provider package? | `plugin`: `plugin_installations` for the tenant's installed identifier/version; other tables only after schema discovery | Distinguish installed package from Marketplace latest and runtime behavior. Check the matching `dify-signoz`/source path when explaining an invocation failure. |

## Credit reconciliation when a customer reports zero, missing refresh, or unexpected use

1. Resolve the exact tenant first. In `billing.tenant_feature_usages`, inspect the relevant `feature_key` and `bucket`, with quota, usage, and `updated_at`. Do not combine trial, paid, reserved, purchased, and promotion balances without confirming their meanings.
2. For claimed consumption, inspect matching `billing.quota_usage_histories` over the relevant period. Count successful `COMMIT` consumption and applicable refunds/reversals; `RESERVE` is not final usage and must not be added again. Group by event type, bucket, time, and available source metadata so an app invocation is not confused with knowledge indexing or another product path.
3. Treat `billing.tenant_message_credit_logs` as grant/event history and `prod.tenant_credit_pools` as a separate persisted pool record, not as a standalone live available-balance answer. Check dated records and any mismatch against the billing counter and ledger. A new-grant row showing `quota_used=0` does not cancel later successful consumption.
4. If the customer's displayed amount, two database records, or the ledger disagree, report the contradiction in the internal analysis and do **not** assert a numeric available balance to the customer. Check the applicable runtime quota/UI path if identifiers and a bounded time window permit; otherwise say what remains unverified and request the minimum missing detail or human verification.

The billing counter and ledger are persisted evidence, not guaranteed real-time Redis/runtime quota truth. A claim that an app was blocked, recovered, or mischarged needs the matching request/run, error or screenshot, time window, and runtime evidence when available. The education policy and official docs are separate evidence for what should be offered; neither proves this tenant's consumption.
