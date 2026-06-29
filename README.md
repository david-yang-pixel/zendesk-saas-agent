# zd-agent

`zd-agent` 是面向 **Dify SaaS 技术支持**的 Zendesk 工单自动处理 Agent。它监听 `tags=saas` 的工单，由轻量 LLM 先做触发判定（verdict），再由主 Agent 以「工具循环」驱动完成根因排查，最后**直接给客户发 public reply**，或在敏感场景下转人工 / 推飞书给 SE / 财务。

> 设计目标：把 SaaS support 的人工 SOP 尽可能自动化，同时在退款、情绪、平台故障等敏感场景保留人工兜底；全程可归档、可回溯、可灰度。

## 项目定位

- **自动公开回复**：命中明确诊断时,agent 直接 `zendesk_public_reply` 回客户(受 `public_reply.enabled` 总开关 + `min_ticket_id` 灰度边界门控)
- **敏感场景人工兜底**：退款、情绪、账号操作、低置信度 → 走 `zendesk_internal_reply` + 飞书推送 SE/财务
- **触发判定前置**：用便宜模型先把客户消息分类成 6 种 verdict，避免对寒暄/打招呼/已解决的工单浪费主 Agent 算力
- **静默窗口去抖**：客户连发多条消息时先攒一攒,窗口结束再一次性处理,避免半句话就触发
- **真实数据驱动**：通过独立 DB Gateway 调用当前运维服务器维护的 Teleport / DB proxy 只读连生产库(prod / billing / plugin)，并通过独立 SigNoz MCP 查询日志证据,不靠猜
- **可灰度可回滚**：`min_ticket_id` 限缩处理范围,`public_reply.enabled` 一键回退到「只发 Internal Note」
- **单语言输出**：自动检测客户语言,中文客户回中文,其他一律 English(不双语对照)

## 整体架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│                          入口层 Entry (aiohttp)                      │
│  POST /webhook/zendesk            ← 经典 ticket 事件                 │
│  POST /webhook/zendesk-messaging  ← messaging_ticket.message_added   │
│  GET  /healthz                                                       │
│                                                                       │
│  后台任务: silence_waker / waiting_poller / mention_poller           │
│            trigger_consumer(消费 trigger_queue)                      │
└──────────────────────────────┬────────────────────────────────────────┘
                               │ 客户消息
┌──────────────────────────────▼────────────────────────────────────────┐
│                  触发判定 trigger.decider (gpt-5.5)                    │
│  6 种 verdict: no_message / greeting / insufficient_info /            │
│                substantial_issue / request_human / self_resolved      │
│  + 高风险 flag: refund / emotional / incident                         │
│  fast-path: 精确匹配 human 关键词直接 request_human(不走 LLM)        │
└──────────────────────────────┬────────────────────────────────────────┘
                               │ verdict 分发
┌──────────────────────────────▼────────────────────────────────────────┐
│                       分发 _handle_post_verdict_dispatch                │
│  request_human         → escalated_to_human(转人工)                  │
│  self_resolved         → public_reply(closed) / 标记 processed        │
│  substantial+emotional → escalated_to_human(情绪客户必须人接手)      │
│  substantial+refund    ┐                                              │
│  substantial+incident  ├→ 进静默窗口 → 主 Agent 查证后再决定          │
│  substantial(safe)     ┘                                              │
│  greeting/insufficient → ask 模板(累计 max_ask_count 次后转人工)     │
└──────────────────────────────┬────────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────────┐
│                       会话 / 运行层 Session + Runtime                   │
│  SessionManager → AgentSession(并发隔离)                             │
│  AgentLoop(AsyncGenerator 事件流) 驱动 LLM ↔ 45 个工具循环           │
│  ┌───────────┐ ┌───────────┐ ┌──────────────┐ ┌──────────────────┐  │
│  │ LLMProvider│ │ Guardrails│ │ Compaction   │ │ Subagent 编排    │  │
│  │ OpenAI/Anth│ │ 轮/Token/ │ │ 锚点+snip+   │ │ 串行优先,mini   │  │
│  │            │ │ 超时       │ │ LLM 多级摘要 │ │ 模型,tool 子集  │  │
│  └───────────┘ └───────────┘ └──────────────┘ └──────────────────┘  │
└──────────────────────────────┬────────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────────┐
│                  输出: reply + 归档 + escalate                         │
│  zendesk_public_reply(solution/ask/closed) 或 zendesk_internal_reply   │
│  archive_ticket → resources/zendesk-cli/工单记录/<id>.md               │
│  feishu_push(escalate 场景, content 必含完整 draft)                  │
└─────────────────────────────────────────────────────────────────────────┘
```

## 处理流程

一条客户消息从进来到回复:

1. **入口**:webhook(`tags=saas`)或 messaging 事件进来 → 提取客户消息 + 截图 → 入 `trigger_queue`
2. **触发判定**:`trigger.decider` 用 `gpt-5.5` 把最近几条客户消息分类成 6 种 verdict + 高风险 flag(支持 vision 看截图)
3. **分发**:按 verdict 决定路径(见上方架构图分发表)。需要主 Agent 处理的进**静默窗口**去抖
4. **主 Agent 循环**(`AgentLoop`):
   - `classify_ticket` 拿 11 种工单类型(platform_issue / billing / refund / workflow / api / permissions / knowledge_base / product_usage / insufficient_info / mention / general)+ 针对性策略
   - `zendesk_get_ticket` 拉对话 + 附件
   - 有 Workspace ID → 首轮 SQL 起手式(`sql_query` / `inspector_exec`,经 DB Gateway 只读)
   - 直接调工具查根因:`git_grep` / `github_code_search`(源码)、`signoz_errors` / `signoz_traceback`(日志)、`ticket_record_search`(历史工单)
   - 多证据源工单默认派 `run_subagents_parallel`：需要跨代码/文档/数据库/日志/历史工单/GitHub PR 任意 ≥2 类证据源时按来源拆给 subagent，最多 5 个
   - Confidence 自检 → 写 reply(public 或 internal)
5. **归档**:`archive_ticket` 写入 `工单记录/<id>.md`(首次新建,跟进追加)
6. **escalate**:退款/情绪/低置信度 → `feishu_push` 通知 SE/财务(content 内联完整 draft)

verdict / 高风险定义见 `src/zd_agent/trigger/decider.py`,Agent 工作流见 `src/zd_agent/prompts/AGENTS.md`。

## 工具层(45 个)

```text
Zendesk:  zendesk_get_ticket / get_attachments / search / search_articles
          get_article / get_user / add_tags
          zendesk_internal_reply        ← 写 Internal Note(给 SE)
          zendesk_follow_ticket         ← 关注工单(经 Zendesk MCP)
          zendesk_public_reply          ← 直接回客户(受 public_reply.enabled 门控)
知识库:    knowledge_search / knowledge_read          ← 本地 saas版知识库
          official_docs_search / official_docs_read   ← 官方在线文档
          zd_kb_cli_exec                              ← Help Center 文章
历史工单:  ticket_record_search / ticket_record_read  ← grep 工单记录/*.md
数据库:    sql_query(prod/billing/plugin,只读,经 DB Gateway)
          inspector_exec(跨 main+billing+TiDB 自动聚合,quota/run 类首选)
日志监控:  signoz_errors / signoz_traceback / signoz_services / signoz_query
源码:      git_grep / git_show / git_log / git_diff / list_repos / list_repo_tags
          github_search_prs / github_get_pr / github_code_search
          saas_branch_switch            ← 切 SaaS 仓库分支(对齐 prod commit)
Sandbox:  sandbox_list / search / read / write
环境:      run_script / sandbox_exec
分类归档:  classify_ticket / archive_ticket
飞书:      feishu_push(escalate 卡片 / 日报)
子代理:    run_subagent / run_subagents_parallel
```

> 数据库工具通过远程 DB Gateway MCP endpoint 调用同级项目 `../db-gateway`；
> MCP 只配置 `DB_GATEWAY_URL` 和 `DB_GATEWAY_API_KEY`，不持有 Teleport / DB 凭据。
> SigNoz 工具通过远程 Signoz MCP endpoint 调用同级项目 `../signoz-mcp`；
> agent 只配置 `SIGNOZ_MCP_URL` 和 `SIGNOZ_MCP_API_KEY`，不持有原始 `SIGNOZ_API_KEY`。
> Zendesk 读取 / 搜索 / Help Center / Internal Note / 标签 / 关注（包括工具层、
> trigger 拉消息、客户画像、mention poller、human-takeover 检测、ACK/error note）
> 通过远程 Zendesk MCP endpoint 调用同级项目 `../zendesk-mcp`；`zendesk_public_reply`
> 继续走 `zendesk-browser-relay`。附件发现和二进制下载都走 Zendesk MCP
> `download_attachment`，agent 只负责把 base64 内容落到 sandbox。
> 当前工具隔离边界以 `src/zd_agent/tools/service_boundary.py` 为准：`sql_query`、
> SigNoz 工具、`zendesk_public_reply`、Zendesk 读写路径已经走独立服务；
> Source 工具仍有本地 git fallback，属于 partial。下一批 Tool Service 迁移候选：
> `github-mcp` / `source-tool-service`（GitHub token、rate limit、PR revert/follow-up 校验）、
> `docs-tool-service`（llms.txt 缓存、限域访问）、`notification-service`（Feishu 凭据和发送重试）、
> `sandbox-worker`（读写/搜索/执行隔离到独立进程或容器）。

## 目录结构

```text
src/zd_agent/
  entry/              入口层
    cli.py              typer CLI(run / batch / serve / webhook / status / ...)
    saas_app.py         SaaS aiohttp app:webhook 路由 + 后台任务 + verdict 分发
    zendesk_webhook.py            经典 ticket webhook handler
    zendesk_messaging_webhook.py  messaging 事件 handler
    silence_waker.py / waiting_poller.py / mention_poller.py  后台轮询
  trigger/
    decider.py          触发判定(6 verdict + 高风险 flag,支持 vision)
    state_store.py      工单状态机(TicketStateTracker)
  session/            AgentSession / SessionManager / Sandbox 生命周期
  runtime/
    llm/                LLMProvider 抽象(openai_provider / anthropic_provider)
    prompt/             PromptBuilder(条件加载 + 静态/动态分段拉满 cache)
    agent_loop.py       核心循环(AsyncGenerator 事件流)
    subagent.py         Subagent 执行器(独立 message_store,mini 模型)
    subagent_parallel ...  并行/串行编排
    compaction.py       上下文压缩(锚点保护 + history snip + LLM 多级摘要)
    guardrails.py       护栏(轮次 / Token / 超时)
    customer_profile.py 客户画像预拉
  tools/              45 个工具(见上)
    service_boundary.py 当前工具运行边界和 Tool Service 迁移状态
  prompts/            Prompt 文件
    SOUL.md             身份与行为准则
    AGENTS.md           工作流总览(skeleton 风格)
    TOOLS.md            工具使用指南
    strategies/         11 种工单类型的针对性策略(lazy load)
    refund/REFUND_FLOW.md   退款专项流程
    references/         antipatterns / quality_bar / subagent_guide / reasonable_requests
    playbook/ sql/ templates/ public_reply/
  observability/      指标埋点 + modify_rate 测量

resources/(运行时数据,base_dir=/opt/zd-agent/resources)
  saas源代码/          6 个 SaaS 仓库 clone
  saas版知识库/        本地知识库 md
  zendesk-cli/工单记录/ 工单归档 <id>.md
  .state/              工单状态 / dedup / mention 记录

config.yaml            主配置
deploy/                Dockerfile / systemd / nginx / RUNBOOK.md

../db-gateway/         独立小项目；负责 Teleport / DB proxy / 续期 / DB Gateway API / MCP server
```

## 配置要点(config.yaml)

| 配置 | 当前值 | 说明 |
|---|---|---|
| `llm.model` | `gpt-5.5` | 主 Agent 模型 |
| `llm.subagent_model` | `gpt-5.4-mini` | Subagent 用便宜模型(省 ~70% 成本) |
| `trigger_decider.llm_model` | `gpt-5.5` | 触发判定模型(误判代价高,用 full) |
| `agent.guardrails.max_tokens_per_session` | `1500000` | 单工单累计 Token 上限 |
| `agent.guardrails.compaction_threshold` | `0.3` | 达上限 30% 触发压缩 |
| `agent.max_concurrent_sessions` | `2` | 2G 服务器档位;4G 可调到 5 |
| `public_reply.enabled` | `true` | **总开关**:false = 全部走 Internal Note |
| `public_reply.max_ask_count` | `10` | 累计 ask 上限,超过转人工 |
| `zendesk_mcp.mcp_url` | `''` | Zendesk 读取/Internal Note/标签/关注 MCP endpoint |
| `zendesk.min_ticket_id` | `2624` | **灰度边界**:只处理 ID ≥ 此值的工单,0 = 全量 |
| `zendesk.search_query` | `status:new tags:saas` | 处理范围 |

> gpt-5.x 系列不接受 `temperature` 参数(runtime 已自动跳过),用 `max_completion_tokens`。
> 模型名必须是 LLM proxy 真实存在的(`gpt-5.5-mini` 不存在,用 `gpt-5.4-mini`)。

## Webhook

两个入口,均在 `.env` 配置各自的签名 secret:

| Endpoint | 事件 | 触发条件 |
|---|---|---|
| `POST /webhook/zendesk` | 经典 ticket 事件 | `tags` 含 `saas` |
| `POST /webhook/zendesk-messaging` | `messaging_ticket.message_added` | messaging 工单客户发消息 |

messaging webhook 是实时主来源;`waiting_poller`(默认 300s)退化为低频兜底。签名校验用 `timestamp + raw_body` 算 SHA256 HMAC 后 base64 比对,时间戳 300s 窗口。

## 本地开发

```bash
uv sync                              # 安装依赖
cp .env.example .env                 # 填凭据(见下)
uv run zd-agent --help               # 查看 CLI
uv run pytest -q                     # 跑测试(当前 636 个)
```

`zd-agent` 使用 `/opt/zd-agent/.env`，只放 agent 自身和 DB Gateway MCP 调用凭据：

```env
OPENAI_API_KEY=sk-xxx
ZENDESK_SUBDOMAIN=your-subdomain
ZENDESK_EMAIL=you@example.com
ZENDESK_API_TOKEN=your-api-token
ZENDESK_WEBHOOK_SECRET=...           # 经典 webhook 签名
ZENDESK_MESSAGING_WEBHOOK_SECRET=... # messaging webhook 签名
ZENDESK_MCP_URL=https://<agent-public-domain>/zendesk-mcp
ZENDESK_MCP_API_KEY=<shared-secret>
FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_BOT_CHAT_ID
SIGNOZ_MCP_URL=https://<agent-public-domain>/signoz-mcp
SIGNOZ_MCP_API_KEY=<shared-secret>
GITHUB_TOKEN=ghp_xxx
DB_GATEWAY_URL=https://<agent-public-domain>/db-gateway
DB_GATEWAY_API_KEY=<shared-secret>
```

`db-gateway` 的配置、部署和 Teleport 凭据在同级项目 `../db-gateway`，不属于本仓库。
`signoz-mcp` 的配置、部署和 SigNoz 原始凭据在同级项目 `../signoz-mcp`，不属于本仓库。
`zendesk-mcp` 的配置、部署和 Zendesk 原始 API 凭据在同级项目 `../zendesk-mcp`，不属于本仓库。

### 常用 CLI

```bash
uv run zd-agent run <ticket-id>            # 处理单个工单
uv run zd-agent run <ticket-id> --dry-run  # 只分析不发送
uv run zd-agent batch 2624 2625 --dry-run  # 批量
uv run zd-agent serve                       # 启动 SaaS app(webhook + 后台任务)
uv run zd-agent status                      # 会话状态
uv run zd-agent tool list                   # 已注册工具
uv run zd-agent repos status                # SaaS 仓库状态
```

> 生产容器入口即 `zd-agent serve`(见 `deploy/Dockerfile`)。

## 多 LLM 厂商

通过 `LLMProvider` 抽象支持 OpenAI / Anthropic 切换:

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-20250514
  max_tokens: 8192      # Anthropic 必传;OpenAI 设 0 = 不限制
```

或用环境变量覆盖:`ZD_LLM_PROVIDER` / `ZD_MODEL` / `ANTHROPIC_API_KEY`。

## 部署

生产部署在 `<agent-server>`，Docker + systemd。`zd-agent` 只运行 Zendesk agent；同级 `db-gateway` 项目负责 Teleport、DB proxy、Teleport 续期和只读 SQL API。完整步骤、内存档位、健康检查见 **`deploy/RUNBOOK.md`**。

推送到远端 `main` 后，GitHub Actions 会构建镜像、推送到 GHCR、通过 SSH
部署本次提交对应的 `sha-<commit-sha>` 镜像。SMTP 通知 secrets 已配置时，
会在健康检查成功后发送邮件；未配置时只跳过邮件，不影响部署结果。

CI/CD 部署时 workflow 会用本次 `GITHUB_TOKEN` 临时登录 GHCR；手动部署
private package 时，服务器需要先完成一次 GHCR 登录，使用只带 `read:packages`
权限的 PAT。GitHub 仓库还需要配置部署 SSH secrets；SMTP 通知 secrets 可选。
完整清单见 `deploy/RUNBOOK.md`。

手动更新到 `main` 最新镜像:

```bash
/opt/zd-agent/code/deploy/deploy-ghcr.sh main
```

回滚到指定提交镜像:

```bash
/opt/zd-agent/code/deploy/deploy-ghcr.sh sha-<commit-sha>
```

## 归档与调试

每次处理后归档到 `resources/zendesk-cli/工单记录/<id>.md`,含内部分析、回复草稿、关键证据。`--dry-run` 模式适合先看归档再决定是否正式投递。

## 相关文档

- `deploy/RUNBOOK.md`:部署运维手册(权威操作文档)
- `src/zd_agent/prompts/AGENTS.md`:Agent 工作流总览
- `src/zd_agent/prompts/SOUL.md`:身份与行为准则
- `ai-docs/design/`:历史设计文档(特定时间点快照,不追踪当前状态)
