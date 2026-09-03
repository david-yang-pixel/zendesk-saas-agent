---
name: app-run-investigation
description: Investigation of Dify application errors, workflow execution failures, and run logs using SigNoz and database queries.
---

# App Run Investigation

## Workflow
1. Identify the application ID, run ID, or tenant ID from the customer ticket.
2. Use `sz_run` or `signoz_search_logs` to inspect backend trace logs and error stacks.
3. Use `db_gateway_query` to inspect app configurations or workflow run states.
4. Synthesize root cause and provide actionable solutions or workarounds.
