---
name: app-run-investigation
description: Investigate Dify Cloud application and workflow failures by correlating persisted runs, current telemetry, plugin state, and deployed source within the reported incident window.
---

# App Run Investigation

## Core Principles & Investigation Hygiene

### 1. Time and object correlation

- Use the customer's reported time and timezone, request ID, app/workflow ID, run ID, node ID, and workspace ID where available.
- Start with a narrow window around the event and expand only when the evidence path requires it. Never substitute unrelated historical errors.
- Distinguish no persisted run, no collected telemetry, and a successful run with a failed outer response; they are different findings.

### 2. Early failures and plugin attribution

When no matching run was persisted, investigate pre-run paths such as graph validation, provider initialization, plugin resolution, and request admission. Recent plugin activity is only a candidate:

- Compare the installed plugin version and update time with the incident window.
- Match a relevant runtime error, source branch, known defect, or controlled recovery result before naming the plugin as the cause.
- A temporal correlation alone is not proof. If evidence remains incomplete, report the result as evidence-supported or unknown and request the smallest reproducible artifact.

### 3. Recovery gate

Do not report recovery from a PR merge, deployment note, successful queue admission, or unrelated later run. Prefer a matching successful customer-path run, current telemetry plus persisted final state, or explicit customer confirmation.
