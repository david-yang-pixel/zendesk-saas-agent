# zd-agent

`zd-agent` 是一个面向 Dify 技术支持场景的 Zendesk 工单自动处理 Agent。它以 LLM 驱动的工具循环为核心，围绕"读取工单 → 版本确认 → 多源检索 → 源码分析 → 修复状态检查 → 生成 Internal Note → 归档结果"这一条标准流程运行，目标是把人工 SOP 尽可能自动化，同时保留 dry-run、归档和错误追踪能力，方便人工抽检与回溯。

## 项目定位

这个项目不是一个简单的"自动回复脚本"，而是一个可持续运行的支持工程 Agent。它具备以下特点：

- 支持 `CLI` 手动触发、`Scheduler` 自动轮询、`Webhook` 事件驱动三种入口
- 基于 `AgentLoop`（AsyncGenerator 事件驱动）驱动 36 个工具
- 支持多 LLM 厂商（OpenAI / Anthropic），通过 `LLMProvider` 抽象层统一接口
- 支持 Subagent 委派，复杂子任务可独立运行隔离上下文
- 支持多工单并发处理、会话隔离、Sandbox 保留与清理
- 面向 Dify 企业版问题，支持 Helm chart 版本与 App 版本映射
- 支持 worktree 级源码分析，而不是只做浅层 grep
- 支持将分析结果、回复草稿、错误信息归档到 `ai-docs/tickets/`
- 自动检测客户语言并以对应语言回复

## 整体架构

系统按 5 层组织：

```text
┌──────────────────────────────────────────────────────────────────┐
│                        入口层 Entry                              │
│  CLI (typer) / Scheduler (APScheduler) / Webhook (aiohttp)      │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                    会话管理层 Session                             │
│  SessionManager / AgentSession / Sandbox 生命周期                │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                     运行层 Runtime                               │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────┐ │
│  │ LLM 抽象    │  │ Agent Loop  │  │ Prompt 系统              │ │
│  │ LLMProvider │  │ AsyncGen    │  │ PromptBuilder            │ │
│  │ OpenAI      │  │ → 事件流    │  │ PromptSection (条件加载) │ │
│  │ Anthropic   │  │ → Subagent  │  │ 动态上下文注入           │ │
│  └─────────────┘  └─────────────┘  └──────────────────────────┘ │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────┐ │
│  │ MessageStore│  │ Guardrails  │  │ Compaction               │ │
│  │ 会话管理    │  │ 轮次/Token  │  │ 锚点保护 + History Snip  │ │
│  │ 厂商无关    │  │ /超时护栏   │  │ + LLM 摘要多级压缩      │ │
│  └─────────────┘  └─────────────┘  └──────────────────────────┘ │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────┐ │
│  │ ToolContext  │  │ Orchestrator│  │ Result Budget            │ │
│  │ 工具上下文  │  │ 并发/串行   │  │ 工具输出 Token 截断      │ │
│  └─────────────┘  └─────────────┘  └──────────────────────────┘ │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                       工具层 Tools (36 个)                       │
│                                                                  │
│  Zendesk: get_ticket / get_attachments / search / internal_reply │
│           search_articles / get_article / get_user / add_tags    │
│  知识库:  knowledge_search / knowledge_read                      │
│  文档:    official_docs_search / official_docs_read              │
│  源码:    git_show / git_grep / git_log / git_diff               │
│           list_repos / list_repo_tags                            │
│  GitHub:  github_search_prs / github_get_pr                     │
│  Helm:    helm_list_versions / helm_get_latest / helm_get_details│
│           helm_list_images / helm_validate / helm_get_values     │
│  Sandbox: create_worktree / list / search / read / write         │
│  环境:    run_script / sandbox_exec                              │
│  分类:    classify_ticket                                        │
│  归档:    archive_ticket                                         │
│  子代理:  run_subagent                                           │
└──────────────────────────────────────────────────────────────────┘
```

## 目录结构

```text
src/zd_agent/
  entry/              CLI、Scheduler、Webhook 入口
  session/            AgentSession、SessionManager、Sandbox
  runtime/
    llm/              LLM 抽象层
      types.py          LLMProvider ABC、LLMResponse、ToolCallRequest、ToolSchema
      openai_provider.py   OpenAI Chat Completions 实现
      anthropic_provider.py  Anthropic Messages API 实现
      factory.py        根据配置创建 Provider 实例
    prompt/           Prompt 系统
      section.py        PromptSection、PromptContext 定义
      builder.py        PromptBuilder（条件加载 + 排序组装）
      defaults.py       默认段注册（SOUL / AGENTS / TOOLS / 动态段）
    agent_loop.py     核心循环（AsyncGenerator 事件驱动）
    tool_loop.py      兼容包装器（消费事件 → Rich 控制台输出）
    events.py         事件类型（LoopProgress / ToolExecuted / LoopFinished）
    messages.py       MessageStore（厂商无关的会话管理）
    subagent.py       Subagent 执行器（独立会话 + 隔离上下文）
    tool_orchestrator.py  工具编排（并发安全分组执行）
    tool_context.py   ToolUseContext（工具执行上下文）
    result_budget.py  工具输出 Token 截断
    compaction.py     上下文压缩（锚点保护 + history snip + LLM 摘要）
    guardrails.py     护栏（轮次/Token/超时限制）
    runner.py         Agent 运行器（组装组件 + 执行完整流程）
    llm_client.py     LLMClient 兼容 facade
    prompt_loader.py  Prompt 加载 + PromptBuilder 委托
    retry.py          全局重试策略
    source_selector.py  知识源推荐
    archive_meta.py   归档元数据构建
  tools/              36 个工具定义
    base.py             Tool ABC + ToolRegistry + 工具元数据
    zendesk.py          Zendesk 工单/搜索/回复/标签工具
    knowledge.py        本地知识库搜索与读取
    official_docs.py    官方在线文档搜索与读取
    git_source.py       Git 源码工具（grep/show/log/diff/tags）
    github_pr.py        GitHub PR 搜索与详情
    helm_watchdog.py    Helm Watchdog REST API
    sandbox_tools.py    Sandbox 文件操作与 worktree
    script_runner.py    脚本执行与 sandbox_exec
    classify.py         工单分类
    archive.py          归档工具
    subagent.py         Subagent 委派工具
    registry.py         工具注册入口
  prompts/            Prompt 文件
    SOUL.md             身份与行为准则
    AGENTS.md           工作流与决策规则
    TOOLS.md            工具使用指南

ai-docs/
  design/             设计文档与蓝图
  docs/               项目文档
  tickets/            工单归档结果

config.yaml           主配置文件
.env.example          环境变量示例
```

## 处理流程

一个工单的处理路径如下：

1. 入口层接收工单事件或手动命令，统一派发到 `SessionManager`
2. 创建 `AgentSession` 与独立 sandbox，加载系统 Prompt（PromptBuilder 动态组装）
3. `AgentLoop` 以 AsyncGenerator 事件流驱动 LLM ↔ 工具循环：
   - Round 1：`zendesk_get_ticket` 获取工单详情、检测客户语言、收集截图
   - Round 2：`zendesk_get_attachments` 下载附件到 sandbox
   - Round 3-4：版本确认（Helm Watchdog + 仓库 tag 查询）
   - Round 5：`classify_ticket` 工单分类，获取后续策略指导
   - Round 6+：多源知识检索与交叉验证（Help Center / 官方文档 / 知识库 / 历史工单 / 源码）
   - 源码分析：`sandbox_create_worktree` → `sandbox_search` → `sandbox_read` 做深度代码追踪
   - 若为 `code-bug`：检查更高版本差异与 GitHub PR 修复状态
   - 复杂子任务：通过 `run_subagent` 委派到隔离上下文
   - 生成三段式 Internal Note（内部分析 / 客户回复建议 / 人工复核指引）
   - `archive_ticket` 归档到 `ai-docs/tickets/<ticket_id>/`
4. `ToolLoop` 消费事件流，输出 Rich 格式控制台日志
5. 清理 sandbox，输出处理摘要

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置凭据

```bash
cp .env.example .env
```

至少需要填写：

```env
OPENAI_API_KEY=sk-xxx
ZENDESK_SUBDOMAIN=your-subdomain
ZENDESK_EMAIL=you@example.com
ZENDESK_API_TOKEN=your-api-token
```

可选配置：

| 环境变量 | 说明 |
|---------|------|
| `OPENAI_BASE_URL` | 自定义 OpenAI API 地址（代理 / Azure） |
| `ANTHROPIC_API_KEY` | Anthropic API Key（provider 设为 anthropic 时需要） |
| `ZD_LLM_PROVIDER` | 覆盖 LLM 厂商（`openai` / `anthropic`） |
| `ZD_MODEL` | 覆盖模型名称 |
| `ZD_MAX_CONCURRENT` | 覆盖并发数 |
| `GITHUB_TOKEN` | 服务器环境使用 HTTPS + PAT 拉取仓库 |
| `ZD_WEBHOOK_SIGNING_SECRET` | Zendesk Webhook 签名校验 |
| `ZD_LOG_LEVEL` | 日志级别（DEBUG / INFO / WARNING / ERROR） |

### 3. 查看帮助

```bash
uv run zd-agent --help
```

## 常用命令

```bash
# 处理单个工单
uv run zd-agent run <ticket-id>

# 只分析不发送回复
uv run zd-agent run <ticket-id> --dry-run

# 带额外提示处理
uv run zd-agent run <ticket-id> --hint "重点看 Helm 部署链路"

# 批量处理
uv run zd-agent batch 10001 10002 10003 --dry-run

# 启动自动轮询
uv run zd-agent serve

# 启动 Webhook 服务
uv run zd-agent webhook

# 查看会话状态
uv run zd-agent status

# 查看统计
uv run zd-agent stats

# 查看已注册工具
uv run zd-agent tool list

# 查看工具 schema
uv run zd-agent tool schema run_subagent

# 仓库状态与版本
uv run zd-agent repos status
uv run zd-agent repos tags dify-enterprise
```

## 多 LLM 厂商支持

通过 `LLMProvider` 抽象层支持多厂商切换，当前支持 OpenAI 和 Anthropic。

### 切换到 Anthropic

方式一：修改 `config.yaml`：

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-20250514
  temperature: 0.2
  timeout: 120
  max_tokens: 8192
```

方式二：通过环境变量覆盖（不修改配置文件）：

```env
ZD_LLM_PROVIDER=anthropic
ZD_MODEL=claude-sonnet-4-20250514
ANTHROPIC_API_KEY=sk-ant-xxx
```

`max_tokens` 配置说明：
- Anthropic API 必传（默认 8192）
- OpenAI 设为 0 表示不限制（使用 `max_completion_tokens` 参数）

## Webhook

Webhook 入口为 `POST /webhook/zendesk`，默认监听 `0.0.0.0:8080`。

如果需要开启 Zendesk 官方签名校验，可在 `.env` 中配置：

```env
ZD_WEBHOOK_SIGNING_SECRET=RyqsCLbrnJtjQofdJVbPjy9M8bbHbjl95747HzOI5hY=
```

签名校验会读取以下请求头，并按 Zendesk 官方规则校验：

- `X-Zendesk-Webhook-Signature`
- `X-Zendesk-Webhook-Signature-Timestamp`

服务端会用 `timestamp + raw_body` 和 secret 计算 `SHA256 HMAC` 后再做 `base64` 比对，并固定要求时间戳在 `300` 秒窗口内。

当前 webhook 仅接收 Zendesk Event Webhook 格式，并且只处理满足以下条件的事件：

- `type` 必须等于 `zen:event-type:ticket.created`
- `detail.tags` 中必须存在一个完整等于 `enterprise` 的标签
- 工单号从 `detail.id` 提取

如果请求不满足处理条件，服务会返回 `200`，并在响应中标记 `ignored: true`。

### 后台运行脚本

如果是在服务器上运行，可直接使用 `scripts/webhook.sh` 管理 webhook 进程。脚本会检查 `uv` 是否安装、检查 `.venv` 是否存在；如果不存在，会自动执行 `uv sync`。启动时使用 `nohup` 后台运行，并将日志写入 `logs/webhook.log`，PID 写入 `logs/webhook.pid`。

```bash
# 环境检查
bash scripts/webhook.sh check

# 后台启动
bash scripts/webhook.sh start

# 指定监听地址与端口启动
bash scripts/webhook.sh start --host 0.0.0.0 --port 8080

# 查看状态
bash scripts/webhook.sh status

# 查看日志
bash scripts/webhook.sh logs

# 重启
bash scripts/webhook.sh restart

# 停止
bash scripts/webhook.sh stop
```

## 归档与调试

每次工单处理后，系统会在 `ai-docs/tickets/<ticket_id>/` 下保存归档结果。常见文件包括：

- `analysis.md`：分析过程或失败说明
- `reply.md`：完整 Internal Note，包含内部分析、客户回复建议、人工复核指引
- `meta.json`：状态、轮次、token、工具调用、失败工具、错误信息、修复状态等元数据

如果开启 `dry-run`，通常适合先观察这里的归档内容，再决定是否正式投递 Internal Reply。

## 相关文档

- `ai-docs/design/2026-04-01-architecture-evolution-blueprint.md`：架构演进蓝图
- `ai-docs/design/zendesk-agent-design.md`：系统设计
- `ai-docs/design/implementation-plan.md`：实施计划与当前进度
- `ai-docs/design/webhook-zendesk-event-plan.md`：Webhook 设计补充
