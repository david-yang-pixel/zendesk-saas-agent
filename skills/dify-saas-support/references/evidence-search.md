# Knowledge, Documents, and Search Recovery

Adapted from the local dify-support evidence-source and OpenConnector routes. Use these concrete entry points through live read-only discovery; do not assume a saved token or title proves current access or policy freshness.

## Formal Feishu knowledge

The formal product source is `SaaS知识库`, known entry token `MGC0fbkWwloYg3dmI6pc5oWLnVe`, through `feishu / default`. Verify what kind of resource it is (Wiki node, Drive folder, or document) before selecting a tool. A Wiki token is not necessarily a document ID or space ID. Resolve the node/folder, obtain its document/child tokens, and read relevant bodies. If the known entry moved, search its title and verify the returned resource.

Discover capabilities with scoped `list_read_tools` and queries such as `wiki`, `document`, or `message`. The directory is alphabetically paginated: the first 50 entries are not the complete Feishu capability list. `query` filters all permitted entries before pagination. Use live `get_action_guide` for exact parameters. `feishu.search_documents` searches documents and Wiki resources; it does not search chats. Use `get_document_content`/`fetch_document` or other discovered read tools to open relevant bodies.

Search short distinctive terms before compound sentences. For an education-credit question, useful independent terms include `教育`, `教育优惠`, `学生`, `积分`, `education`, or `student discount`. These are examples, not a universal fixed query set. For another problem use its feature name, error string, issue ID, or product component. Inspect relevant dates and follow promising document links.

A zero result for `教育版 积分 刷新` proves only that this query returned no match. Shorten or change the term, check the canonical knowledge-base entry, and use a relevant alternative search surface before declaring a gap. Do not scan every document or every page of a broad query; refine around the ticket's actual question.

## Discussions and secondary sources

When formal documents are missing, ambiguous, old, or contradicted, discover Feishu message/chat/thread search capabilities. Search by topic, exact error/issue, or a useful date range; read the matched original message and follow-up thread, including links to policy or engineering records. A chat statement is dated supporting evidence; verify author/context and any subsequent correction before treating it as policy.

Record `unresolved` when visible material still cannot establish the current policy. Do not claim the knowledge base is empty, or blame permissions, merely because a query returned no hits. Distinguish “no match for these terms,” “candidate not yet read,” “read but inapplicable,” and an actual tool/permission failure.

## Official documents and website

Use `custom_mcp / dify-docs`. Discover its live descriptors, then call `official_docs_search` with `source=official_docs_community` for SaaS. Read the selected page using `official_docs_read`. For pricing, education offers, or other website material, use the available `official_site_list`, `official_site_search`, and `official_site_read` tools. Follow the returned `next_offset` when the needed statement is later in the page.

Keep internal policy and public promises as separate planned checks. A successful DB query cannot satisfy either check; a document search title/snippet cannot satisfy a body read. Verify URL, SaaS versus Enterprise, language, update/effective date, and applicability. Cite the original `docs.dify.ai` or `dify.ai` URL in the customer draft when useful. Do not replace this route with Zendesk Help Center or third-party copies.

## Conflicts and stopping

Compare source authority, effective date, cohort/plan, version, and the customer's event time. An active education-promotion row does not prove a current free-credit policy; an old policy may apply only to a legacy cohort. Describe unresolved contradictions instead of choosing the easiest result. Stop when the relevant statement is supported or when bounded focused attempts establish an explicit gap; do not consume the budget cataloging unrelated tools.
