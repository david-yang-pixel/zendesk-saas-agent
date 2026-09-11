---
name: dify-db-investigation
description: Dify Cloud database architecture knowledge and SQL query patterns for prod, plugin, and billing databases.
---

# Dify Database Investigation

## Schema Architecture
1. **prod database**:
   - Accounts: `accounts` (`id`, `email`, `name`)
   - Tenants: `tenants` (`id`, `name`)
   - Joins: `tenant_account_joins` (`account_id`, `tenant_id`, `role`)
   - Look up workspace/tenant by email:
     `SELECT a.id, a.email, taj.tenant_id, t.name FROM accounts a JOIN tenant_account_joins taj ON a.id = taj.account_id JOIN tenants t ON taj.tenant_id = t.id WHERE a.email = '...';`

2. **plugin database**:
   - Installed plugins: `plugin_installations` (`tenant_id`, `plugin_id`, `plugin_unique_identifier`, `runtime_type`, `source`, `created_at`, `updated_at`)
   - Query plugins installed in a workspace (ordered by recent updates):
     `SELECT plugin_id, plugin_unique_identifier, runtime_type, source, created_at, updated_at FROM plugin_installations WHERE tenant_id = '<tenant_id>' ORDER BY updated_at DESC LIMIT 10;`
   - Pay close attention to `updated_at`—if a plugin was updated right around the time the user started experiencing errors, inspect that plugin version.

3. **billing database**:
   - Subscriptions, payment records, and education promotions.
   - **Subscription settings**: `tenant_subscription_settings` (`tenant_id`, `plan`, `interval`, `expiration_date`, `stripe_subscription_id`)
     * Note: `plan = 'professional'` is identical for both regular paying subscribers and education discount users. You CANNOT distinguish an education discount workspace from a regular paid workspace using `tenant_subscription_settings` alone.
   - **Education Promotion Logs (Canonical Source of Truth)**: `education_promotion_logs` (`id`, `tenant_id`, `account_id`, `valid_starts_at`, `valid_ends_at`, `stripe_promo_id`, `role`)
     * Verifies whether an education promotion is actually applied to a specific workspace and its validity period:
       ```sql
       SELECT tenant_id, account_id, valid_starts_at, valid_ends_at, stripe_promo_id
       FROM education_promotion_logs
       WHERE tenant_id = '<tenant_id>'
         AND NOW() BETWEEN valid_starts_at AND valid_ends_at;
       ```
   - **Education Identities**: `education_identities` (`account_id`, `institution`, `school_role`, `discount_claimed`, `expired_at`)
     * Proves whether a user account passed education certification and claimed the discount.
     * Note: `education_identities` alone does NOT prove that a specific workspace is currently using the education discount; always cross-reference `education_promotion_logs` by `tenant_id`.
   - **Credit Policy for Education Promotion**:
     * Workspaces under an active Education Promotion receive a one-time grant of 200 credits upon activation.
     * **There is NO monthly renewal or monthly reset of credits** for education plans (credits do NOT reset on the monthly billing cycle date).
     * Once the 200 credits are exhausted, the user must configure BYOK (Bring Your Own Key) in model provider settings to continue using models.
     * Never tell users on an education promotion that their credits will replenish next month.
