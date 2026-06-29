# 工具使用指南（SaaS 版）

## Zendesk 工具组

| 工具 | 用途 |
|------|------|
| `zendesk_get_ticket` | Step 1 起手，拉工单详情 + 对话 |
| `zendesk_get_attachments` | Step 7，有附件时下载（截图 / 文件） |
| `zendesk_search` | 按语法搜工单（如 `requester:xxx status:solved`） |
| `zendesk_search_articles` / `zendesk_get_article` | Help Center 查询（API 版，zd_kb_cli_exec 是 CLI 备选） |
| `zendesk_internal_reply` | 只写 Internal Note（客户不可见）。不要分多次调用；一次写完三段式 |
| `zendesk_public_reply` | **客户可见最终回复**。SaaS messaging 工单要回复客户时直接调用它 |
| `zendesk_add_tags` | 酌情打标（如确认退款 tag） |
| `zendesk_follow_ticket` | 关注工单（默认用 MCP 配置的 Zendesk agent 账号） |
| `zendesk_get_user` | 查工单请求者信息 |

除 `zendesk_public_reply` 继续走 `zendesk-browser-relay` 外，Zendesk 读取、搜索、
Help Center、Internal Note、标签和关注都通过远程 Zendesk MCP 执行。附件发现也走
MCP；实际下载通过 `download_attachment` 返回 base64，agent 只写入 sandbox。后台
trigger 拉消息、requester profile prefetch、mention poller、human takeover 检测和
ACK/error note 也走 Zendesk MCP。

## 数据查询工具组

### `sql_query` — 生产库只读查询（核心，经 DB Gateway）
- `database`: `prod`（langgenius）/ `billing`（billing 库）/ `plugin`（dify_plugin_daemon）
- 只允许 SELECT；INSERT / UPDATE / DELETE 等会被工具层拒绝
- 有 Workspace ID 时**必跑万能起手式**（参考 `prompts/sql/SQL_TEMPLATES.md`）
- DB Gateway 负责 Teleport / DB proxy / 续期；认证过期时会自动挂起工单，无需你处理

## 平台日志工具组

### SigNoz（经远程 sz MCP）
远程 SigNoz MCP 是统一观测查询入口，不只是查日志。它覆盖
logs / traces / metrics / services / schema / ClickHouse SQL：

- `signoz_errors`：查 ERROR/FATAL logs，可按 `tenant` / `grep`(body 子串) / `deployment` / `env` / `hours` / `limit` 过滤；这是便利工具，做广谱聚合或 0 行复查时改用 `signoz_query`
- `signoz_traceback`：从 logs 里的 exception 字段拉完整 Python stacktrace（按 `id` / `tenant` / `grep`）。要看堆栈用这个，不要用 signoz_errors
- `signoz_services`：列当前活跃 service / k8s deployment；查不到 logs/traces 时先用它核对服务名和 deployment 名
- `signoz_query`：统一 ClickHouse 查询入口，可查 logs、traces、metrics，也可做聚合和 schema 探测。常用表先用 `DESCRIBE signoz_logs.distributed_logs_v2`，traces/metrics 字段不确定时先跑 `DESCRIBE signoz_traces...` / `DESCRIBE signoz_metrics...` 找真实表名和列；字段不确定时先查 schema，不要靠记忆猜字段。

远程 SigNoz MCP 不可用时，停止该监控查询路径并在 Internal Note / 飞书摘要里写明
“MCP 不可用 + 原始错误”。不要使用本地 `sz` CLI，不要使用 SigNoz Web UI，不要把 0
结果改写成“线上无异常”。

> 版本感知：`saas_deployed_version` 查 cloud.dify.ai 生产 dify-api 当前运行版本 + 对应源码 ref。

## 知识匹配工具组

- `ticket_record_search`：在本地 1200+ 历史工单记录中 grep（多关键词 AND）
- `ticket_record_read`：按 ticket_id 读历史分析
- `zd_kb_cli_exec`：执行 `zd kb search/article/list/categories/sections`

## 源码工具组

- `git_grep` / `git_show` / `git_log` / `git_diff`：底层通过 Source MCP 查询 SaaS 源码；默认
  `ref=runtime`，也就是线上正在运行的代码版本。不要假设本地 checkout 或 main 分支就是线上事实。
- 查线上 SaaS 行为时优先不传 `ref`，让 Source MCP 自动解析 runtime。只有客户明确问某个版本 / PR /
  tag 时才显式指定 ref。`git_grep` 提示 ref 无法解析时，先用 `saas_deployed_version` /
  `list_repo_tags` 确认 ref 再重试，不要反复用同一个错 ref。
- `github_search_prs` / `github_get_pr`：GitHub API（langgenius/dify 等）

## Subagent 工具

### `run_subagents_parallel`（复杂并行时使用）
多证据源工单默认使用：只要需要查看 **代码 / 文档 / 数据库 / 日志 / 历史工单 / GitHub PR**
中任意 ≥2 类证据源，就按证据源拆给 subagent。单一证据源问题可由主 agent 直接批量调工具。
一次调用派发多个独立 subagent，参数 `tasks: [{task, tools, context}]`。
最多 5 个，推荐 2-3 个；复杂 platform_issue / workflow / 多组件交互可派 3-5 个。每个 subagent 独立上下文，互不污染。

### `run_subagent`（单个）
只有在明确"只需查一件事"时用。默认应该用 parallel 版本。

### Subagent 任务描述写法（重要）

Subagent 已内置统一 contract（数据类必给 count+p50+p90+max+基线对比；维度查不到时按
service.name/endpoint 回退；源码类先 grep 上下文再下结论），**不要在 task 里重复列这些
通用约束**——重复反而稀释主任务，且每次调用浪费 ~200-400 tokens。

### Subagent 完成判定（减少重复排查）

Subagent 返回后先读它的 `Conclusion / Checked / Evidence / Gaps / Parent next action`。
若 `Gaps` 为 none，或缺口不会改变客户回复结论，父 agent 应直接汇总并回复；不要再用
`git_grep` / `github_code_search` / `sql_query` 大范围重复查同一证据源。只有缺少会改变
结论的具体事实时，才追加少量、有明确目标的工具调用。

当前 prompt 和工具描述已经覆盖 subagent 规则；不要主动加载 `references/subagent_guide.md`。

✅ 正确（聚焦"查什么"）：
- `task: "SigNoz 查 workspace=70b5cf9a... 最近 7 天 webapp/site/embed/init 的错误"`
- `task: "git_grep dify-saas/api/services 里所有 BillingPlan.refund_eligibility 的调用点"`

❌ 错误（重复 contract，应删掉）：
- `"...必须给 count+p50+p90+max，且至少找一个正常参考样本做对比；若 tenant 维度查不到也要按..."`
- `"...请引用具体行号 / commit / 文件路径..."`

只有在**任务有非通用的特殊要求**时（如"只看一眼别 deep dive"、"窗口必须 30 天而非默认 7 天"、
"只关心 5xx 不要看 4xx"）才在 task 里加约束，否则交给 contract。

## 通知工具

### `feishu_push`
- `ticket_done`：工单处理完，推送摘要给 Support Engineer
  - `content` 必须用固定字段，不要写成一整段；DB 字段按表名分组，用 - `字段`: 值 逐行展示，**不要把 DB 字段写进一整段英文长句**：
    ```text
    处理结果：已回复客户 / 已完成
    关键事实：一句话写最重要证据或判定
    数据库核查：
    **workspace**
    - `status`: normal
    **tenant_subscription_settings**
    - `plan`: team
    - `expiration_date`: 2026-07-11 01:39:43
    代码路径：
    - `api/services/billing_service.py:188-191`: /subscription/info proxy
    日志核查：48 小时内未找到该 tenant 或 /subscription/info 的 ERROR/FATAL
    客户回复：一句话概括已发给客户的内容
    后续动作：无需跟进 / 等客户补充 / 需观察
    注意事项：SigNoz 不可用 / 证据缺口 / 无
    ```
- `ticket_escalate`：需人工介入（退款 / 信息不足 / 敏感决策）
- `alert`：通用告警（`urgent=true` 会触发 @ 所有人）

## 归档工具

### `archive_ticket`
- 写入 `zendesk-cli/工单记录/<ticket_id>.md`
- 首次处理 → 建新文件（带 frontmatter）
- 跟进 → 追加 `## 跟进 <ts>` 段

## 运维工具

### `saas_branch_switch`
- 支持 5 仓库别名（`d`/`bill`/`mb`/`mf`/`deploy`）
- agent 一般不需要主动调用；由运维在 CLI 手动触发

## 工具选择策略

```
工单到达
  ↓
Step 1: zendesk_get_ticket
  ↓
Step 4: classify_ticket → 决定激活哪些工具组
  ↓
├─ platform_issue → signoz_* + sql_query
├─ knowledge_base → sql_query + ticket_record_*
├─ workflow       → sql_query + ticket_record_*
├─ refund         → sql_query (billing 库) + ticket_record_*
├─ api            → ticket_record_* (sql 通常不需要)
├─ insufficient_info → 尽量少调工具，直接输出"需客户补充"
├─ product_usage  → zd_kb_cli_exec / zendesk_search_articles 为主
└─ general        → 广谱
  ↓
Step 10-14: 单一证据源直接工具排查；多证据源默认 run_subagents_parallel (根因)
  ↓
Step 15-18: 文档/历史/GitHub/日志等跨源核查交给 subagent 并行摘要 (知识)
  ↓
Step 19: zendesk_public_reply（客户可见回复）或 zendesk_internal_reply（仅内部分析）
Step 20: archive_ticket
Step 22: feishu_push ticket_done（content 用固定字段摘要，不要粘贴完整客户回复）
```

## 避免的反模式

- ❌ 串行调用 `run_subagent` 5 次查相同阶段的不同源 → 应该 `run_subagents_parallel`
- ❌ 在不知道 Workspace ID 的情况下跑 SQL → 先跟客户确认或读已有评论找到 ID
- ❌ 用 `zendesk_internal_reply` 当客户回复 → 客户看不到；客户可见回复必须直接 `zendesk_public_reply`
- ❌ 成功 `zendesk_public_reply` 后再写 Internal Note → 重复且客户不可见，处理摘要交给 `archive_ticket`
- ❌ 用 `zendesk_internal_reply` 多次写半成品 → 一次写完整三段
- ❌ 反复调 `zendesk_get_ticket` → 拉一次就够，后续用 session 里的信息
- ❌ 自行调用 shell 跑 `tsh proxy db` 之类命令 → DB Gateway 统一管理
