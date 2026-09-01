---
name: dify-ticket-investigation
description: Evidence-driven investigation workflow for Dify Cloud Zendesk tickets. Use for every non-trivial case before deciding that the customer's question is resolved, especially runtime failures, stuck asynchronous work, retrieval, provider, workflow, quota, and billing discrepancies.
---

# Dify Ticket Investigation

Investigate the customer's actual question, not merely a nearby fact that is easy
to verify. Treat the ticket form as identity and routing input; treat end-user
conversation messages as the problem statement. Dify-authored greetings, agent
notes, tags, and plan labels are not customer evidence.

## Required workflow

1. Restate the active customer question internally in one sentence.
2. Select the matching investigation scenario supplied with this skill.
3. Draw the real execution chain for that scenario before calling tools.
4. Resolve the workspace from trusted form/requester identity. Never ask for a
   workspace ID when code has already resolved one.
5. Build an evidence ledger with separate entries for target, current state,
   failing boundary, impact, root cause, and recovery.
6. Investigate each required hop in order. Use exact object IDs returned by a
   trusted read-only result to narrow later reads, even when the object is not
   in the workspace initially resolved from the ticket.
7. Re-read the evidence against the active question before returning terminal
   JSON. A true but adjacent fact is not an answer.

## Evidence rules

- Current DB state proves persistence state, not that a worker received a task.
- API acceptance proves enqueue intent, not worker execution or completion.
- Absence of `ERROR` or `FATAL` logs is not proof of health.
- A quota with remaining capacity only excludes one possible blocker.
- Historical tickets, docs, and source explain expected behavior; they do not
  prove the current customer path.
- When a fixed tool cannot answer the question, use `sql_query` or
  `signoz_query`. They retain full read-only query expressiveness. Prefer exact
  identifiers and bounded time windows, and never use unrelated customer data
  as evidence for the current ticket.
- Root cause requires both a target-bound failing boundary and an aligned
  mechanism. Otherwise report the bounded boundary and keep root cause unknown.
- A retry or workaround is recovery evidence only after the affected path is
  observed to progress or complete.

## Stop conditions

Do not mark a runtime failure resolved until current state and the first failing
boundary are evidenced. If the customer asks why, add a verified mechanism or
state explicitly that the deeper root cause remains unknown.

When evidence is unavailable, narrow or retry the authoritative read. If the
boundary remains inaccessible, return an insufficient result or a typed action
proposal with completed checks and a precise unresolved action. Never replace
missing evidence with a generic reassurance, a quota check, or a broad log search.

## Output

Return customer-facing claims only in concise English. Lead with the answer to the
active question, then current impact, recovery/next step, and any bounded
limitation. Never expose tenant IDs, document IDs, traces, source paths, internal
tools, infrastructure names, or raw evidence.
