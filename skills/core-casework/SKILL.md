---
name: core-casework
description: Investigate Dify SaaS Zendesk cases from the latest customer thread, correlate current evidence, and separate verified findings from unknowns before producing a public reply.
---

# Core Casework Investigation

1. Refresh the latest Zendesk conversation and relevant attachments before non-Zendesk investigation. Work from all consecutive unreplied customer events, not a stale summary.
2. Establish the exact object, identifiers, incident time and timezone, expected behavior, observed behavior, and the customer's current question. Do not reuse an older incident merely because the ticket number matches.
3. Maintain a small evidence plan. Use a service only when it can prove or rule out a material hop, claim, recovery path, or current-state question.
4. Label material findings as:
   - `confirmed`: directly shown by current ticket, database, telemetry, or executed-path evidence.
   - `evidence-supported`: multiple facts support the conclusion, but direct execution proof is incomplete.
   - `ruled out`: relevant current evidence contradicts the candidate.
   - `unknown`: required evidence is missing, unavailable, or owner-only.
5. Treat source code, documentation, an Issue/PR status, deployment report, outer HTTP 200, or an old snapshot as supporting context, not proof that the customer's current path recovered.
6. Complete relevant pagination. Record any deliberate bound, cursor, interruption reason, or unavailable route in the trace-only internal section.
7. The customer reply must contain only verified customer-safe facts, safe next steps, and the smallest useful request for missing information. Never expose raw SQL, logs, private identifiers, internal links, or speculative causes.
