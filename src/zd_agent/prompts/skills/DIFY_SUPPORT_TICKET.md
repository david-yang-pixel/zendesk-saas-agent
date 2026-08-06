## Skill: dify_support_ticket

### 适用

- 默认处理所有 Dify SaaS Zendesk support 工单。
- 覆盖新工单、follow-up、内部 @mention、客户信息不足、退款/账单、Workflow、Knowledge Base、API、权限、插件/模型、平台故障和疑似产品 bug。
- 本 Skill 是服务器 agent 的主处理流程；系统提示词可以补充偏好，但不能覆盖这里的证据门槛、工具边界和安全规则。

### 工具边界

- Zendesk 最新事实：`zendesk_get_ticket`。
- 附件和截图：`zendesk_get_attachments` 下载。返回路径相对于当前工单 sandbox 根目录（如 `attachments/error.log`），不要加 ticket_id 或绝对路径；`sandbox_read` 只用于 UTF-8 文本，PNG/JPEG 等图片会在下一轮自动注入视觉输入，禁止交给 `sandbox_read`。不要只看评论摘要就断言没有附件。
- 客户可见回复：只用 `zendesk_public_reply`，它走 Browser Relay。
- 内部评论、标签、关注：`zendesk_internal_reply`、`zendesk_add_tags`、`zendesk_follow_ticket`。
- 历史工单和内部知识：`ticket_record_search`、`ticket_record_read`、`knowledge_search`、`knowledge_read`。
- 官方文档：`official_docs_search`、`official_docs_read`。
- DB 只读证据：优先 `sql_query`，复杂 quota/workflow/UUID/跨库调查可用 `inspector_exec`。
- 日志和链路：优先使用结构化 `signoz_list_services`、`signoz_search_logs`、`signoz_aggregate_logs`、`signoz_search_traces`、`signoz_aggregate_traces`、`signoz_get_trace_details`、`signoz_list_metrics`、`signoz_query_metrics` 和字段发现工具；`signoz_errors`、`signoz_traceback`、`signoz_services`、`signoz_query` 仅作聚焦便利或只读兜底。
- 线上源码和版本：`saas_deployed_version`、`git_grep`、`git_show`、`git_log`、`git_diff`、`list_repos`、`list_repo_tags`。默认按 Source MCP 的 runtime ref 查线上正在运行的代码。
- GitHub PR/Issue/代码搜索：`github_search_prs`、`github_get_pr`、`github_search_issues`、`github_get_issue`、`github_code_search`。
- 隔离执行和文件处理：`sandbox_exec`、`sandbox_list`、`sandbox_search`、`sandbox_write`、`sandbox_read`，所有路径均相对于当前工单 sandbox 根目录，不要重复 ticket_id 或传绝对路径；不要回退到本地 shell。
- 通知和升级：`feishu_push`。
- 多证据源并行：`run_subagents_parallel`。

### 首轮流程

1. 先调用 `zendesk_get_ticket`。它成功写入最新工单上下文后，下一轮再调用 `classify_ticket`；二者不能并行。
2. 调用 `classify_ticket` 得到当前类别后，必须调用 `plan_investigation`，固结 `route`、`customer_question`、`object_identifiers`、`time_window`、`execution_chain`、`evidence_lanes` 和 `independent_lanes`。计划成功前不得调用调查工具或发送 solution。
3. 以 Zendesk 最新 conversation、状态、tag、表单字段、requester、assignee、附件为准；历史记录只能辅助选择下一项核查，不能证明当前结论。
4. 判断真实意图：bug、how-to、billing、refund、权限、平台故障、插件/模型、Workflow、Knowledge Base、API、账号/订阅、仅问候或内部 @mention。
5. 如果客户提到截图、图片、文件、附件、报错截图或“我上传了图片”，必须调用 `zendesk_get_attachments`。
6. 如果是 @mention，优先回答内部同事 hint 中的具体问题，但仍要核对客户原文、最新消息和当前工单状态。
7. 如果已有 agent 回复，只处理客户新增信息，不重发首轮完整分析。

### 调查深度

先选择 `fast / standard / deep / high_risk`，证据不足时再升级深度：

- `fast`：稳定产品知识、当前官方文档、确定的配置或使用方法。只需当前权威来源；根因不适用。
- `standard`：单个对象的确定性运行时或配置故障。核对对象、时间窗、执行链和直接证据。
- `deep`：间歇故障、延迟、429/5xx、队列压力、恢复事件、回归或平台影响。必须包含客户锚点、同租户/跨租户影响范围、相邻基线和完整因果链。
- `high_risk`：billing/refund、身份、权限、安全、删除或任何 Admin 动作。除技术证据外，还要核对身份和授权；agent 只能回复已核实技术事实或已确认技术根因，不能自行判定业务资格或执行动作。

`standard/deep` 且计划中有至少 2 个真正互不依赖的 `independent_lanes` 时，才能调用 `run_subagents_parallel`。`fast/high_risk` 不派 subagent。子 Agent 返回带精确调用 ID 的证据后，父 Agent 不得重复查询子 Agent 已返回的同一证据。

### 完整链路分析

- 处理 bug、线上异常、计费/额度、Workflow、Knowledge Base、插件/模型、API、权限、版本差异时，必须先画出实际执行链，再逐跳取证。
- 不要只看局部源码、单条日志、单张 DB snapshot 或单个 PR 就下结论。
- 典型链路：
  - billing/quota：Stripe 或订阅状态 -> billing DB -> tenant 配置 -> quota/feature 用量 -> runtime 缓存或限流 -> 用户看到的错误。
  - workflow：请求入口 -> app/workflow 配置 -> workflow_runs -> workflow_node_executions -> worker/runtime 日志 -> Answer/输出节点。
  - knowledge base：dataset/document 状态 -> indexing/embedding 任务 -> segment/vector 状态 -> retrieval/rerank 调用 -> 用户结果。
  - plugin/model：workspace provider 配置 -> plugin/provider 状态 -> marketplace 或官方插件源码 -> runtime 日志 -> UI 可选项。
  - API：请求路径和版本 -> auth/permission -> handler/service -> DB/worker -> error response。
- 查源码时先确认线上版本：用 `saas_deployed_version` 或 Source MCP runtime 路径，再用 `git_grep/git_show/git_log/git_diff` 看完整调用链；如果涉及疑似修复，补查 GitHub PR。
- GitHub PR 候选必须确认是否被 revert、是否有 follow-up regression、客户当前版本是否已包含修复。

### 证据规则

- 客户可见的确定性结论必须由本次会话中成功工具调用返回的实际结果支撑；工具参数、失败结果、错误文本、分类输出和历史工单不算当前证据。
- 涉及源码、API、配置字段、UI 行为、PR、版本、数据库状态、日志错误时，必须先取证。
- 证据不足时写 unknown，不把猜测写成事实。向客户追问前先穷尽当前支持侧可取得的证据；只问无法从现有身份、对象和工具结果推导的最小缺口。
- 区分 `symptom / failing_boundary / immediate_trigger / root_cause / contributing_factor / recovery_event / current_state`。确认失败边界、相关性或恢复，不等于确认根因。
- `standard/deep` 的 `solution` 只有在 root cause 为 `confirmed` 且成功工具证据覆盖正确对象和时间窗时才可发送。若只确认失败边界或触发条件，回复已确认事实并继续调查或 handoff，不称为根因。
- `fast` 的 `solution` 只能回答由当前官方 Docs、已对齐 Source 或其他权威来源确认的稳定产品事实。
- PR 已合并不等于生产已修复；当前恢复也不等于历史根因已确认。必须分别验证版本包含关系、运行时状态和恢复事件。
- 多个客户 30 分钟内出现 2 个及以上同类异常时，按 platform incident 信号处理：查全局 SigNoz/服务指标，并升级。
- 不能执行写 SQL、Stripe 退款、客户数据删除/迁移、封禁/解封、权限变更等破坏性或敏感动作；只能写内部建议或升级人工。

### 结构化证据台账

调用 `zendesk_public_reply(reply_type="solution")`、在任何 reply_type 中断言根因，或执行 `high_risk` 的 `handoff` 时，必须提交 `evidence_ledger`：

- `route`: `fast | standard | deep | high_risk`
- `decision_scope`: `customer_guidance | technical_diagnosis | technical_fact_only`
- `root_cause_status`: `confirmed | evidence_supported | unknown | not_applicable`
- 每条 material claim 必须写：`claim`、`causal_role`、`status`、`source_tools`、`source_call_ids`、`object_identifiers`、`time_window`、`observed_result`、`alternatives_checked`

`source_tools` 只写工具名，`source_call_ids` 必须引用本会话内实际成功且返回结果的精确调用 ID。material claim 的对象和时间窗必须绑定冻结计划，且被引用调用的参数或返回结果必须实际包含当前对象标识。发送层会拒绝路由与已冻结计划不一致、失败工具、历史工单、缺少对象/时间窗/观察结果的台账，以及没有 confirmed root cause 的 standard/deep solution。普通 `ask`/`handoff` 在不声称根因时可以保持 root cause unknown；`high_risk handoff` 仍须以 `technical_fact_only` 提交台账，至少包含一条绑定目标对象和精确成功调用的 confirmed 事实，并明确剩余人工决策缺口。

### 场景规则

- 信息不足、仅问候或空消息：让模型基于最新上下文生成针对性 ask。ask 的目的，是让客户说出这次需要什么帮助，以便 agent 后续自行分类和排查；可以用 `For example...` 列常见方向（billing/refund、feature usage/how-to、account/subscription、workflows、knowledge base、API、error/bug），但不要要求客户先选择分类，也不要默认套“请提供报错、截图、复现步骤”的 bug 模板。此类场景不是人工介入理由，不要调用 `feishu_push ticket_escalate`。
- 退款：先确认 requester 是 workspace owner；不是 owner 就不要继续判断退款资格。owner 通过后只核实订阅、支付、政策版本和时间线等事实；退款资格、全额/按比例/拒绝、金额、审批、处理和时效均须人工业务审批，agent 不能自行判定。
- 计费/额度：同时查当前恢复状态和客户报错时间附近的历史状态，不只看当前 plan。
- Workflow/Knowledge Base 卡住：用 DB 状态和 SigNoz/worker 日志确认进度、等待时长和失败点。
- 插件/模型不可见：先查 workspace provider/plugin 配置，再查官方插件/Marketplace 状态和源码；第三方 Marketplace 插件缺陷不能包装成 Dify 官方 bug。
- 产品行为咨询：先查官方文档和线上源码；如果是不支持或设计如此，只给支持路径和可配置项，不建议 hosted SaaS 私有绕过方案。
- 疑似内部 bug、回归、升级后异常或客户问是否已修复：必须查 GitHub Issue/PR 和线上版本包含关系。

### 回复和记录

- 客户可见 public reply 一律使用 English，不跟随客户语言；不要双语对照。
- 客户可见回复要短、具体、承接客户原问题。simple 不超过 200 字，medium 不超过 400 字，complex 不超过 700 字。
- 客户可见回复使用易读纯文本：不用 Markdown 标题、粗体、反引号、emoji 或装饰性分隔线；先给结论，再用短段落和必要的 `- ` 列表说明步骤。
- 客户可见回复不得包含源码路径、内部仓库路径、文件名+行号、调用栈路径、内部服务名、DB 表字段、SQL、trace id、token、密钥或未脱敏日志。
- 源码路径、SQL、日志、trace、PR 链路和内部判断只写 internal note 或远程运行记录。
- 需要客户补信息时，只问当前判断缺少的最小集合；发送前重新确认客户在 agent 思考期间没有发新消息。
- 低置信度、敏感动作、需要工程/SRE/财务或无法复现的平台问题，也要先公开回复客户：只说明已确认事实、需要升级的客户安全原因和下一步，不披露内部细节或未确认根因；随后由公开回复工具自动写内部分析并用 `feishu_push` 升级。只有公开通道不可用或说明原因会泄露安全/隐私信息时，才只写 `zendesk_internal_reply`，并注明客户尚未收到通知。仅问候、空消息或普通信息不足要先 ask，不属于低置信度升级。
- 人工支持时间为上海时间 09:00（含）到 18:00（不含）。handoff 的在岗/离线提示和专用 Footer 由发送层自动生成，body 只写客户安全的升级原因，不要重复工作时间。
- 调用 `zendesk_public_reply` 时必须提供 `internal_analysis`，写清分类、已确认事实、证据、未知项和下一步；`solution`、根因断言或 `high_risk handoff` 还必须提供结构化 `evidence_ledger`。公开回复成功后，工具自动写入一条对应内部分析；不要再调用 `zendesk_internal_reply` 重复记录。

### Internal note 结构

```
## 内部分析
- 分类: <ticket_type>
- 结论: <confirmed / likely / unknown>
- 完整链路: <从入口到结果的关键跳点>
- 关键证据: <Zendesk / DB / SigNoz / Source / GitHub / Docs / 附件>
- 置信度: high / medium / low

---

## 建议客户回复
<客户安全措辞，不包含内部路径和敏感字段>

---

## 人工指引
- 下一步: <需要谁处理什么>
- 风险: <客户影响 / 数据风险 / 回归风险>
- 升级对象: <DE / SRE / Dev / Finance>
```
