# 工具使用指南（SaaS 版）

## Zendesk 工具组

| 工具 | 用途 |
|------|------|
| `zendesk_get_ticket` | Step 1 起手，拉工单详情 + 对话 |
| `zendesk_get_attachments` | Step 7，有附件时下载（截图 / 文件） |
| `zendesk_search` | 按语法搜工单（如 `requester:xxx status:solved`） |
| `zendesk_search_articles` / `zendesk_get_article` | Help Center 查询（API 版，zd_kb_cli_exec 是 CLI 备选） |
| `zendesk_internal_reply` | **最终产出** Internal Note。不要分多次调用；一次写完三段式 |
| `zendesk_add_tags` | 酌情打标（如确认退款 tag） |
| `zendesk_get_user` | 查工单请求者信息 |

## 数据查询工具组

### `sql_query` — 生产库只读查询（核心）
- `database`: `prod`（langgenius）/ `billing`（billing 库）/ `plugin`（dify_plugin_daemon）
- 只允许 SELECT；INSERT / UPDATE / DELETE 等会被工具层拒绝
- 有 Workspace ID 时**必跑万能起手式**（参考 `prompts/sql/SQL_TEMPLATES.md`）
- Teleport 认证过期时会自动挂起工单，无需你处理

## 平台监控工具组（条件加载：配置了 API key 才可用）

### Grafana
- `grafana_list_dashboards`：搜 dashboard 关键词（如 "request success rate"）
- `grafana_get_dashboard`：按 UID 取面板详情
- `grafana_query_prometheus`：PromQL 范围查询（`query` / `start` / `end` / `step`）

### SigNoz
- `signoz_list_services`：列可查询的 service 名
- `signoz_search_logs`：按 `service_name` + `severity`（ERROR/WARN/INFO）+ 可选 `query`
- `signoz_search_traces`：按 `service_name` 查链路
- `signoz_get_field_keys` / `signoz_get_field_values`：发现可用过滤字段

## 知识匹配工具组

- `ticket_record_search`：在本地 1200+ 历史工单记录中 grep（多关键词 AND）
- `ticket_record_read`：按 ticket_id 读历史分析
- `zd_kb_cli_exec`：执行 `zd kb search/article/list/categories/sections`

## 源码工具组

- `git_grep` / `git_show` / `git_log` / `git_diff`：直接在 `saas源代码/<repo>/` 下运行
- `github_search_prs` / `github_get_pr`：GitHub API（langgenius/dify 等）

## Subagent 工具

### `run_subagents_parallel`（⚡⚡ 核心）
根因分析（Step 10-14）和知识匹配（Step 15-18）**必须**使用。
一次调用派发多个独立 subagent，参数 `tasks: [{task, tools, context}]`。
最多 6 个，推荐 3-4 个。每个 subagent 独立上下文，互不污染。

### `run_subagent`（单个）
只有在明确"只需查一件事"时用。默认应该用 parallel 版本。

## 通知工具

### `feishu_push`
- `ticket_done`：工单处理完，推送摘要给 Support Engineer
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
├─ platform_issue → grafana_* + signoz_* + sql_query
├─ knowledge_base → sql_query + ticket_record_*
├─ workflow       → sql_query + ticket_record_*
├─ refund         → sql_query (billing 库) + ticket_record_*
├─ api            → ticket_record_* (sql 通常不需要)
├─ insufficient_info → 尽量少调工具，直接输出"需客户补充"
├─ product_usage  → zd_kb_cli_exec / zendesk_search_articles 为主
└─ general        → 广谱
  ↓
Step 10-14: run_subagents_parallel (根因)
  ↓
Step 15-18: run_subagents_parallel (知识)
  ↓
Step 19: zendesk_internal_reply
Step 20: archive_ticket
Step 22: feishu_push ticket_done
```

## 避免的反模式

- ❌ 串行调用 `run_subagent` 5 次查相同阶段的不同源 → 应该 `run_subagents_parallel`
- ❌ 在不知道 Workspace ID 的情况下跑 SQL → 先跟客户确认或读已有评论找到 ID
- ❌ 用 `zendesk_internal_reply` 多次写半成品 → 一次写完整三段
- ❌ 反复调 `zendesk_get_ticket` → 拉一次就够，后续用 session 里的信息
- ❌ 自行调用 shell 跑 `tsh proxy db` 之类命令 → 运维手动管理
