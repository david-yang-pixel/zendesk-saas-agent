---

## name: zendesk-agent-design
description: Zendesk 工单自动处理 Agent 系统设计文档
created_at: 2026-03-25

# Zendesk 工单自动处理 Agent — 系统设计

## 目录

- [概述](#概述)
- [需求决策汇总](#需求决策汇总)
- [整体架构](#整体架构)
- [入口层](#入口层)
- [会话管理层](#会话管理层)
- [Agent 运行层](#agent-运行层)
- [工具层](#工具层)
- [Sandbox 机制](#sandbox-机制)
- [Prompt 系统](#prompt-系统)
- [源码仓库池](#源码仓库池)
- [技术栈](#技术栈)
- [配置系统](#配置系统)
- [可观测性](#可观测性)
- [项目目录结构](#项目目录结构)

---

## 概述

构建一个全自动的 Zendesk 工单处理 Agent，自行监听新工单，按照 Dify 企业版技术支持 SOP 完成分析、源码定位、生成回复，并自动发送 Internal Reply（内部备注）。人只做最终抽检。

设计理念借鉴 [OpenClaw Agent 架构](../openclaw-analyze/agent-architecture.md)：

- **LLM 驱动的工具循环**（ToolLoop）作为核心执行引擎
- **SOP 编码在 Prompt 中**，LLM 作为决策主体按指令行事
- **Session 隔离**，每个工单拥有独立的会话上下文和 Sandbox
- **Skills / 工具注册机制**，模块化、可扩展

---

## 需求决策汇总


| 维度    | 决策                                                   |
| ----- | ---------------------------------------------------- |
| 自动化程度 | 全自动，Agent 自行监听 → 分析 → 回复 Internal Reply              |
| 运行平台  | 独立 Python 服务，借鉴 OpenClaw 设计思想                        |
| 分析深度  | 完整 SOP：知识库 → 环境调查 → 源码定位 → 修复验证 → 生成回复               |
| LLM   | OpenAI API（GPT-4o / GPT-4.1）                         |
| 部署    | 先本地开发调试，架构预留远程部署能力                                   |
| 并发策略  | 混合 Sandbox：轻量级上下文 + 按需升级隔离环境                         |
| 源码访问  | 共享仓库池（`git show` / `git grep` 只读）+ 按需 `git worktree` |
| 仓库更新  | 定时 fetch + 缺失版本按需补充 + 手动 fetch                       |


---

## 整体架构

系统由 4 个核心层组成：

```
┌──────────────────────────────────────────────────────────┐
│                      入口层 (Entry)                        │
│  Scheduler（自动轮询）/ CLI（手动触发）/ Webhook（未来）     │
│            ↓ 统一调用 SessionManager.dispatch()            │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│                   会话管理层 (Session)                      │
│  SessionManager: 并发控制、队列管理、Session 生命周期        │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│                Agent 运行层 (Runtime)                      │
│  AgentSession: ToolLoop（LLM ↔ Tools 循环）               │
│  System Prompt + Compaction + 护栏                        │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│                     工具层 (Tools)                         │
│  Zendesk / 知识库 / 源码 / MCP / 脚本 / 归档               │
└──────────────────────────────────────────────────────────┘
```

---

## 入口层

三种触发方式，殊途同归，统一调用 `SessionManager.dispatch()`：

### 1. Scheduler（自动轮询）

定时轮询 Zendesk API，发现新工单或新回复时自动派发处理。

- 轮询间隔可配置（默认 60 秒）
- 支持过滤条件（如 `status:open status:new`）
- 防重复：已处理/正在处理的工单不会重复派发

### 2. CLI（手动触发）

```bash
# 处理单个工单
zd-agent run <ticket-id>

# 带额外提示
zd-agent run <ticket-id> --hint "重点看部署问题"

# 批量处理
zd-agent batch 10001 10002 10003

# 只做分析不发回复（dry run）
zd-agent run <ticket-id> --dry-run

# 查看运行中的 session
zd-agent status

# 查看某工单的处理日志
zd-agent log <ticket-id>

# 查看处理统计
zd-agent stats --last 7d

# 启动轮询守护进程
zd-agent serve

# 仓库管理
zd-agent repos fetch                    # fetch 所有仓库
zd-agent repos fetch dify dify-helm     # fetch 指定仓库
zd-agent repos status                   # 查看仓库状态
zd-agent repos tags dify-enterprise     # 列出版本 tag
```

### 3. Webhook（未来扩展）

HTTP 端点，接收 Zendesk Trigger 推送，实时触发。预留接口，第一版不实现。

---

## 会话管理层

### AgentSession 状态机

```
                    dispatch()
                       │
                       ▼
                 ┌──────────┐
                 │  QUEUED   │  ← 并发已满时排队等待
                 └─────┬────┘
                       │ 有空位
                       ▼
                 ┌──────────┐
    创建 Sandbox │ RUNNING   │  ← ToolLoop 运行中
     加载 Prompt └──┬───┬───┘
                    │   │
           正常完成 │   │ 异常/超时/超限
                    ▼   ▼
              ┌──────────────┐
              │  COMPLETED / │
              │  FAILED /    │
              │  TIMEOUT     │
              └──────┬───────┘
                     │
                     ▼
               ┌───────────┐
               │  CLEANUP   │  ← 归档结果、清理 Sandbox
               └───────────┘
```

### Session 数据结构

```python
class AgentSession:
    ticket_id: int
    session_id: str                  # uuid
    status: SessionStatus            # QUEUED / RUNNING / COMPLETED / FAILED / TIMEOUT

    # Sandbox
    sandbox_dir: Path                # /tmp/zd-agent/<ticket-id>/
    context: TicketContext           # 工单元数据、客户信息、版本号

    # 运行时状态
    messages: list[Message]          # LLM 对话历史
    tool_calls: list[ToolCall]       # 工具调用记录
    round_count: int                 # 当前轮次
    token_usage: TokenUsage          # token 消耗统计

    # 产出物
    analysis: str                    # 分析结果
    reply_draft: str                 # 回复草稿
    reply_sent: bool                 # 是否已发送 Internal Reply
    archived: bool                   # 是否已归档
```

### 并发控制

```python
class SessionManager:
    max_concurrent: int              # 通过配置/环境变量设定
    active_sessions: dict[int, AgentSession]
    queue: asyncio.Queue[int]

    async def dispatch(self, ticket_id: int, hint: str = None):
        # 防重入：同一工单不重复处理
        if ticket_id in self.active_sessions:
            return
        if len(self.active_sessions) >= self.max_concurrent:
            await self.queue.put(ticket_id)
        else:
            await self._start_session(ticket_id, hint)
    # session 完成后自动从队列取下一个
```

---

## Agent 运行层

### ToolLoop 核心循环

借鉴 OpenClaw 的 `runEmbeddedPiAgent`，LLM 作为决策主体在工具循环中自主行动：

```
初始化 messages = [
    system_prompt（AGENTS.md + SOUL.md + TOOLS.md）,
    user_message（"新工单 #10001 需要处理" + 可选 hint）
]
    │
    ▼
┌─────────────── ToolLoop ───────────────────┐
│                                             │
│  while round < max_rounds:                  │
│      │                                      │
│      ├─► 调用 LLM（messages + tools 定义）   │
│      │                                      │
│      ├─► LLM 返回：                          │
│      │   ├─ 纯文本 → 记录到 analysis.md      │
│      │   ├─ tool_calls → 执行工具            │
│      │   └─ 无 tool_calls → 循环结束         │
│      │       （LLM 认为任务完成）             │
│      │                                      │
│      ├─► 执行工具，结果追加到 messages        │
│      │                                      │
│      ├─► 检查护栏：                          │
│      │   ├─ token 超预算？→ compaction       │
│      │   ├─ 超时？→ 强制结束                 │
│      │   └─ 轮次超限？→ 强制结束             │
│      │                                      │
│      └─► 下一轮                             │
│                                             │
└─────────────────────────────────────────────┘
```

### Compaction（上下文压缩）

当对话 token 数接近模型上下文窗口上限时（可配置阈值，默认 75%）：

1. 保留 system prompt（不压缩）
2. 保留最近 N 轮对话（保持连贯）
3. 将早期对话让 LLM 总结为摘要，替换原文
4. 继续运行

### 护栏机制


| 护栏       | 默认值          | 行为                                  |
| -------- | ------------ | ----------------------------------- |
| 最大轮次     | 30           | 达到上限后强制结束，标记需人工跟进                   |
| 超时       | 600 秒（10 分钟） | 超时后强制结束，保留 sandbox 便于调试             |
| Token 预算 | 200,000      | 超预算前触发 compaction，compaction 后仍超则结束 |


---

## 工具层

### 工具注册机制

借鉴 OpenClaw 的 `createOpenClawTools` 模式：

```python
class Tool(ABC):
    name: str
    description: str
    parameters: dict              # JSON Schema

    @abstractmethod
    async def execute(self, args: dict, session: AgentSession) -> str:
        ...

class ToolRegistry:
    tools: dict[str, Tool]

    def register(self, tool: Tool): ...

    def to_openai_tools(self) -> list[dict]:
        """转换为 OpenAI function calling 格式"""
        ...
```

每个工具都能访问当前 `AgentSession`，从而获取 sandbox 路径、工单上下文等。

### 完整工具集

#### Zendesk 工具组


| 工具名                       | 参数                | 说明               |
| ------------------------- | ----------------- | ---------------- |
| `zendesk_get_ticket`      | `ticket_id`       | 获取工单详情 + 完整对话记录  |
| `zendesk_get_attachments` | `ticket_id`       | 下载附件到 sandbox    |
| `zendesk_search`          | `query`           | 搜索历史工单           |
| `zendesk_internal_reply`  | `ticket_id, body` | 发送 Internal Note |


#### 知识库工具组


| 工具名                | 参数             | 说明               |
| ------------------ | -------------- | ---------------- |
| `knowledge_search` | `query, path?` | 在 ai-docs/ 中全文搜索 |
| `knowledge_read`   | `filepath`     | 读取知识库中的某个文件      |


#### 源码工具组


| 工具名          | 参数                            | 说明               |
| ------------ | ----------------------------- | ---------------- |
| `git_show`   | `repo, ref, filepath`         | 读取特定版本的文件内容      |
| `git_grep`   | `repo, ref, pattern, glob?`   | 在特定版本中搜索代码       |
| `git_log`    | `repo, ref, filepath?, n?`    | 查看提交历史           |
| `git_diff`   | `repo, ref1, ref2, filepath?` | 比较两个版本的差异        |
| `list_repos` | 无                             | 列出所有可用仓库及其分支/tag |


#### 环境工具组


| 工具名             | 参数                   | 说明                 |
| --------------- | -------------------- | ------------------ |
| `helm_watchdog` | `action, params`     | MCP 调用，查询客户部署版本    |
| `run_script`    | `script_name, args?` | 执行 scripts/ 下的排查脚本 |
| `sandbox_exec`  | `command, cwd?`      | 在 sandbox 内执行受限命令  |


#### 工作区工具组


| 工具名                       | 参数                  | 说明                         |
| ------------------------- | ------------------- | -------------------------- |
| `sandbox_create_worktree` | `repo, ref`         | 在 sandbox 中创建 git worktree |
| `sandbox_write`           | `filepath, content` | 向 sandbox 写文件              |
| `sandbox_read`            | `filepath`          | 读取 sandbox 中的文件            |


#### 归档工具组


| 工具名              | 参数                              | 说明                        |
| ---------------- | ------------------------------- | ------------------------- |
| `archive_ticket` | `ticket_id, analysis, solution` | 将处理结果归档到 ai-docs/tickets/ |


---

## Sandbox 机制

### 混合策略

- **轻量级模式（默认）**：每个工单创建独立临时目录，源码通过 `git show` / `git grep` 从共享仓库池只读访问
- **升级模式（按需）**：Agent 判断需要运行脚本或验证修复时，通过 `git worktree` 创建隔离工作区

### Sandbox 目录结构

```
/tmp/zd-agent/<ticket-id>/
├── context.json              ← 工单元数据、客户版本号
├── attachments/              ← 下载的附件（截图、日志）
├── analysis.md               ← 分析过程记录
├── reply-draft.md            ← 生成的回复
├── agent.log                 ← 本次处理日志
└── worktree/                 ← 仅按需创建
    └── dify/                 ← git worktree 指向共享仓库
```

### 清理策略


| Session 结果       | 行为                                 |
| ---------------- | ---------------------------------- |
| COMPLETED        | 归档产出物到 ai-docs/tickets/，删除 sandbox |
| FAILED / TIMEOUT | 保留 sandbox 内容便于调试，标记需人工跟进          |


### 抽象接口（预留 Docker 升级路径）

```python
class Sandbox(ABC):
    @abstractmethod
    async def create(self, ticket_id: int) -> Path: ...

    @abstractmethod
    async def exec(self, command: str, cwd: str = None) -> ExecResult: ...

    @abstractmethod
    async def cleanup(self) -> None: ...

class LocalSandbox(Sandbox):
    """临时目录实现（第一版）"""
    ...

class DockerSandbox(Sandbox):
    """Docker 容器实现（未来升级）"""
    ...
```

---

## Prompt 系统

借鉴 OpenClaw 的 workspace 文件体系，用三份 Prompt 文件控制 Agent 行为：

### SOUL.md — 人设与行为底线

```markdown
你是 Dify 企业版技术支持专家。

## 身份
- 你隶属于 Dify 技术支持团队，负责处理企业版客户工单
- 你精通 Dify 全栈架构：社区版核心、企业版后端（Go）、前端、
  插件系统、Helm/K8s 部署、AWS CDK、Terraform
- 你对客户保持专业、有同理心的态度

## 行为准则
- 永远不要猜测答案，找不到依据就诚实说明需要人工介入
- 永远不要在 Internal Reply 中暴露内部源码路径或仓库地址
- 分析过程要有证据链：工单描述 → 定位到的代码/配置 → 结论
- 回复邮件语气专业且有同理心
```

### AGENTS.md — SOP 工作流指令

```markdown
## 工作流

收到工单后，按以下步骤处理。每一步都必须执行，不要跳过。

### Step 1 — 信息收集
1. 调用 zendesk_get_ticket 获取工单详情和完整对话记录
2. 调用 zendesk_get_attachments 下载所有附件（截图、日志等）
3. 从工单内容中提取关键信息：
   - 客户描述的问题现象
   - 错误信息 / 日志片段
   - 客户的部署方式（Helm / Docker Compose / AWS CDK 等）

### Step 2 — 版本确认
1. 调用 helm_watchdog 查询客户的部署版本
2. 如果 helm_watchdog 查不到，从工单对话中寻找版本线索
3. 记录确认的版本号，后续源码查找必须基于此版本

### Step 3 — 知识库检索
1. 调用 knowledge_search 搜索 ai-docs/ 中的已知解决方案
2. 调用 zendesk_search 搜索历史相似工单
3. 如果找到匹配方案，验证方案适用于客户的版本后，跳到 Step 6

### Step 4 — 源码定位
根据问题类型选择对应仓库：
- 社区版通用逻辑 / API / 工作流 → dify
- 代码执行 / Sandbox → dify-sandbox
- SSO / License / 企业管理 → dify-enterprise
- 前端控制台 → dify-enterprise-frontend
- Helm / K8s 部署 → dify-helm
- 插件相关 → dify-plugin-daemon / dify-official-plugins

使用 git_grep 和 git_show（指定客户版本的 ref）搜索和阅读相关代码。
逐步缩小范围，直到定位到问题根因或相关逻辑。

### Step 5 — 环境调查（按需）
如果仅靠源码无法确认问题，可以：
1. 调用 run_script 执行相关排查脚本
2. 如果需要完整运行环境，调用 sandbox_create_worktree 创建隔离工作区

### Step 6 — 生成回复
基于分析结果，撰写 Internal Reply，必须包含：
1. **问题确认**：用自己的话复述客户问题，表明理解
2. **分析结论**：问题根因是什么，附上证据（代码片段/配置/日志对比）
3. **解决方案**：具体的操作步骤，清晰无歧义
4. **后续步骤**：如果需要客户配合或有后续跟进

语言规则：
- 自动识别工单对话的语言，使用相同语言回复
- 如果工单中混合多种语言，以客户最近一条消息的语言为准

### Step 7 — 投递与归档
1. 调用 zendesk_internal_reply 发送 Internal Note
2. 调用 archive_ticket 将本次分析过程和解决方案归档到知识库

## 决策规则
- 如果分析后确信度低于 70%，在 Internal Reply 中明确标注
  "⚠️ 置信度不高，建议人工复核"
- 如果工单问题超出能力范围（如需要访问客户环境），标注
  "🔄 需要人工介入：<原因>"
- 如果遇到工具调用失败，尝试一次重试，仍失败则记录错误并继续
```

### TOOLS.md — 工具使用规范

```markdown
## 工具使用注意事项

### 源码工具
- git_show 和 git_grep 必须指定 ref 参数（tag 或 branch），不要用默认分支
- 搜索代码时先用 git_grep 定位文件，再用 git_show 读取完整内容
- 不确定仓库时，先用 list_repos 查看可用仓库列表

### Zendesk 工具
- zendesk_internal_reply 发送的是内部备注，客户看不到
- 回复内容不要包含内部仓库路径、源码引用等敏感信息

### 安全边界
- sandbox_exec 只允许执行 scripts/ 目录下的已知脚本
- 禁止执行任何修改共享仓库的命令
- 禁止执行网络请求类命令（curl / wget 等）
```

---

## 源码仓库池

### 设计原则

本地维护一份完整的共享仓库池，Agent 通过 `git show` / `git grep` 按版本只读访问，无需 checkout 即可并发服务多个工单。

### 仓库列表


| 仓库                         | 覆盖范围                       |
| -------------------------- | -------------------------- |
| `dify`                     | 社区版核心（API、工作流、模型管理）        |
| `dify-enterprise`          | 企业版后端（Go，SSO、License、管理后台） |
| `dify-sandbox`             | 代码执行沙箱                     |
| `dify-enterprise-frontend` | 企业版前端控制台                   |
| `dify-helm`                | Helm Chart 部署              |
| `dify-plugin-daemon`       | 插件守护进程                     |
| `dify-official-plugins`    | 官方插件源码                     |


### 更新策略


| 方式          | 说明                                           |
| ----------- | -------------------------------------------- |
| 启动时自动 fetch | Agent 服务启动时拉取所有仓库最新状态                        |
| 定时 fetch    | 可配置 cron 表达式（默认每天凌晨 3 点）                     |
| 按需 fetch    | Agent 发现本地缺少客户对应的版本时自动 fetch                 |
| 手动 fetch    | 通过 CLI `zd-agent repos fetch [repo...]` 手动触发 |


### 多版本访问方式

```bash
# 零成本读取任意版本的任何文件（不改工作区）
git show v1.3.0:api/controllers/workflow.py

# 零成本在特定版本中搜索代码
git grep "PluginDaemon" v1.3.0 -- "*.py"

# 仅修复验证时才创建 worktree（有成本）
git worktree add /tmp/zd-agent/<ticket>/worktree/dify v1.3.0
```

---

## 技术栈

### 核心依赖


| 依赖                  | 版本策略   | 用途                                           |
| ------------------- | ------ | -------------------------------------------- |
| `openai`            | latest | LLM API 调用（function calling + ToolLoop 自行实现） |
| `httpx`             | latest | Zendesk REST API、HTTP 请求（异步）                 |
| `typer`             | latest | CLI 框架（手动触发 + 管理命令）                          |
| `pydantic`          | v2     | 数据模型、工具参数校验                                  |
| `pydantic-settings` | latest | 配置管理（YAML + 环境变量覆盖）                          |
| `structlog`         | latest | 结构化日志                                        |
| `apscheduler`       | v4     | 定时轮询 / cron 任务（异步调度器）                        |
| `mcp`               | latest | MCP 客户端（helm-watchdog 版本查询）                  |
| `pyyaml`            | latest | 解析 config.yaml                               |
| `rich`              | latest | CLI 输出美化（表格、进度条）                             |


### 选型理由

**LLM 调用 + ToolLoop：直接基于 `openai` SDK 实现**

不使用 LangChain / LangGraph / OpenAI Agents SDK 等框架，原因：

1. ToolLoop 核心逻辑不复杂（while 循环 + tool_calls 分发），约 200-300 行代码
2. 周围有大量定制需求（Compaction、Session 状态、Sandbox 生命周期、结构化日志），框架反而碍事
3. 依赖少 = 维护成本低，不用跟着框架升级跑
4. 完全掌控每一步，调试直观

**Zendesk API：基于 `httpx` 自行封装**

Zendesk REST API 简单直接，且可复用现有 `zendesk-cli` 的逻辑。不引入第三方 Zendesk SDK，减少依赖。

**Git 操作：基于 `asyncio.subprocess` 封装**

`git show` / `git grep` / `git worktree` 就是命令行调用，直接用子进程执行即可，无需引入 GitPython 等重量级库。

---

## 配置系统

### config.yaml

```yaml
# Agent 核心配置
agent:
  max_concurrent_sessions: 5          # 环境变量 ZD_MAX_CONCURRENT 覆盖
  guardrails:
    max_rounds: 30
    timeout_seconds: 600
    max_tokens_per_session: 200000
    compaction_threshold: 0.75

# LLM 配置
llm:
  provider: openai
  model: gpt-4o                        # 环境变量 ZD_MODEL 覆盖
  temperature: 0.2
  api_key: ${OPENAI_API_KEY}

# Zendesk 配置
zendesk:
  subdomain: ${ZENDESK_SUBDOMAIN}
  email: ${ZENDESK_EMAIL}
  api_token: ${ZENDESK_API_TOKEN}
  poll_interval_seconds: 60
  poll_query: "status:open status:new"

# 源码仓库池
repos:
  base_dir: ./repos
  auto_fetch: true
  fetch_cron: "0 3 * * *"
  repositories:
    - name: dify
      url: https://github.com/langgenius/dify.git
    - name: dify-enterprise
      url: https://github.com/langgenius/dify-enterprise.git
    - name: dify-sandbox
      url: https://github.com/langgenius/dify-sandbox.git
    - name: dify-enterprise-frontend
      url: git@github.com:langgenius/dify-enterprise-frontend.git
    - name: dify-helm
      url: https://github.com/langgenius/dify-helm.git
    - name: dify-plugin-daemon
      url: https://github.com/langgenius/dify-plugin-daemon.git
    - name: dify-official-plugins
      url: https://github.com/langgenius/dify-official-plugins.git

# Sandbox 配置
sandbox:
  type: local                          # local | docker
  base_dir: /tmp/zd-agent
  cleanup_on_success: true
  cleanup_on_failure: false
  docker:                              # Docker 模式额外配置（未来）
    image: zd-agent-sandbox:latest
    memory_limit: 2g
    network: host

# MCP 配置
mcp:
  helm_watchdog:
    url: https://helm-watchdog.dify.ai/api/v1/mcp
    timeout: 60

# 知识库
knowledge:
  docs_dir: ./ai-docs
  archive_dir: ./ai-docs/tickets
```

### 环境变量覆盖


| 环境变量                | 覆盖的配置项                          | 说明                |
| ------------------- | ------------------------------- | ----------------- |
| `ZD_MAX_CONCURRENT` | `agent.max_concurrent_sessions` | 最大并发 session 数    |
| `ZD_MODEL`          | `llm.model`                     | LLM 模型名称          |
| `OPENAI_API_KEY`    | `llm.api_key`                   | OpenAI API Key    |
| `ZENDESK_SUBDOMAIN` | `zendesk.subdomain`             | Zendesk 子域名       |
| `ZENDESK_EMAIL`     | `zendesk.email`                 | Zendesk 账号邮箱      |
| `ZENDESK_API_TOKEN` | `zendesk.api_token`             | Zendesk API Token |


---

## 可观测性

### 结构化日志

```
控制台（实时）
├─ [INFO]  ticket=10001 session=abc123 status=RUNNING
├─ [INFO]  ticket=10001 tool=git_grep repo=dify ref=v1.3.0 pattern="workflow"
├─ [WARN]  ticket=10001 round=25/30 approaching limit
└─ [INFO]  ticket=10001 status=COMPLETED rounds=18 tokens=45230 duration=4m32s
```

### Session 日志文件

每个 sandbox 内保留完整日志：`/tmp/zd-agent/<ticket-id>/agent.log`

### 处理报告（归档时生成）

```
ai-docs/tickets/<ticket-id>/
├── analysis.md               # 分析过程
├── reply.md                  # 发送的回复内容
└── meta.json                 # 元数据（耗时、token、轮次、工具调用列表）
```

### CLI 监控命令

```bash
# 查看所有活跃 session
zd-agent status
# TICKET   STATUS    ROUND   TOKENS   DURATION
# 10001    RUNNING   12/30   32450    3m21s
# 10002    QUEUED    -       -        -

# 实时日志
zd-agent log <ticket-id> --follow

# 历史统计
zd-agent stats --last 7d
# 过去 7 天处理工单: 42
# 平均处理时间: 5m12s
# 平均 token 消耗: 58,000
# 成功率: 85% (36/42)
# 需人工介入: 15% (6/42)
```

---

## 项目目录结构

```
zendesk-ai-auto/
├── pyproject.toml                      # uv 项目配置
├── .env                                # 凭据（Zendesk / OpenAI）
├── config.yaml                         # Agent 配置
│
├── src/
│   ├── __init__.py
│   │
│   ├── entry/                          # 入口层
│   │   ├── scheduler.py                # 轮询 Zendesk 新工单
│   │   └── cli.py                      # CLI 入口（手动触发 + 管理命令）
│   │
│   ├── session/                        # 会话管理层
│   │   ├── manager.py                  # SessionManager（并发控制、队列）
│   │   ├── agent_session.py            # AgentSession（单工单生命周期）
│   │   └── sandbox.py                  # Sandbox 抽象 + LocalSandbox 实现
│   │
│   ├── runtime/                        # Agent 运行层
│   │   ├── tool_loop.py                # 核心工具循环（LLM ↔ Tools）
│   │   ├── llm_client.py              # OpenAI API 封装
│   │   ├── compaction.py               # 上下文压缩
│   │   └── guardrails.py              # 护栏（轮次/超时/token）
│   │
│   ├── tools/                          # 工具层
│   │   ├── base.py                     # Tool 基类 + ToolRegistry
│   │   ├── zendesk.py                  # Zendesk 系列工具
│   │   ├── knowledge.py                # 知识库检索
│   │   ├── git_source.py               # 源码搜索工具
│   │   ├── script_runner.py            # 排查脚本执行
│   │   ├── mcp_client.py              # MCP 工具（helm-watchdog）
│   │   ├── sandbox_tools.py            # Sandbox 工具（worktree/read/write/exec）
│   │   └── archive.py                  # 归档工具
│   │
│   └── prompts/                        # Prompt 文件
│       ├── AGENTS.md                   # SOP 工作流指令
│       ├── SOUL.md                     # 人设与行为准则
│       └── TOOLS.md                    # 工具使用规范
│
├── repos/                              # 共享仓库池
│   ├── dify/
│   ├── dify-enterprise/
│   ├── dify-sandbox/
│   ├── dify-enterprise-frontend/
│   ├── dify-helm/
│   ├── dify-plugin-daemon/
│   └── dify-official-plugins/
│
├── ai-docs/                            # 知识库 + 归档
│   ├── openclaw-analyze/               # OpenClaw 架构分析
│   ├── solve-ticket-sop/               # 工单处理 SOP
│   ├── design/                         # 设计文档
│   └── tickets/                        # 工单处理记录归档
│
└── scripts/                            # 排查脚本
    └── check-plugin-runtime.sh
```

