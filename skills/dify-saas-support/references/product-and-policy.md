# Product and Policy Evidence

Read for Dify SaaS product capabilities, limits, UI/configuration behavior, current public guidance, or a policy statement that affects the customer reply. Do not treat an old ticket reply or a search snippet as current product truth.

Use [Evidence search](evidence-search.md) for concrete Feishu/official-document entry points and query refinement. Record the relevant source checks before querying and review actual body reads with `investigation_check` before concluding.

## Fast route

1. Confirm this is Dify SaaS and identify the exact feature, app mode, UI/API entry, customer expectation, and any version or rollout constraint. If the evidence instead points to Enterprise or a private deployment, mark that mismatch; do not transfer SaaS behavior to it.
2. For a stable, non-account-specific question, use `custom_mcp / dify-docs` to find the relevant current official `docs.dify.ai` or `dify.ai` page and read the exact page. Use `official_docs_search` with `source=official_docs_community` for SaaS docs when supported by the live schema, then `official_docs_read`; use `official_site_list/search/read` for the website. Follow `next_offset` or other live pagination when the needed statement may be later in the page. A search result title or snippet is only a locator. Zendesk Help Center and third-party copies are not authoritative product documentation.
3. Check whether the page applies to the customer's product mode, plan, release, and configuration. If it gives a clear answer, stop; do not add DB, SigNoz, source, and GitHub calls merely because they are available.

## When the fast route is insufficient

- For account-specific availability, confirm the correct workspace/account and current plan or feature state through read-only `dify-db`; a public feature description does not prove that this customer has access. Discover uncertain schema through `information_schema` rather than guessing columns.
- For education credits, use the known policy in [Billing and credits](billing-and-credits.md). For other promotions, pricing, credits, or internal policy, search the formal Feishu SaaS knowledge base and current dated runbooks first. Other Feishu documents or chats may corroborate but do not by themselves replace policy. Use current official public wording for the customer-facing promise. Follow the Billing and credits route for a customer's entitlement or balance.
- If documentation and observed behavior disagree, inspect the aligned SaaS source/deployed ref and relevant runtime evidence. Source code describes a possible implementation, not proof that the customer's request took that branch. GitHub issues or merged PRs do not prove rollout or customer recovery.
- For plugin/model-provider functionality, distinguish the workspace's installed package/version from the latest Marketplace listing and the product's general capabilities. Inspect the matching installed-version source and runtime evidence before making a version-wide claim.

## Reply gate

When a current official page answers the question, answer it plainly and give the steps; do not call documented behavior “unverified” because you did not also check this workspace. Distinguish “documented,” “enabled for this workspace,” and “observed working for this customer” only when that difference matters to the question. Web-UI users need web-UI steps: find them in the docs before falling back to an API or CLI route. Include the original public documentation URL when it helps; never expose private Feishu or engineering links. If the exact plan, version, rollout, or policy is genuinely unknown, say in one sentence what remains to be checked; do not invent an ETA or imply a feature is generally available from a flag, issue, or nearby deployment.
