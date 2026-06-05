# zd-agent

`zd-agent` 是面向 **Dify SaaS 技术支持**的 Zendesk 工单自动处理 Agent。它监听 `tags=saas` 的 Zendesk 工单，先用轻量判定层判断客户消息是否值得处理，再由主 Agent 调用工具查证据并回复客户；退款、情绪、账号操作、低置信度等敏感场景会转人工并推送飞书。

> 当前权威文档：本 README 负责项目结构和本地开发；`deploy/RUNBOOK.md` 负责生产部署、服务运维和管理后台。

## 项目定位

- **自动公开回复**：明确诊断后调用 `zendesk_public_reply` 回客户，受 `public_reply.enabled` 和 `zendesk.min_ticket_id` 双重门控。
- **人工兜底**：高风险或低置信度场景走 `zendesk_internal_reply` / handoff，并通过飞书 Incoming Webhook 通知人工跟进。
- **触发判定前置**：`trigger.decider` 使用完整工单上下文和客户消息判断 6 种 verdict，避免寒暄、已解决、信息不足工单浪费主 Agent 算力。
- **静默窗口去抖**：客户连发消息时先累计上下文，窗口结束后再一次性处理。
- **生产证据驱动**：通过 Teleport 只读 PostgreSQL、SigNoz、Grafana、GitHub / 本地 SaaS 仓库查证据。
- **可灰度可回滚**：`min_ticket_id` 控制处理范围，`public_reply.enabled=false` 可退回只写 Internal Note。

## 整体架构

```text
Zendesk webhook / messaging webhook
        │
        ▼
services.saas_app (aiohttp)
  - /webhook/zendesk
  - /webhook/zendesk-messaging
  - /healthz
  - /teleport-auth
        │
        ├─ services.zendesk_webhook / zendesk_messaging_webhook
        ├─ services.trigger_consumer
        ├─ services.silence_waker
        ├─ services.waiting_poller
        └─ services.mention_poller
        │
        ▼
trigger.decider
  verdict: no_message / greeting / insufficient_info /
           substantial_issue / request_human / self_resolved
  high_risk: refund / emotional / incident
        │
        ▼
services.verdict_dispatch / trigger_actions
        │
        ▼
SessionManager → AgentSession → AgentLoop
        │
        ├─ LLMProvider(OpenAI / Anthropic)
        ├─ Guardrails / Compaction
        ├─ tools registry
        └─ optional subagents
        │
        ▼
Zendesk public reply / internal note + archive + Feishu notification
```

`src/zd_agent/entry/` 现在主要保留 CLI 和兼容导出；业务入口、webhook、轮询、状态流转等核心逻辑在 `src/zd_agent/services/`。

## 处理流程

1. Zendesk classic webhook 或 messaging webhook 收到客户消息。
2. webhook handler 抽取客户消息、附件和图片上下文，写入 `trigger_queue`。
3. `trigger.decider` 基于完整工单上下文做 verdict 和 high-risk 判定。
4. `services.verdict_dispatch` 决定路径：
   - `request_human`：直接转人工。
   - `self_resolved`：标记已处理或发送 closed 回复。
   - `greeting` / `insufficient_info`：发送 ask 模板；超过 `max_ask_count` 后转人工。
   - `substantial_issue`：进入静默窗口，之后由主 Agent 查证并回复。
5. 主 Agent 用工具查根因，形成客户回复或人工交接说明。
6. 处理结果归档到 `resources/zendesk-cli/工单记录/<ticket-id>.md`，完成/升级场景推送飞书。

## 工具层

工具注册入口是 `src/zd_agent/tools/registry.py`。当前最多注册 47 个工具，其中 Grafana 工具按配置条件注册。

```text
Zendesk:  zendesk_get_ticket / get_attachments / search / search_articles
          get_article / get_user / add_tags
          zendesk_internal_reply / zendesk_public_reply

知识库:    knowledge_search / knowledge_read
          official_docs_search / official_docs_read
          zd_kb_cli_exec

历史工单:  ticket_record_search / ticket_record_read

数据库:    inspector_exec / sql_query

日志监控:  signoz_errors / signoz_traceback / signoz_services / signoz_query
          grafana_query_prometheus / grafana_list_dashboards / grafana_get_dashboard

源码:      git_grep / git_show / git_log / git_diff / list_repos / list_repo_tags
          github_search_prs / github_get_pr / github_code_search
          saas_deployed_version / saas_branch_switch

Sandbox:  sandbox_list / sandbox_search / sandbox_read / sandbox_write

环境:      run_script / sandbox_exec

分类归档:  classify_ticket / archive_ticket

飞书:      feishu_push

子代理:    run_subagent / run_subagents_parallel
```

SigNoz 现在走 vendored `sz` CLI 封装，不再依赖旧 MCP 工具名。

## 目录结构

```text
src/zd_agent/
  entry/              CLI 和兼容导出层
    cli.py
    saas_app.py / waiting_poller.py / zendesk_webhook.py ...
  services/           SaaS app 核心服务层
    saas_app.py              aiohttp app factory / runner
    saas_routes.py           HTTP route 注册
    trigger_consumer.py      trigger_queue 消费
    verdict_classification.py / verdict_dispatch.py
    trigger_actions.py / trigger_gates.py / trigger_state_machine.py
    zendesk_webhook.py / zendesk_messaging_webhook.py
    silence_waker.py / waiting_poller.py / mention_poller.py
    feishu_notifications.py / escalation_notifications.py
    control_state.py
  trigger/            verdict 判定、静默窗口、状态存储
  session/            AgentSession / SessionManager / Sandbox 生命周期
  runtime/            LLM、AgentLoop、compaction、guardrails、subagent
  tools/              Zendesk / SQL / SigNoz / Grafana / Git / KB 等工具
  prompts/            Agent 工作流、策略、工具说明和回复模板
  observability/      指标埋点和 modify-rate 测量

log-viewer/           独立管理后台和工单日志界面(FastAPI + 单文件前端)
  main.py             API 入口
  admin.py            管理后台配置、暂停/恢复、重启、审计
  static/index.html   前端
  tests/

deploy/               Dockerfile、systemd、nginx、RUNBOOK
docs/                 当前文档索引
ai-docs/              本地知识库占位；生产优先使用 resources/saas版知识库
config.yaml           主配置
```

运行时数据默认在 `/opt/zd-agent/resources`：

```text
saas源代码/             SaaS 仓库 clone
saas版知识库/           本地知识库
zendesk-cli/工单记录/   工单归档
工单附件/               附件缓存
.state/                 工单状态、dedup、control 状态
```

## 配置要点

| 配置 | 当前值 | 说明 |
|---|---|---|
| `llm.model` | `gpt-5.5` | 主 Agent 模型 |
| `llm.subagent_model` | `gpt-5.4-mini` | Subagent 模型 |
| `trigger_decider.llm_model` | `gpt-5.5` | 触发判定模型 |
| `agent.max_concurrent_sessions` | `2` | 2G 服务器档位 |
| `agent.guardrails.max_tokens_per_session` | `1500000` | 单工单累计 Token 上限 |
| `agent.guardrails.compaction_threshold` | `0.3` | 达上限 30% 触发压缩 |
| `public_reply.enabled` | `true` | `false` 时退回只写 Internal Note |
| `public_reply.max_ask_count` | `10` | ask 次数上限 |
| `zendesk.min_ticket_id` | `2624` | 灰度边界，`0` 为全量 |
| `waiting_poller.scan_interval_seconds` | `300` | webhook 接管后的低频兜底 |

## Webhook

当前 SaaS app 注册的外部入口：

| Endpoint | 事件 | 说明 |
|---|---|---|
| `POST /webhook/zendesk` | classic ticket event | 经典 Zendesk trigger |
| `POST /webhook/zendesk-messaging` | `messaging_ticket.message_added` | Messaging 客户消息 |
| `GET /healthz` | health check | 返回 `ok` |
| `GET/POST /teleport-auth` | Teleport auth relay | 认证中转 |

`/webhook/feishu` 目前没有注册。飞书当前只作为 Incoming Webhook 通知通道，不提供飞书指令入口。

## 管理后台

管理后台在 `log-viewer/`，独立于主 agent：

- API: `log-viewer/main.py`
- 管理逻辑: `log-viewer/admin.py`
- 前端: `log-viewer/static/index.html`
- systemd: `deploy/systemd/zd-log-viewer.service`

生产访问入口和部署细节见 `deploy/RUNBOOK.md` 的“工单日志 Web 界面（log-viewer）”。

## 本地开发

```bash
uv sync
cp .env.example .env
uv run zd-agent --help
uv run pytest -q
```

当前主仓测试收集数量：`821 tests collected`。

`log-viewer` 是独立子项目，主仓 pytest 默认不递归收集它：

```bash
cd log-viewer
uv run pytest -q
```

`.env` 至少需要：

```env
OPENAI_API_KEY=sk-xxx
ZENDESK_SUBDOMAIN=your-subdomain
ZENDESK_EMAIL=you@example.com
ZENDESK_API_TOKEN=your-api-token
ZENDESK_WEBHOOK_SECRET=...
ZENDESK_MESSAGING_WEBHOOK_SECRET=...
FEISHU_WEBHOOK_URL=...
FEISHU_WEBHOOK_SECRET=...
GRAFANA_API_KEY=...
SIGNOZ_API_KEY=...
GITHUB_TOKEN=...
TELEPORT_USER=...
INTERNAL_SHARED_SECRET=...
```

## 常用 CLI

```bash
uv run zd-agent run <ticket-id>
uv run zd-agent run <ticket-id> --dry-run
uv run zd-agent batch 2624 2625 --dry-run
uv run zd-agent serve
uv run zd-agent status
uv run zd-agent tool list
uv run zd-agent repos status
```

生产容器入口是 `zd-agent serve`。

## 部署

生产部署使用 Docker + systemd，管理后台使用宿主 venv + systemd。服务器地址、域名和账号请在私有部署环境中配置，完整操作以 `deploy/RUNBOOK.md` 为准。

更新主 agent：

```bash
cd /opt/zd-agent/code
git pull
docker build --network host -f deploy/Dockerfile -t zd-agent:latest .
systemctl restart zd-agent
```

更新管理后台见 `deploy/RUNBOOK.md`。

## 当前文档

- `README.md`：项目结构、本地开发、当前架构概览。
- `deploy/RUNBOOK.md`：生产部署、服务操作、log-viewer、nginx / oauth2-proxy。
- `docs/README.md`：文档目录说明。
- `src/zd_agent/prompts/AGENTS.md`：Agent 工作流。
- `src/zd_agent/prompts/TOOLS.md`：工具使用约束。
- `src/zd_agent/prompts/SOUL.md`：身份与行为准则。
