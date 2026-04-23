# Zendesk SaaS Agent 改造设计

**日期**：2026-04-22
**作者**：yyg + Claude (brainstorming session)
**状态**：Draft，待 review

---

## 1. 概要

将现有 `zendesk-agent` 项目（当前专注 Dify 企业版技术支持）原地重构为 **Dify SaaS 技术支持自动化 Agent**，部署到云服务器 24/7 运行。工作流完全迁移到 `dify-support` skill 的 SaaS 处理规范（Step 0-22），引入 PostgreSQL 生产库直查、Grafana / SigNoz 监控、飞书机器人双向交互、Webhook 驱动的触发判定等新能力。

## 2. 背景与动机

### 2.1 当前项目状态

`zendesk-agent` 是为 Dify 企业版支持工程师设计的 LLM Agent 系统，主要能力：
- 基于 LLM 循环 + 36 个工具处理 Zendesk 工单
- Helm 版本查询、源码 worktree 分析
- 生成三段式 Internal Note
- 支持 CLI / Scheduler / Webhook 三种入口

### 2.2 改造目标

- **业务场景切换**：从企业版（私有化 / 自部署）改为 SaaS（Dify Cloud）
- **部署形态**：从本地手动运行改为云服务器 24/7 无人值守
- **流程规范**：完全对齐 `dify-support` skill 的 SaaS SOP（Section 2.1 Step 0-22）
- **交互模式**：新增飞书机器人双向通信（推送通知 + 接收指令）
- **数据能力**：新增 PostgreSQL 生产库只读查询、Grafana/SigNoz 监控查询

### 2.3 非目标（明确不做）

- 不再处理企业版工单（Helm / Worktree 相关能力全部删除）
- 不支持多用户 / 多租户（单实例为 Dify 内部使用）
- 不做 public reply 自动发送（只写 Internal Note，对齐 skill 约束）
- 不做完整的 Web UI（只用飞书卡片交互）
- Cloudflare 日志查询暂不纳入

---

## 3. 设计决策（brainstorming 阶段已锁定）

| # | 决策项 | 结论 |
|---|--------|------|
| 1 | 改造模式 | 原地彻底替换（方向 A），仅处理 SaaS |
| 2 | 工具实现方式 | Grafana/SigNoz 用 HTTP API + key；psql 子进程；Zendesk CLI 补充；GitHub 保留 PyGithub |
| 3 | 数据库访问 | psql 连接，地址可配置（Teleport `localhost:56061` 或直连都支持） |
| 4 | Teleport 认证 | Headless Login + AuthWatchdog（过期前推飞书，用户点 URL 认证） |
| 5 | 部署环境 | 百度云 BCC，Debian 11，Docker + systemd + Nginx 反代 |
| 6 | HTTPS 域名 | `zd-agent.smlershou.top`（Let's Encrypt） |
| 7 | LLM 主模型 | `gpt-5.4`（沿用当前配置） |
| 8 | LLM 预判模型 | `gpt-5.4-mini`（触发判定用） |
| 9 | 工单过滤 | `status:new tags:saas` |
| 10 | 触发架构 | Webhook 主 + Scheduler 兜底（5 分钟） + 静默期唤醒器（30 秒） |
| 11 | 静默期策略 | 默认 3 分钟；连发检测（30s 内 2+ 条）自动延长到 5 分钟 |
| 12 | Mention 遥控 | 保留，改账号为 `@saas-bot`（需注册） |
| 13 | 飞书机器人 | 方案 B（双向基础，支持 9 个指令）+ 每日 09:00 summary |
| 14 | 产出形态 | Internal Note + 飞书通知（A 方案，不做二次审批闸门） |
| 15 | 退款工单 | 纳入自动处理（A 方案，产出客户回复草稿 + 财务申请草稿） |
| 16 | 工单记录同步 | 单向初始化 + 双方独立（云上 agent 和本地 skill 各写各的） |
| 17 | Subagent 并行 | 新增 `run_subagents_parallel` 工具 + Prompt 强制并行派发 |
| 18 | 资源路径 | `<resources_dir>` 配置分离代码与数据；状态在 `.state/` 子目录 |

---

## 4. 高层架构

```
┌─ 百度云 BCC (180.76.243.249, Debian 11, 建议升级到 4G) ─────────────┐
│                                                                     │
│  [已有服务，保持不动]                                                │
│  ├─ Nginx (80/443) 多站点                                           │
│  ├─ api-relay 4 个容器                                              │
│  └─ Let's Encrypt 已管理多域名                                      │
│                                                                     │
│  [新增服务]                                                          │
│  ├─ Nginx site: zd-agent.smlershou.top                              │
│  │     ├─ /webhook/zendesk → 127.0.0.1:8080                         │
│  │     └─ /webhook/feishu  → 127.0.0.1:8080                         │
│  │                                                                  │
│  ├─ Docker: zd-agent:latest                                         │
│  │     ├─ Python 3.12 / aiohttp / uv                                │
│  │     ├─ 监听 127.0.0.1:8080                                       │
│  │     ├─ --network host（方便连 localhost:56061）                  │
│  │     └─ mem 1.5G / cpu 1.0                                        │
│  │                                                                  │
│  └─ systemd 服务:                                                   │
│      ├─ zd-agent.service                                            │
│      ├─ tsh-proxy-db.service                                        │
│      └─ tsh-auth-watchdog.service                                   │
│                                                                     │
│  [资源目录] /opt/zd-agent/resources/                                │
│  ├─ saas源代码/{dify,billing,mp-be,mp-fe,deploy}                    │
│  ├─ saas版知识库/                                                   │
│  ├─ zendesk-cli/工单记录/                                           │
│  ├─ 工单附件/                                                       │
│  └─ .state/                                                         │
└─────────────────────────────────────────────────────────────────────┘

                ▲ HTTPS              ▲ Feishu Webhook
                │                    │
  [Zendesk]─────┘         [飞书机器人]┘
       │
       │                         ┌───── 推送 ─────→ [飞书群]
       │ Agent API                │
       ↓                         │
  agent 以 ZENDESK_EMAIL         │
  (saas-bot@dify.ai) 调用 ──────┘
  - 读工单 / 评论
  - 写 Internal Note
```

### 4.1 主要模块

```
src/zd_agent/
├── entry/
│   ├── cli.py              # 沿用（本地测试用）
│   ├── scheduler.py        # 沿用，改查询条件 + 降频兜底
│   ├── webhook.py          # 升级：支持 Zendesk + 飞书两类 webhook
│   └── silence_waker.py    # 新增：每 30s 扫静默期到期工单
│
├── session/                # 沿用，AgentSession 新增 mode(first_pass/followup)
│
├── runtime/
│   ├── agent_loop.py       # 沿用
│   ├── llm/                # 沿用
│   ├── prompt/
│   │   └── builder.py      # 扩充：按工单分类动态加载 prompt 段
│   ├── messages.py         # 沿用
│   ├── guardrails.py       # 沿用
│   └── compaction.py       # 沿用
│
├── trigger/                # 新增模块：触发判定
│   ├── decider.py          # LLM 预判 + 决策树
│   ├── state_store.py      # TicketStateTracker（持久化到 .state/tickets.json）
│   └── silence.py          # 静默期管理（含连发检测）
│
├── integrations/           # 新增模块：外部系统
│   ├── feishu/
│   │   ├── client.py       # lark-oapi 封装：推送 + 接收
│   │   ├── cards.py        # 卡片模板（ticket_done/escalate/auth_renew/summary）
│   │   └── commands.py     # 飞书指令处理（9 个指令）
│   ├── postgres.py         # psql 执行器（只读校验 + 超时 + 审计）
│   ├── grafana.py          # Grafana HTTP API client
│   ├── signoz.py           # SigNoz HTTP API client
│   └── teleport.py         # tsh status 解析 + headless login 触发
│
├── mention/                # 沿用，改配置
│
├── tools/                  # 工具集重构（详见 §5.3）
│
├── prompts/                # Prompt 重写（详见 §5.4）
│
├── repos.py                # 沿用，支持 5 个 SaaS 仓库
├── config.py               # 扩充配置项
└── log.py                  # 沿用
```

---

## 5. 详细设计

### 5.1 入口层

#### 5.1.1 Webhook（主入口）

**Zendesk Webhook**：

- 端点：`https://zd-agent.smlershou.top/webhook/zendesk`（Nginx 转发 → 127.0.0.1:8080/webhook/zendesk）
- 认证：HMAC SHA-256 签名校验，secret 存 `.env` 的 `ZENDESK_WEBHOOK_SECRET`
- Zendesk Trigger 条件：`Ticket Status is New AND Tags contain saas AND (Ticket is Created OR Updated)`
- Payload 字段：`ticket_id`, `latest_comment_id`, `latest_comment_author_id`, `latest_comment_is_public`, `tags`, `updated_at`
- 幂等性：基于 `latest_comment_id` 去重（存 `.state/processed_webhooks.json`，保留最近 10000 条）

**飞书 Webhook**：

- 端点：`https://zd-agent.smlershou.top/webhook/feishu`
- 认证：飞书官方 Encrypt Key + Verify Token（lark-oapi 原生支持）
- 订阅事件：`im.message.receive_v1`
- 指令路由（详见 §5.2.3）

#### 5.1.2 Scheduler（兜底）

- 频率：每 **5 分钟**（从 60s 降频，因 webhook 已承担实时性）
- 查询：`status:new tags:saas updated_at>{last_poll}`
- 目的：补漏 webhook 失败 / agent 宕机期间的事件
- 发现的工单 → 走 TriggerDecider 统一决策（不绕过）

#### 5.1.3 Silence Waker（静默期唤醒器）

- 频率：每 **30 秒**
- 扫 `.state/tickets.json` 中 `state == "in_silence_window"` 的工单
- 判断静默期是否到期：
  - 到期 → 启动 AgentSession 完整处理 → 状态置 `processed`
  - 未到期 → 略过

#### 5.1.4 Mention Watcher

- 沿用现有 [mention/](src/zd_agent/mention/) 模块
- `config.yaml: mention_trigger: "@saas-bot"`（替换 `@support-agent`）
- 触发后**绕过** TriggerDecider，直接进 AgentSession

### 5.2 触发判定层

新建模块 `src/zd_agent/trigger/`。

#### 5.2.1 TicketStateTracker

持久化到 `<resources_dir>/.state/tickets.json`：

```json
{
  "12345": {
    "state": "in_silence_window",
    "first_seen": "2026-04-22T10:00:00Z",
    "last_customer_comment_id": 67890,
    "last_comment_ts": "2026-04-22T10:15:30Z",
    "last_eval_at": "2026-04-22T10:15:35Z",
    "silence_window_end": "2026-04-22T10:18:35Z",
    "processed_count": 0,
    "last_verdict": "substantial_issue"
  }
}
```

状态机：

```
new → [LLM 预判 + 跟进模式检查]
  ├─ has_record → followup → AgentSession(followup) → processed
  ├─ greeting → waiting_for_issue
  ├─ insufficient_info → waiting_for_issue
  └─ substantial_issue → in_silence_window

in_silence_window:
  ├─ 新消息 + completeness 上升 → 重置静默期
  ├─ 新消息 + 连发检测触发 → 静默期延长到 5 分钟
  ├─ 新消息 + verdict 退化 → 退回 waiting_for_issue
  └─ 静默期到 → processing → processed

processed:
  └─ 新客户消息 → 进入跟进模式
```

**清理**：
- `processed` 保留 30 天
- `waiting_for_issue` > 6 小时无活动 → 下次新消息到达时重新判定
- 总记录数 > 10000 → LRU 淘汰

#### 5.2.2 TriggerDecider 决策流程

```python
async def decide(ticket, state) -> Decision:
    # 1. 跟进模式（已有工单记录）
    if has_record(ticket.id):
        new_comments = diff_since_last_record(ticket)
        if not new_comments:
            return SKIP
        return PROCESS_FOLLOWUP

    # 2. 强制触发
    if ticket.has_mention("@saas-bot") or has_force_flag(ticket.id):
        return PROCESS_NOW

    # 3. 无客户消息
    if not ticket.customer_comments:
        return SKIP

    # 4. LLM 预判（gpt-5.4-mini）
    verdict = await llm_classify(ticket.customer_comments)
    # → {nature, completeness, reason}

    # 5. 决策树
    if verdict.nature == "greeting":
        state.update(ticket.id, "waiting_for_issue")
        if state.greeting_only_duration(ticket.id) > 30 * MIN:
            feishu.push_alert(f"工单 #{ticket.id} 30 min 无实质内容，请人工评估")
        return SKIP

    if verdict.nature == "substantial_issue":
        silence_duration = compute_silence(ticket)  # 3 或 5 min
        state.update(ticket.id, "in_silence_window",
                     silence_window_end=now() + silence_duration)
        feishu.push_info(f"工单 #{ticket.id} 判定有效，{silence_duration}min 后处理")
        return ENTER_SILENCE

    if verdict.nature == "insufficient_info":
        last_msg_age = now() - ticket.last_comment_ts
        if last_msg_age > 20 * MIN:
            return PROCESS_WITH_CLARIFICATION
        return WAIT

def compute_silence(ticket) -> timedelta:
    # 连发检测：最近 30s 内有 2+ 条客户消息 → 延长到 5 分钟
    recent = [c for c in ticket.customer_comments
              if (now() - c.created_at) < 30]
    if len(recent) >= 2:
        return timedelta(minutes=5)
    return timedelta(minutes=3)
```

#### 5.2.3 飞书指令集

| 指令 | 说明 |
|------|------|
| `/branch <repo> <branch>` | 切换指定 SaaS 仓库到指定分支（5 仓库别名：dify/billing/mb/mf/deploy） |
| `/fetch-all` | 所有 SaaS 仓库全量 fetch + 各自当前分支 pull |
| `/reprocess <工单号>` | 忽略已处理状态重跑 |
| `/process <工单号>` | 跳过静默期立即处理 |
| `/status` | 整体状态（今日处理数、队列、错误） |
| `/status <工单号>` | 单工单状态 |
| `/pause` / `/resume` | 控制自动轮询（不重启进程） |
| `/relogin` | 立即触发 Teleport headless renew |
| `/help` | 列出所有指令 |

每日 **09:00** 推送 summary 卡片（昨日工单量、成功/失败、退款数、token 消耗、Teleport 剩余时间）。

#### 5.2.4 决策日志

每次决策写一行 JSONL 到 `<resources_dir>/.state/logs/trigger_log.jsonl`：

```json
{"ts":"2026-04-22T10:15:35Z","ticket":12345,"verdict":"substantial_issue","completeness":85,"rhythm":"slow","silence_min":5,"action":"enter_silence"}
```

### 5.3 工具层

#### 5.3.1 工具改动总表

| 工具 | 动作 | 说明 |
|------|------|------|
| Helm 系列（HelmListVersions 等） | 🗑️ 删 | Enterprise 专属 |
| Worktree 系列（sandbox_create_worktree 等） | 🗑️ 删 | SaaS 不用 worktree |
| GitShow/Grep/Log/Diff | ♻️ 简化 | 直接在 `saas源代码/<repo>/` 下执行 |
| ZendeskGetTicket / GetAttachments / Search / SearchArticles / Assign / InternalReply | ✅ 保留 | 沿用 |
| ClassifyTicket | ♻️ 调整 | 分类改为 SaaS 场景（对齐 saas版知识库 01-08） |
| ArchiveTicket | ♻️ 调整 | 归档到 `zendesk-cli/工单记录/<id>.md` |
| GithubSearchPrs / GetPr | ✅ 保留 | 沿用 PyGithub |
| RunSubagent | ✅ 保留 | 保留（单任务场景） |
| **RunSubagentsParallel** | ➕ 新 | 一次派发多个并行 subagent |
| **SqlQuery** | ➕ 新 | 只读 psql 查询 |
| **GrafanaListDashboards / GetDashboard / QueryPrometheus** | ➕ 新 | Grafana API |
| **SignozSearchLogs / SearchTraces / ListServices / GetFieldKeys / GetFieldValues** | ➕ 新 | SigNoz API |
| **FeishuPush** | ➕ 新 | 主动推送 |
| **TicketRecordSearch / Read** | ➕ 新 | 历史工单 Grep |
| **SaasBranchSwitch** | ➕ 新 | 5 仓库切分支 |
| **ZdKbCliExec** | ➕ 新（可选） | 执行 `zd kb search/article` |

总计：原 36 → 精简到 ~30。

#### 5.3.2 SqlQuery（最关键的新工具）

```python
class SqlQuery(Tool):
    name = "sql_query"
    description = "在 SaaS 生产只读库执行 SELECT。仅允许只读。"
    parameters = {
        "database": Literal["prod", "billing", "plugin"],
        "sql": str,
        "limit": int,  # default 1000
    }
    
    async def execute(self, args, session):
        sql = args["sql"]
        # 三重只读校验
        if not validate_select_only(sql):
            return ToolResult.error("仅允许 SELECT，禁止 DDL/DML")
        
        db_map = {
            "prod": cfg.pg.db_prod,
            "billing": cfg.pg.db_billing,
            "plugin": cfg.pg.db_plugin,
        }
        db_name = db_map[args["database"]]
        
        try:
            result = await asyncio.wait_for(
                run_psql(db_name, sql, limit=args.get("limit", 1000)),
                timeout=30,
            )
            audit_log(args["database"], sql, "OK", session.ticket_id)
            return ToolResult.ok(result.as_csv())
        except TeleportAuthError:
            feishu.push_auth_renew_needed()
            session.mark_waiting_for_auth()
            return ToolResult.error("Teleport 认证过期，已推飞书请求 renew")
        except asyncio.TimeoutError:
            audit_log(args["database"], sql, "TIMEOUT", session.ticket_id)
            return ToolResult.error("SQL 超时 30 秒")
```

#### 5.3.3 RunSubagentsParallel

```python
class RunSubagentsParallel(Tool):
    name = "run_subagents_parallel"
    description = "一次并行派发多个 subagent。用于 Step 10-14 根因分析和 Step 15-18 知识匹配阶段。"
    parameters = {
        "tasks": [  # list
            {"task": str, "tools": [str], "context": str}
        ]
    }
    concurrency_safe = True
    
    async def execute(self, args, session):
        tasks = args["tasks"]
        results = await asyncio.gather(*[
            run_subagent(t["task"], t.get("tools", []), t.get("context", ""), session)
            for t in tasks
        ], return_exceptions=True)
        return format_parallel_results(results)
```

#### 5.3.4 工具分组 & 动态加载

按 `classify_ticket` 结果注入对应工具组，节省主 agent context：

```yaml
tool_groups:
  always: [zendesk_*, feishu_push, classify_ticket, archive_ticket,
           run_subagent, run_subagents_parallel]
  platform_issue: [grafana_*, signoz_*]
  database_query: [sql_query]
  workflow: [sql_query]  # workflow 问题常查 workflow_runs
  knowledge_base: [sql_query]  # 查 documents
  code_analysis: [git_*, github_*]
  knowledge_search: [ticket_record_*, zd_kb_cli_exec]  # subagent 用
  refund: [sql_query]  # 查订阅/退款状态
```

### 5.4 Prompt 与工作流

#### 5.4.1 目录结构

```
src/zd_agent/prompts/
├── SOUL.md                        # 重写
├── AGENTS.md                      # 重写，Step 0-22
├── TOOLS.md                       # 更新
├── refund/
│   └── REFUND_FLOW.md             # 新增，skill Section 11
├── templates/
│   ├── REPLY_TEMPLATES.md         # 新增
│   └── DEV_REPORT.md              # 新增
├── playbook/
│   └── COMMON_PLAYBOOK.md         # 新增，快速分诊
└── sql/
    └── SQL_TEMPLATES.md           # 新增，从 saas版知识库/10 复制
```

#### 5.4.2 条件加载规则

```python
# always（每 session 加载，~5K tokens）
SOUL + AGENTS + TOOLS + COMMON_PLAYBOOK

# 动态追加
if session.mode == "followup":
    inject followup_context(previous_record, new_comments_diff)

# Step 4 classify 后按 category 加载
category_prompts = {
    "refund": [REFUND_FLOW, SQL_TEMPLATES],
    "billing": [SQL_TEMPLATES, REFUND_FLOW],
    "platform_issue": [MONITORING_GUIDE],
    "workflow": [SQL_TEMPLATES],
    "knowledge_base": [SQL_TEMPLATES],
    "api": [],
    "permissions": [],
    "product_usage": [],
}

# 进入回复起草阶段
if phase == "reply_drafting":
    inject REPLY_TEMPLATES
```

#### 5.4.3 Runtime 上下文注入

AgentSession 启动时注入：

```
# Runtime 状态
- 工单：#{ticket_id}
- 分类预判：{category}
- 模式：first_pass | followup
- SaaS 源码分支：
  - dify:     {branch} @ {commit_short}
  - billing:  {branch} @ {commit_short}
  - mp-be:    {branch} @ {commit_short}
  - mp-fe:    {branch} @ {commit_short}
  - deploy:   {branch} @ {commit_short}
- Teleport：剩余 {X} 小时
```

#### 5.4.4 AGENTS.md 核心大纲

```markdown
# Dify SaaS Support Agent Workflow

## 角色
你是 Dify SaaS 支持工程师的自动化助手，处理 Zendesk tags=saas 的工单。
产出 Internal Note 供 support engineer 参考。
⚠️ 绝不发 public reply；⚠️ 绝不执行写 SQL；⚠️ 绝不自启 Teleport。

## 核心原则
- 双语输出：EN（前）+ 中文（后），EN ≤ 150 词，CN ≤ 200 字（不含签名）
- 签名："Best regards,\n\nDify Support" / "此致\n\nDify Support"
- 文档链接用 "文档名：URL"（禁 Markdown 超链接）
- 有 Help Center 文章直接附链接

## 工作流（Step 0-22）
Step 0:  跟进模式识别（系统自动注入 previous_record）
Step 1:  zendesk_get_ticket
Step 2:  COMMON_PLAYBOOK 快速匹配 → 命中 → 跳 Step 19
Step 3:  解析 tags 确认 saas
Step 4:  classify_ticket
Step 5:  First-Pass Intake（Workspace ID / 错误 / 复现路径）
Step 6:  优先级判断（urgent → 先发简短初步响应）
Step 7:  附件下载（有附件时）
Step 8:  [有 Workspace ID] SQL 万能起手式（参考 SQL_TEMPLATES）
Step 9:  已注入源码分支信息，无需再查

## 根因分析（Step 10-14）⚡⚡⚡ 强制并行
必须用 run_subagents_parallel 一次派发最多 4 个 subagent：
  A: 源码分析
  B: GitHub Issues/PRs
  C: [platform_issue] Grafana
  D: [platform_issue] SigNoz

## 知识匹配（Step 15-18）⚡⚡⚡ 强制并行
必须用 run_subagents_parallel 一次派发最多 4 个 subagent：
  A: Help Center (zd_kb_cli_exec)
  B: 历史工单 (ticket_record_search)
  C: 本地知识库 (sandbox_read saas版知识库/)
  D: Web 搜索（A-C 无匹配时）

## 输出（Step 19-22）
Step 19: Internal Note 三段式（内部分析 / 客户回复草稿 / 人工指引）
Step 20: archive_ticket → 工单记录
Step 21: 判断是否写入本地知识库
Step 22: feishu_push 推送卡片
```

### 5.5 部署 & 运维

#### 5.5.1 部署架构

- **宿主机**：百度云 BCC Debian 11，**建议升级 2G → 4G**
- **部署单元**：
  - `zd-agent.service`（systemd）→ 管理 Docker 容器
  - `tsh-proxy-db.service` → Teleport DB 端口转发
  - `tsh-auth-watchdog.service` → 认证过期前推飞书
  - Nginx 新增 `zd-agent.conf` → HTTPS 反代到 127.0.0.1:8080
- **网络**：容器 `--network host`，监听 127.0.0.1:8080；Nginx 处理公网入口

#### 5.5.2 Dockerfile 核心

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY scripts ./scripts
COPY config.yaml ./config.yaml
# zendesk-cli 作为 dependency 装进镜像

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uv", "run", "zd-agent", "serve"]
```

#### 5.5.3 Nginx 配置

```nginx
server {
    listen 80;
    server_name zd-agent.smlershou.top;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl http2;
    server_name zd-agent.smlershou.top;
    ssl_certificate     /etc/letsencrypt/live/zd-agent.smlershou.top/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/zd-agent.smlershou.top/privkey.pem;
    client_max_body_size 10m;
    location /webhook/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Host $host;
        proxy_read_timeout 60s;
    }
}
```

#### 5.5.4 环境变量（`.env` 模板，真实值只写服务器）

```bash
# Zendesk
ZENDESK_SUBDOMAIN=xxx
ZENDESK_EMAIL=saas-bot@dify.ai
ZENDESK_API_TOKEN=${ZENDESK_API_TOKEN}
ZENDESK_WEBHOOK_SECRET=${ZENDESK_WEBHOOK_SECRET}

# LLM
OPENAI_API_KEY=${OPENAI_API_KEY}

# Feishu
FEISHU_APP_ID=${FEISHU_APP_ID}
FEISHU_APP_SECRET=${FEISHU_APP_SECRET}
FEISHU_VERIFY_TOKEN=${FEISHU_VERIFY_TOKEN}
FEISHU_ENCRYPT_KEY=${FEISHU_ENCRYPT_KEY}
FEISHU_BOT_CHAT_ID=${FEISHU_BOT_CHAT_ID}

# Grafana / SigNoz
GRAFANA_URL=https://grafana.ops.dify.dev
GRAFANA_API_KEY=${GRAFANA_API_KEY}
GRAFANA_PROM_DATASOURCE_ID=1

SIGNOZ_URL=https://dify.us2.signoz.cloud
SIGNOZ_API_KEY=${SIGNOZ_API_KEY}

# PostgreSQL
PG_HOST=localhost
PG_PORT=56061
PG_USER=pro_ops_teleport_read
PG_PASSWORD=${PG_PASSWORD}
PG_DB_PROD=langgenius
PG_DB_BILLING=billing
PG_DB_PLUGIN=dify_plugin_daemon

# GitHub
GITHUB_TOKEN=${GITHUB_TOKEN}

# Teleport
TELEPORT_PROXY=teleport.dify.dev
TELEPORT_USER=yangyonggang
```

#### 5.5.5 资源 & 状态布局

```
/opt/zd-agent/
├── code/                              # 项目代码（git clone）
├── .env                               # 生产凭证（chmod 600）
└── resources/                         # config.yaml: resources_dir
    ├── saas源代码/{dify,...}           # 5 仓库
    ├── saas版知识库/
    ├── zendesk-cli/工单记录/           # 初始 1209 条从本地 rsync
    ├── 工单附件/
    └── .state/
        ├── tickets.json               # 工单状态机
        ├── silence_windows.json
        ├── processed_mentions.json
        ├── processed_webhooks.json
        ├── daily_stats.json
        ├── teleport_auth.json
        └── logs/
            ├── agent.log
            ├── trigger_log.jsonl
            ├── sql_audit.log
            ├── webhook.log
            └── feishu.log
```

#### 5.5.6 备份

```bash
# cron 每日 02:00
tar czf /backup/zd-agent-$(date +%Y%m%d).tar.gz \
    /opt/zd-agent/resources/.state \
    /opt/zd-agent/resources/zendesk-cli/工单记录
find /backup -name "zd-agent-*.tar.gz" -mtime +7 -delete
```

---

## 6. 改造清单（文件级别）

### 6.1 删除

- `src/zd_agent/tools/helm_*.py`（全部 Helm 工具）
- `src/zd_agent/tools/sandbox_worktree.py`（如存在）
- `src/zd_agent/prompts/AGENTS.md` 的 Enterprise 版本（重写）
- `ai-docs/design/` 下所有 Enterprise 架构文档（保留用作参考，但 prompt 不再加载）
- `tests/test_helm_*.py`（对应工具测试）

### 6.2 新增

- `src/zd_agent/trigger/` 整个模块
- `src/zd_agent/integrations/{feishu,postgres,grafana,signoz,teleport}/`
- `src/zd_agent/tools/sql_query.py`
- `src/zd_agent/tools/grafana_tools.py`
- `src/zd_agent/tools/signoz_tools.py`
- `src/zd_agent/tools/feishu_tools.py`
- `src/zd_agent/tools/ticket_record.py`
- `src/zd_agent/tools/saas_branch.py`
- `src/zd_agent/tools/subagent_parallel.py`
- `src/zd_agent/tools/zd_kb_cli.py`（可选）
- `src/zd_agent/entry/silence_waker.py`
- `src/zd_agent/prompts/refund/REFUND_FLOW.md`
- `src/zd_agent/prompts/templates/REPLY_TEMPLATES.md`
- `src/zd_agent/prompts/templates/DEV_REPORT.md`
- `src/zd_agent/prompts/playbook/COMMON_PLAYBOOK.md`
- `src/zd_agent/prompts/sql/SQL_TEMPLATES.md`
- `scripts/auth-watchdog.py`
- `scripts/init-resources.sh`（首次初始化资源目录）
- `deploy/Dockerfile`
- `deploy/systemd/zd-agent.service`
- `deploy/systemd/tsh-proxy-db.service`
- `deploy/systemd/tsh-auth-watchdog.service`
- `deploy/nginx/zd-agent.conf`
- 测试：每个新工具对应的 unit test

### 6.3 修改

- `src/zd_agent/prompts/SOUL.md`（重写 SaaS 身份）
- `src/zd_agent/prompts/AGENTS.md`（重写 Step 0-22）
- `src/zd_agent/prompts/TOOLS.md`（更新工具列表）
- `src/zd_agent/runtime/prompt/builder.py`（扩充条件加载）
- `src/zd_agent/session/agent_session.py`（新增 mode: first_pass / followup）
- `src/zd_agent/entry/scheduler.py`（查询条件 + 降频）
- `src/zd_agent/entry/webhook.py`（升级：Zendesk + 飞书双 webhook）
- `src/zd_agent/mention/detector.py`（可能微调，主要改 config）
- `src/zd_agent/tools/archive.py`（归档目录改为 `zendesk-cli/工单记录/`）
- `src/zd_agent/tools/classify.py`（分类改为 SaaS 场景）
- `src/zd_agent/tools/zendesk.py`（可能微调 Search 参数）
- `src/zd_agent/tools/subagent.py`（保留但并行版本优先）
- `src/zd_agent/config.py`（新增配置项）
- `config.yaml`（新增所有新配置）
- `.env.example`（新增所有新变量）
- `pyproject.toml`（加依赖：`lark-oapi`、`httpx` 如未有、`psycopg2-binary` 可选）
- `README.md`（更新）

---

## 7. 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| Teleport 认证过期，客户在等 | DB 查询失败，工单积压 | AuthWatchdog 提前 30 min 推飞书；过期后工单标记 `waiting_for_auth`，重认证后自动重入队 |
| 内存 1.8G 不够 | OOM，容器被 kill | 强烈建议升级到 4G；Docker `--memory=1500m` 限制，避免影响其他容器 |
| Webhook 丢事件 | 工单漏处理 | Scheduler 5 min 兜底 + 基于 `latest_comment_id` 幂等 |
| LLM 预判判错（把真问题判为寒暄） | 延迟处理 | 30 min 无实质内容推飞书人工评估；support engineer 可 `@saas-bot` 强制触发 |
| 静默期过短/过长 | 漏信息 / 响应慢 | 连发检测延长到 5 min；drive 日志便于复盘调参 |
| psql 误跑写 SQL | 破坏生产数据 | 三重校验（正则 + SQL parser + 只读账号权限）；超时 30s；审计日志 |
| 飞书指令被误发 | 误操作 | 敏感指令（`/pause`、`/branch`）加二次确认按钮 |
| 共享服务器资源冲突 | 影响现有 api-relay 等 | Docker 资源限制；Nginx 新 server 块不动现有配置 |
| 密码 / API key 泄露 | 账户被劫持 | .env 权限 600 不上 git；定期轮换；禁用 SSH 密码登录改 key |
| 工单记录 Git 冲突（如后续改为同步） | 数据丢失 | 暂不同步；如后续要同步用独立 repo + 自动 commit + 手工 pull |
| 代码 bug 导致无限循环 | Token 爆炸 | Guardrails（max_rounds=30, max_tokens_per_session=1.05M，沿用）|

---

## 8. 验收标准

### 8.1 功能验收（MVP）

- [ ] 服务器部署成功，`https://zd-agent.smlershou.top/webhook/zendesk` 返回 200（带 POST）
- [ ] Zendesk 建一个带 `tags:saas status:new` 的测试工单，webhook 触发
- [ ] LLM 预判（gpt-5.4-mini）能区分 "hi" vs 实质问题
- [ ] 静默期 3 分钟到期后启动 AgentSession
- [ ] 连发场景（30s 内 2+ 消息）静默期自动延长到 5 分钟
- [ ] AgentSession 能完整跑通 Step 0-22
- [ ] 根因分析阶段观察到 4 个 subagent 并行
- [ ] 知识匹配阶段观察到 4 个 subagent 并行
- [ ] Internal Note 按三段式写入（内部分析 / 客户回复草稿 / 人工指引）
- [ ] `archive_ticket` 写入 `zendesk-cli/工单记录/<id>.md`
- [ ] 飞书收到处理完成卡片
- [ ] 飞书发 `/branch dify hotfix/xxx` 能切分支
- [ ] 飞书发 `/reprocess 12345` 能重跑
- [ ] 飞书发 `/status` 能返回状态
- [ ] `@saas-bot` mention 能绕过静默期立即触发
- [ ] 跟进模式：同一工单第二次处理能读 previous_record，不重复分析
- [ ] SQL 工具拒绝写 SQL（注入测试：`SELECT * FROM tenants; DROP TABLE tenants;`）
- [ ] Teleport 过期场景：psql 失败 → 飞书推送 renew URL → 认证后工单自动重入队
- [ ] 退款工单：Internal Note 包含客户回复草稿 + 财务申请草稿
- [ ] 每日 09:00 飞书收到 summary 卡片

### 8.2 性能验收

- [ ] 单工单处理平均时长 < 3 分钟（含 LLM + 工具调用）
- [ ] Token 消耗单工单平均 < 100K
- [ ] 容器内存峰值 < 1.5G
- [ ] Webhook 响应时间 < 200ms（只入队，不阻塞）

### 8.3 稳定性验收

- [ ] 连续 72 小时运行无崩溃
- [ ] Docker 容器重启后状态恢复（`.state/tickets.json` 可读）
- [ ] 处理中工单 agent 崩溃重启后继续

---

## 9. 后续迭代（Out of Scope）

以下不在本次改造范围内，作为后续演进：

- **Cloudflare 日志查询**（用户确认暂不纳入）
- **企业版双轨支持**（本次只做 SaaS）
- **Web UI dashboard**（只用飞书交互）
- **多人协作**（单用户场景）
- **工单记录 Git 双向同步**（本次两边独立）
- **LLM 预判模型微调**（先用 gpt-5.4-mini 默认能力）
- **自动发 public reply**（违反 skill 约束，永不做）
- **DB 写操作**（永不做）

---

## 10. 术语表

- **SaaS** = Dify Cloud（dify.ai）用户
- **Enterprise** = Dify 企业版（私有化自部署）用户
- **Internal Note** = Zendesk 工单里 `is_public=false` 的内部备注，客户不可见
- **Support Engineer** = Dify 内部人员，实际发 public reply 的人
- **`@saas-bot`** = Zendesk agent 账号（本次改造替代原 `@support-agent`）
- **Teleport Headless Login** = Teleport 为 headless host 提供的认证方式，通过"登录+批准"两步完成
- **静默期** = agent 判定客户问题有效后，等待的缓冲时长（默认 3 min，连发时 5 min）
- **跟进模式** = 工单已有历史分析记录时，agent 只处理新增内容的模式
- **Skill** = 本文若未特指，指 `dify-support` skill（`/Users/yangyonggang/.claude/skills/dify-support`）
- **Headless Login** = Teleport 为无浏览器环境提供的认证模式（生成批准 URL，用户在任意设备点击后完成认证）

---

**文档结束**
