# Engineering Status and Prior Experience

Adapted from the local dify-support engineering-evidence and diagnostic-learning routes. Apply when a question concerns a known bug, fix, rollout, engineering discussion, or a diagnostic gap after a live first pass.

## Bugs and fixes

Use `linear / default` for matching issues, descriptions, latest comments, relationships, and status. Use `feishu / default` to read original dated discussions and follow-ups; search messages separately from documents. Use `custom_mcp / github-mcp` for the matching issue, PR, commit, release, and implementation. Discover the exact read actions and input schema; search titles locate candidates but do not establish fix status.

Plan the actual claims, for example “is this the same defect?” and “has this fix reached the affected customer path?” Verify the component, product mode, version, reproduction, latest updates, linked issue/PR and rollout separately. A similar error string can have another cause.

Distinguish hypothesis, accepted issue, implementation in progress, merged PR, release artifact, SaaS deployment, and successful customer retest. A Linear Done status or merged PR cannot prove the last two. Source describes a possible path; aligned runtime evidence establishes what happened. Do not invent an ETA or imply an escalation was sent.

## Notion lessons

After independent live investigation leaves a material gap, use `notion / default` and the `Dify SaaS 排障经验库`. Known database ID: `8a9d7058-a946-446f-a15e-d251e168a092`; data-source ID: `458faeb2-ef7c-405d-a71e-a51144156b77`. Verify the live connection and resource identity before querying; do not silently substitute another database if unavailable.

Discover `query_data_source` or its current equivalent and the actual schema. Select `状态=已发布` and `隐私状态=已脱敏`, then match the relevant component, symptom, failure boundary, or exact source reference. Open the selected page body with `retrieve_page_markdown` or another available read action. Follow pagination when needed for the selected evidence.

Prior experience chooses the next live check; it never proves the current ticket's cause, entitlement, fix or recovery. Recheck environment, version, path, dates and current customer facts. Record inapplicability or remaining gaps. Investigation here is read-only: creating issues, comments, messages, or lessons is not part of this Agent's tool authority.
