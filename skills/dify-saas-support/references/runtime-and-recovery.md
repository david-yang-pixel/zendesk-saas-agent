# Runtime Failure and Recovery Investigation

Read when the ticket reports a failing or slow request, workflow, dataset, plugin/model provider, intermittent 429/5xx, queue delay, regression, or an alleged fix. Start with the affected customer's execution path rather than broad searches.

## Standard route: one affected object

1. Establish the latest symptom, exact action and expected result, error text, UTC window, workspace, app/workflow/dataset, run/request/trace ID, installed plugin/provider version, and preview versus published path where relevant. Use the Worker-prepared conversation and attachments; read older Zendesk pages only when needed.
2. Reconstruct the entry-to-failure chain:
   - **`dify-signoz` precision log queries**:
     - Convert the customer's reported local incident timestamp to UTC.
     - Set `timeRange` to a tight window (e.g. `1h` or `3h` around the incident) instead of broad `1d` or `7d`.
     - Combine user/workspace identifiers with status codes (`403`, `429`, `500`) or specific paths (`/api/v1/auth`, `/api/v1/workflows`) to isolate gateway or application errors.
   - **Cloudflare / Edge / Cloud Browser challenges**:
     - In production, `cloudflare_mcp` has no read log capability.
     - Understand that Cloudflare Bot Management challenges datacenter IP addresses and headless/automated browser sessions by design.
     - **Constructive guidance**: When a customer's cloud browser or automation environment is challenged, do not merely deflect to third-party support. Explain the datacenter IP challenge mechanism, and provide viable Dify workarounds (e.g., using Dify API keys directly for workflow execution, exporting/importing DSL via desktop browsers, or using dedicated IP options).
   - **`dify-db`**: Check persisted state (run records, tenant configurations, credentials).
3. Keep an internal evidence ledger for each material claim: causal role (`symptom`, `failing boundary`, `trigger`, `root cause`, `contributor`, `recovery event`, or `current state`), confidence, object/path/version, time window, source, exact observation, and alternative explanation. A confirmed failing boundary is not automatically a confirmed root cause.

## Deep route: intermittent, slow, shared, or changing symptom

- Anchor one matching customer failure first. Then compare the same signal across nearby runs, deployments, and tenants with a comparable baseline before saying “platform-wide.” Counts and correlation establish scope, not cause.
- For latency and timeouts, trace the slow hop first; then check the matching service's logs and capacity/dependency metrics. For asynchronous paths, connect producer and consumer with a message/task/trace identifier and timestamps.
- For a plugin/provider issue, verify the workspace-installed version, relevant daemon/connector logs, downstream provider boundary, and aligned source. Do not generalize one installed plugin's behavior to all Dify users.
- Use Cloudflare reads only when edge/CDN/WAF/Worker evidence is implicated. Use GitHub, Linear, or Feishu engineering discussion only to investigate a plausible fix; distinguish proposal, merged PR, release, relevant deployment, and customer-path recovery.
- Sanitized Notion experience can suggest a next check after an independent live pass; it cannot prove this ticket's current cause or recovery.
- An empty search is not proof of absence until service/deployment, identifier, UTC conversion, field, signal freshness, and retention are checked. Narrow first; widen only to resolve a material uncertainty.

## Recovery and stop gate

For each feasible customer-safe action, identify prerequisites, the failing mechanism it might address, expected result, validation path, observed result, and reversibility/data risk. Classify the evidence as `code-derived`, `isolated reproduction`, `matching customer-path`, or `current recovery confirmed`. Do not recommend destructive changes, credential rotation, downgrade, or data deletion from a hypothesis alone.

Stop when the material causal and current-state claims are supported, or when the exact remaining unavailable/owner-only hop is identified with object, UTC window, known facts, and a precise request for that evidence. Do not keep calling unrelated tools to fill time; do not claim recovery from an HTTP 200, a merged PR, a deployment, or another tenant's success.
