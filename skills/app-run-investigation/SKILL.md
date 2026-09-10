---
name: app-run-investigation
description: Investigation of Dify application errors, workflow execution failures, and run logs using SigNoz and database queries with strict time-window correlation and plugin change attribution.
---

# App Run Investigation

## Core Principles & Investigation Hygiene

### 1. Strict Time-Window Correlation (Crucial!)
- **Only investigate telemetry and logs corresponding to the customer's reported incident time** (e.g. within ±1 hour of the ticket creation or the customer's stated timestamp).
- **NEVER correlate or cite historical errors from days or weeks ago!** If a customer reports an error today, do NOT dig up a week-old node failure and claim it is the root cause.
- If no execution logs or trace errors exist around the reported time, state truthfully that no server execution trace was captured for the current event.

### 2. Workflow "Internal Error" & Early Initiation Crashes (Plugin Attribution)
When a customer reports an immediate **"Internal Server Error"** or failure upon editing, loading, or clicking "Run" on a workflow, and NO `workflow_runs` record exists for the reported time:
1. **Root Cause Pattern**: This symptom almost always means the workflow failed *before* execution began—during canvas graph compilation or model provider schema initialization.
2. **Mandatory Check**: Query `plugin_installations` in the `plugin` database ordered by `updated_at DESC`:
   ```sql
   SELECT plugin_id, plugin_unique_identifier, runtime_type, source, created_at, updated_at 
   FROM plugin_installations 
   WHERE tenant_id = '<tenant_id>' 
   ORDER BY updated_at DESC LIMIT 5;
   ```
3. **Check for Recent Updates**: Check if any model provider plugin (e.g., Gemini, OpenAI, Claude, Bedrock) or tool plugin was installed or automatically updated within the last 24–48 hours. A buggy or breaking new plugin release is the #1 cause of sudden workflow initiation failures.
4. **Remediation**: Advise the customer to check the Plugins menu in their Dify workspace, inspect the recently updated plugin, and try downgrading/rolling back to the previous stable version or turning off auto-update.
