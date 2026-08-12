# Workflow, retrieval, and provider runtime failures

Build the path before searching:

- Workflow: `request -> workflow run -> node execution -> provider/tool -> output`
- Retrieval: `chat -> retrieval -> embedding -> vector search -> LLM -> response`
- Provider: `configured provider path -> credential/quota -> invocation -> response`

Start from an exact request, run, message, conversation, invocation, dataset, or
trace identifier whenever one is available. Locate the earliest observed failing
hop. Separate configured custom-provider success from recovery of the
Dify-managed path. Do not infer recovery from HTTP 200, active metadata, or a
different successful path.

For a current failure, a product explanation alone is insufficient. Require a
target-bound current-state claim and a target-bound failing-boundary claim before
returning an answerable result.
