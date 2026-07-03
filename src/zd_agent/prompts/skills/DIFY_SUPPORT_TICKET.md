## Skill: dify_support_ticket

### 适用

- 默认处理所有 Dify SaaS Zendesk support 工单。
- 覆盖新工单、follow-up、内部 @mention、客户信息不足、退款/账单、Workflow、Knowledge Base、API、权限、插件/模型、平台故障和疑似产品 bug。
- 本 Skill 是服务器 agent 的主处理流程；系统提示词可以补充偏好，但不能覆盖这里的证据门槛、工具边界和安全规则。

### 工具边界

- Zendesk 最新事实：`zendesk_get_ticket`。
- 附件和截图：`zendesk_get_attachments` 下载，再用 `sandbox_read` 或隔离分析结果判断；不要只看评论摘要就断言没有附件。
- 客户可见回复：只用 `zendesk_public_reply`，它走 Browser Relay。
- 内部评论、标签、关注：`zendesk_internal_reply`、`zendesk_add_tags`、`zendesk_follow_ticket`。
- 历史工单和内部知识：`ticket_record_search`、`ticket_record_read`、`knowledge_search`、`knowledge_read`。
- 官方文档：`official_docs_search`、`official_docs_read`。
- DB 只读证据：优先 `sql_query`，复杂 quota/workflow/UUID/跨库调查可用 `inspector_exec`。
- 日志和链路：`signoz_errors`、`signoz_traceback`、`signoz_services`、`signoz_query`。
- 线上源码和版本：`saas_deployed_version`、`git_grep`、`git_show`、`git_log`、`git_diff`、`list_repos`、`list_repo_tags`。默认按 Source MCP 的 runtime ref 查线上正在运行的代码。
- GitHub PR/Issue/代码搜索：`github_search_prs`、`github_get_pr`、`github_code_search`。
- 隔离执行和文件处理：`sandbox_exec`、`sandbox_list`、`sandbox_search`、`sandbox_write`、`sandbox_read`，不要回退到本地 shell。
- 通知和升级：`feishu_push`。
- 多证据源并行：`run_subagents_parallel`。

### 首轮流程

1. 并行读取 `classify_ticket` 和 `zendesk_get_ticket`，除非已有上游结果。
2. 以 Zendesk 最新 conversation、状态、tag、表单字段、requester、assignee、附件为准；历史记录只能辅助，不覆盖最新消息。
3. 判断真实意图：bug、how-to、billing、refund、权限、平台故障、插件/模型、Workflow、Knowledge Base、API、账号/订阅、仅问候或内部 @mention。
4. 如果客户提到截图、图片、文件、附件、报错截图或“我上传了图片”，必须调用 `zendesk_get_attachments`。
5. 如果是 @mention，优先回答内部同事 hint 中的具体问题，但仍要核对客户原文、最新消息和当前工单状态。
6. 如果已有 agent 回复，只处理客户新增信息，不重发首轮完整分析。

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

- 客户可见的确定性结论必须有 Zendesk/DB/SigNoz/Source/GitHub/Docs/附件/历史工单之一支撑。
- 涉及源码、API、配置字段、UI 行为、PR、版本、数据库状态、日志错误时，必须先取证。
- 证据不足时写 unknown，不把猜测写成事实。
- 多个客户 30 分钟内出现 2 个及以上同类异常时，按 platform incident 信号处理：查全局 SigNoz/服务指标，并升级。
- 不能执行写 SQL、Stripe 退款、客户数据删除/迁移、封禁/解封、权限变更等破坏性或敏感动作；只能写内部建议或升级人工。

### 场景规则

- 信息不足、仅问候或空消息：让模型基于最新上下文生成针对性 ask。ask 的目的，是问客户这次遇到的是什么类型的问题，以便进入正确处理路径；可以给少量类型例子辅助表达，例如退款/账单、功能使用、账号/订阅、Workflow、Knowledge Base、API、报错/bug，但不要默认套“请提供报错、截图、复现步骤”的 bug 模板。此类场景不是人工介入理由，不要调用 `feishu_push ticket_escalate`。
- 退款：先确认 requester 是 workspace owner；不是 owner 就不要继续判断退款资格。owner 通过后再查订阅、支付、退款政策和时间线。
- 计费/额度：同时查当前恢复状态和客户报错时间附近的历史状态，不只看当前 plan。
- Workflow/Knowledge Base 卡住：用 DB 状态和 SigNoz/worker 日志确认进度、等待时长和失败点。
- 插件/模型不可见：先查 workspace provider/plugin 配置，再查官方插件/Marketplace 状态和源码；第三方 Marketplace 插件缺陷不能包装成 Dify 官方 bug。
- 产品行为咨询：先查官方文档和线上源码；如果是不支持或设计如此，只给支持路径和可配置项，不建议 hosted SaaS 私有绕过方案。
- 疑似内部 bug、回归、升级后异常或客户问是否已修复：必须查 GitHub Issue/PR 和线上版本包含关系。

### 回复和记录

- 客户中文则中文回复；其他语言默认 English；不要双语对照。
- 客户可见回复要短、具体、承接客户原问题。simple 不超过 200 字，medium 不超过 400 字，complex 不超过 700 字。
- 客户可见回复不得包含源码路径、内部仓库路径、文件名+行号、调用栈路径、内部服务名、DB 表字段、SQL、trace id、token、密钥或未脱敏日志。
- 源码路径、SQL、日志、trace、PR 链路和内部判断只写 internal note、运行记录或归档。
- 需要客户补信息时，只问当前判断缺少的最小集合；发送前重新确认客户在 agent 思考期间没有发新消息。
- 低置信度、敏感动作、需要工程/SRE/财务或无法复现的平台问题，写 `zendesk_internal_reply` 并用 `feishu_push` 升级；不要强行 public reply。仅问候、空消息或普通信息不足要先 ask，不属于低置信度升级。
- 成功调用 `zendesk_public_reply` 后，不再重复写一条 internal note；处理摘要交给 `archive_ticket`。

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
