# 工具使用指南（SaaS 版）

## MCP-only 外部工具边界

外部系统工具统一走远程 MCP：Zendesk / Help Center 走 Zendesk MCP，
客户可见 public reply 走 zendesk-browser-relay MCP，数据库走 DB Gateway MCP，
SigNoz 走 SigNoz MCP，GitHub 走 GitHub MCP，官方文档走 Docs Tool Service MCP，
飞书通知走 Notification Service MCP，sandbox 读写/搜索/执行走 Sandbox Worker MCP。

如果某个远程 MCP 不可用，记录“MCP 不可用 + 原始错误”，停止该工具路径并升级人工；
不要尝试任何非 MCP 命令行、shell 兜底、浏览器后台或直接外网替代路径。

## Zendesk 工具组

| 工具 | 用途 |
|------|------|
| `zendesk_get_ticket` | Step 1 起手，拉工单详情 + 对话 |
| `plan_investigation` | 分类后冻结 route、对象、时间窗、执行链和证据 lanes；成功前不得调用调查工具 |
| `zendesk_get_attachments` | Step 7，有附件时下载（截图 / 文件） |
| `zendesk_search` | 按语法搜工单（如 `requester:xxx status:solved`） |
| `zendesk_search_articles` / `zendesk_get_article` | Help Center 查询（经 Zendesk MCP） |
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
- 只有当前结论依赖 DB 且已有正确对象标识时才查询；先用 `information_schema` 发现真实表/列，不批量注入或机械执行“万能起手式”
- DB Gateway 负责 Teleport / DB proxy / 续期；认证过期时会自动挂起工单，无需你处理

## 平台日志工具组

### SigNoz（经远程 SigNoz MCP）
远程 SigNoz MCP 是统一观测查询入口，不只是查日志。它覆盖
logs / traces / metrics / services / schema / ClickHouse SQL：

- 发现：`signoz_list_services`、`signoz_get_field_keys`、`signoz_get_field_values`、`signoz_list_metrics`。服务名、deployment、字段或 metric 名未知时先发现，禁止靠记忆猜。
- 日志：先用 `signoz_aggregate_logs` 确认数量、影响范围和分组，再用 `signoz_search_logs` 取少量样本；完整 Python stacktrace 可用便利工具 `signoz_traceback`。
- Trace：用 `signoz_search_traces` 找异常/慢 span，再用 `signoz_get_trace_details` 按 trace ID 还原完整父子链；需要 p50/p90/p95/错误率时用 `signoz_aggregate_traces`。
- Metrics：先 `signoz_list_metrics` 找真实 metric 名，再用 `signoz_query_metrics` 按环境、deployment、Pod 和时间窗聚合。
- 便利/兜底：`signoz_errors`、`signoz_traceback`、`signoz_services` 保留给聚焦查询；只有结构化工具表达不了的只读调查才用 `signoz_query`。字段不确定时先查 schema/field discovery；raw SQL 可 `DESCRIBE signoz_logs.distributed_logs_v2` / `DESCRIBE signoz_traces...` / `DESCRIBE signoz_metrics...`，不要靠记忆猜字段。

结构化工具失败会返回原始 MCP 错误码和错误文本。失败调用不是证据；修正参数或换
一条有明确目的的查询，仍失败就记录准确缺口，不得把 0 行或错误改写成“线上无异常”。

远程 SigNoz MCP 不可用时，停止该监控查询路径并在 Internal Note / 飞书摘要里写明
“MCP 不可用 + 原始错误”。不要使用任何非 MCP 观测路径，不要把 0 结果改写成“线上无异常”。

> 版本感知：`saas_deployed_version` 查 cloud.dify.ai 生产 dify-api 当前运行版本 + 对应源码 ref。

## 知识匹配工具组

- `ticket_record_search`：在本地 1200+ 历史工单记录中 grep（多关键词 AND）
- `ticket_record_read`：按 ticket_id 读历史分析
- `zendesk_search_articles` / `zendesk_get_article`：查 Help Center 文章（经 Zendesk MCP）
- `official_docs_search` / `official_docs_read`：查 Dify 官方文档（经 Docs Tool Service MCP）

## 源码工具组

- `git_grep` / `git_show` / `git_log` / `git_diff`：底层通过 Source MCP 查询 SaaS 源码；默认
  `ref=runtime`，也就是线上正在运行的代码版本。不要假设本地 checkout 或 main 分支就是线上事实。
- 查线上 SaaS 行为时优先不传 `ref`，让 Source MCP 自动解析 runtime。只有客户明确问某个版本 / PR /
  tag 时才显式指定 ref。`git_grep` 提示 ref 无法解析时，先用 `saas_deployed_version` /
  `list_repo_tags` 确认 ref 再重试，不要反复用同一个错 ref。
- `github_search_issues` / `github_get_issue`：查询已知 bug、回归、限制和维护者结论
- `github_search_prs` / `github_get_pr`：查询候选修复及状态
- `github_code_search`：查询未在 Source MCP 本地镜像中的 GitHub 源码

## Subagent 工具

### `run_subagents_parallel`（复杂并行时使用）
仅在 `standard/deep` 调查且已冻结计划中有至少 2 个 `independent_lanes` 时使用：当 **代码 / 文档 / 数据库 / 日志 / GitHub PR/Issue**
存在多个互不依赖的取证任务，且并行会明显加快调查时，才按证据源拆给 subagent。
不要仅因需要两类证据就机械派发。
一次调用派发多个独立 subagent，参数 `tasks: [{task, tools, context}]`。
最多 5 个，推荐 2-3 个；复杂 platform_issue / workflow / 多组件交互可派 3-5 个。每个 subagent 独立上下文，互不污染。

### `run_subagent`（单个）
只有在明确"只需查一件事"时用。默认应该用 parallel 版本。

### Subagent 任务描述写法（重要）

Subagent 已内置统一 contract（统计量按问题类型选择；延迟/间歇问题做基线对比；维度查不到时按
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
- `ticket_escalate`：需人工介入（退款财务实操 / 敏感决策 / 已达到 ask 上限 / 已取证后需工程或 SRE 介入）；不要用于仅问候或普通信息不足。
- `alert`：通用告警（`urgent=true` 会触发 @ 所有人）

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
下一轮: classify_ticket → 加载当前类别策略
  ↓
调用 plan_investigation 冻结 route / customer_question / execution_chain / evidence_lanes / independent_lanes
  ↓
├─ platform_issue → 结构化 signoz_*；对象状态确实需要时再 sql_query
├─ knowledge_base → 按执行链选择 sql_query / worker logs / Source
├─ workflow       → 按执行链选择 sql_query / traces / worker logs / Source
├─ refund         → Owner gate + billing 事实；资格/金额/审批交人工
├─ api            → Docs / Source / GitHub / traces 按需取证
├─ insufficient_info → 尽量少调工具，直接输出"需客户补充"
├─ product_usage  → zendesk_search_articles / zendesk_get_article / official_docs_* 为主
└─ general        → 广谱
  ↓
选择 fast / standard / deep / high_risk 调查深度
  ↓
仅 standard/deep 且存在至少 2 个 independent_lanes 时使用 run_subagents_parallel
  ↓
zendesk_public_reply（solution/因果断言附 evidence_ledger；每条 claim 同时填 source_tools 和精确 source_call_ids；需要人工/SRE 时先发 handoff）
```

## 避免的反模式

- ❌ 串行调用 `run_subagent` 5 次查相同阶段的不同源 → 应该 `run_subagents_parallel`
- ❌ 在不知道 Workspace ID 的情况下猜 tenant 跑 SQL → 先用用户表单 Email 调 `inspector_exec section=tenants` 查关联 workspace；多个 workspace 且表单没有可匹配的 Workspace ID 时再向客户澄清
- ❌ 用 `zendesk_internal_reply` 当客户回复 → 客户看不到；客户可见回复必须直接 `zendesk_public_reply`
- ❌ 需要人工或 SRE 介入时直接沉默升级 → 需要人工或 SRE 介入时，先用 `zendesk_public_reply` 说明客户安全的升级原因和下一步，再通知内部团队
- ❌ 成功 `zendesk_public_reply` 后再写 Internal Note 或本地工单镜像 → 重复且会制造冲突记录
- ❌ 用 `zendesk_internal_reply` 多次写半成品 → 一次写完整三段
- ❌ 反复调 `zendesk_get_ticket` → 拉一次就够，后续用 session 里的信息
- ❌ 自行调用 shell 跑 `tsh proxy db` 之类命令 → DB Gateway 统一管理
