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
   - Subscriptions and billing records.
