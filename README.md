# zd-agent

`zd-agent` 是一个面向 Dify 技术支持场景的 Zendesk 工单自动处理 Agent。它以 LLM 驱动的工具循环为核心，围绕“读取工单 -> 版本确认 -> 多源检索 -> 源码分析 -> 修复状态检查 -> 生成 Internal Note -> 归档结果”这一条标准流程运行，目标是把人工 SOP 尽可能自动化，同时保留 dry-run、归档和错误追踪能力，方便人工抽检与回溯。

## 项目定位

这个项目不是一个简单的“自动回复脚本”，而是一个可持续运行的支持工程 Agent。它具备以下特点：

- 支持 `CLI` 手动触发、`Scheduler` 自动轮询、`Webhook` 事件驱动三种入口
- 基于 `ToolLoop` 驱动 Zendesk、知识库、官方文档、源码仓库、Helm Watchdog 等工具
- 支持多工单并发处理、会话隔离、Sandbox 保留与清理
- 面向 Dify 企业版问题，支持 Helm chart 版本与 App 版本映射
- 支持 worktree 级源码分析，而不是只做浅层 grep
- 支持将分析结果、回复草稿、错误信息归档到 `ai-docs/tickets/`

## 核心能力

- 自动读取 Zendesk 工单正文、对话记录、附件和自定义字段
- 优先使用工单字段中的 `Product`、`Version`、`Environment` 等结构化信息
- 融合 Zendesk Help Center、官方在线文档、本地知识库、历史工单、源码等多种知识源
- 使用 `sandbox_create_worktree`、`sandbox_search`、`sandbox_read` 做连续代码追踪
- 调用 Helm Watchdog 做版本映射、values 查询和部署侧辅助判断
- 对 `code-bug` 工单额外检查更高版本差异与 GitHub PR 修复状态
- 生成三段式 Internal Note：内部分析、客户回复建议、人工复核指引
- 在失败场景下记录 `meta.json`、`analysis.md`、失败工具和错误信息

## 整体架构

系统按 4 层组织：

```text
┌──────────────────────────────────────────────────────────┐
│                     入口层 Entry                         │
│  CLI / Scheduler / Webhook                              │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│                 会话管理层 Session                       │
│  SessionManager / AgentSession / Sandbox 生命周期        │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│                  运行层 Runtime                          │
│  ToolLoop / Prompt / Guardrails / Compaction / Retry     │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│                    工具层 Tools                          │
│  Zendesk / Knowledge / Official Docs / Git / Sandbox     │
│  Helm Watchdog / Script Runner / Archive                 │
└──────────────────────────────────────────────────────────┘
```

架构设计文档见 `ai-docs/design/zendesk-agent-design.md`，实施进度见 `ai-docs/design/implementation-plan.md`。

## 处理流程

一个工单的大致处理路径如下：

1. 入口层接收工单事件或手动命令，统一派发到 `SessionManager`
2. 创建 `AgentSession` 与独立 sandbox，加载系统 Prompt
3. LLM 通过 `ToolLoop` 调用工具，先收集工单信息和版本线索
4. 根据问题类型动态组合知识源：
   - Zendesk Help Center
   - 官方在线文档
   - 本地知识库
   - 历史工单
   - Helm Watchdog
   - 源码仓库 / worktree
5. 若为 `code-bug`，补充检查更新版本差异与 GitHub PR 状态
6. 生成三段式 Internal Note 草稿，dry-run 时仅保留草稿不发送
7. 将分析结果、回复、元数据、错误信息归档到 `ai-docs/tickets/<ticket_id>/`

## 目录结构

```text
src/zd_agent/
  entry/        CLI、Scheduler、Webhook 入口
  session/      AgentSession、SessionManager、Sandbox
  runtime/      ToolLoop、Runner、LLM、Guardrails、Compaction、Retry
  tools/        Zendesk、知识库、官方文档、Git、Sandbox、归档等工具
  prompts/      SOUL.md、AGENTS.md、TOOLS.md

ai-docs/design/   设计文档与实施计划
ai-docs/tickets/  工单归档结果
config.yaml       主配置文件
.env.example      环境变量示例
```

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

- `OPENAI_BASE_URL`：自定义 OpenAI API 地址
- `GITHUB_TOKEN`：服务器环境使用 HTTPS + PAT 拉取仓库时启用
- `ZD_MAX_CONCURRENT`：覆盖并发数
- `ZD_MODEL`：覆盖模型
- `ZD_WEBHOOK_AUTH_HEADER` / `ZD_WEBHOOK_AUTH_VALUE`：Webhook 鉴权

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

# 仓库状态与版本
uv run zd-agent repos status
uv run zd-agent repos tags dify-enterprise
```

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

- `ai-docs/design/zendesk-agent-design.md`：系统设计
- `ai-docs/design/implementation-plan.md`：实施计划与当前进度
- `ai-docs/design/webhook-zendesk-event-plan.md`：Webhook 设计补充
