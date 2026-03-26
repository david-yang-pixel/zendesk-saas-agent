---
name: implementation-plan
description: Zendesk 工单自动处理 Agent 实施计划
created_at: 2026-03-25
---

# 实施计划

基于 [系统设计文档](./zendesk-agent-design.md)，按从底层到上层、每阶段可独立验证的原则分 6 个阶段实施。

---

## Phase 0 — 项目脚手架 ✅

**目标：** 搭建项目基础结构，所有依赖就位，能跑通最简单的 CLI 命令。

### 任务

- [x] 初始化 uv 项目，创建 `pyproject.toml`，添加所有依赖
- [x] 创建 `src/` 目录结构（entry / session / runtime / tools / prompts）
- [x] 创建 `.env.example`，列出所有需要的环境变量
- [x] 创建 `config.yaml` 默认配置文件
- [x] 实现配置加载模块（`pydantic-settings`，支持 YAML + 环境变量覆盖）
- [x] 实现 CLI 骨架（`typer`），注册所有子命令的空壳
- [x] 配置 `structlog` 结构化日志

### 验证

```bash
uv sync
uv run zd-agent --help          # 能看到所有子命令
uv run zd-agent version         # 输出版本号
```

---

## Phase 1 — 工具层（不涉及 LLM） ✅

**目标：** 实现所有工具，每个工具可独立测试，不依赖 ToolLoop。

### 1a. 工具基础设施

- [x] 实现 `Tool` 基类和 `ToolRegistry`
- [x] 实现 `to_openai_tools()` 转换方法（生成 function calling JSON Schema）

### 1b. Zendesk 工具

- [x] 复用 `zendesk-cli`（git 依赖），通过 `asyncio.to_thread` 桥接
- [x] `zendesk_get_ticket` — 获取工单详情 + 对话记录 + 图片收集
- [x] `zendesk_get_attachments` — 下载附件到 sandbox
- [x] `zendesk_search` — 搜索历史工单
- [x] `zendesk_internal_reply` — 发送 Internal Note
- [x] `zendesk_search_articles` / `zendesk_get_article` / `zendesk_get_user`（额外）

### 1c. 知识库工具

- [x] `knowledge_search` — 在 ai-docs/ 中全文搜索（rg，含历史工单归档）
- [x] `knowledge_read` — 读取知识库文件

### 1d. 源码工具

- [x] 实现 Git 命令封装（`asyncio.subprocess`）+ bare clone 仓库管理（`repos.py`）
- [x] `git_show` — 读取特定版本文件（含按需 auto-fetch）
- [x] `git_grep` — 版本内搜索代码（含按需 auto-fetch）
- [x] `git_log` — 查看提交历史
- [x] `git_diff` — 比较版本差异
- [x] `list_repos` — 列出所有可用仓库及状态

### 1e. 环境工具

- [x] `helm_watchdog` — Helm Watchdog REST API 封装（6 个工具）
- [x] `run_script` — 执行 scripts/ 下的排查脚本（白名单限制）
- [x] `sandbox_exec` — sandbox 内受限命令执行（白名单 + 黑名单双重安全）

### 1f. Sandbox 工具

- [x] 实现 `Sandbox` 抽象基类
- [x] 实现 `LocalSandbox`（临时目录创建/清理）
- [x] `sandbox_create_worktree` — 按需创建 git worktree
- [x] `sandbox_write` / `sandbox_read`（路径穿越防护）

### 1g. 归档工具

- [x] `archive_ticket` — 归档分析结果到 ai-docs/tickets/

### 验证

```bash
# 用 CLI 手动测试每个工具
uv run zd-agent tool test zendesk_get_ticket --args '{"ticket_id": 10001}'
uv run zd-agent tool test git_grep --args '{"repo": "dify", "ref": "main", "pattern": "workflow"}'
uv run zd-agent tool list    # 列出所有注册的工具
```

---

## Phase 2 — Agent 运行层（核心循环） ✅

**目标：** 实现 ToolLoop + Compaction + 护栏，能用 LLM 驱动工具完成一个工单的完整处理。

### 任务

- [x] 实现 `LLMClient`（OpenAI API 封装，支持 function calling + 指数退避重试）
- [x] 实现 `run_tool_loop` 核心循环：
  - LLM 调用 → 解析 tool_calls → 分发给 ToolRegistry → 结果回填 → 下一轮
  - 支持并行工具调用（LLM 返回多个 tool_calls 时并发执行）
  - 无 tool_calls 时结束循环
  - Rich Console 实时输出工具执行状态
- [x] 实现护栏模块：
  - 最大轮次检查
  - 超时检查
  - Token 预算检查
- [x] 实现 Compaction 模块：
  - Token 计数（tiktoken）
  - 阈值触发压缩
  - 保留 system prompt + 最近 N 轮 + 早期摘要
- [x] 编写 Prompt 文件：AGENTS.md / SOUL.md / TOOLS.md
- [x] 实现 AgentSession + Runner（组装 prompt + sandbox + tool_loop + guardrails）

### 验证

```bash
# 手动触发处理一个真实工单，观察完整的工具调用过程
uv run zd-agent run <ticket-id> --dry-run    # 不实际发送回复
# 检查 sandbox 目录下的 analysis.md 和 reply-draft.md
```

---

## Phase 3 — 会话管理层（并发与队列） ✅

**目标：** 支持多工单并发处理，有队列管理和生命周期控制。

### 任务

- [x] 实现 `SessionManager`：
  - 并发控制（信号量，max_concurrent 可配置）
  - 等待队列（超限时排队）
  - 防重入（同一工单不重复处理）
  - Session 完成后自动取队列中下一个
- [x] 实现 Session 生命周期管理：
  - 状态流转（QUEUED → RUNNING → COMPLETED/FAILED/TIMEOUT → CLEANUP）
  - 成功时归档 + 清理 sandbox
  - 失败/超时时保留 sandbox + 标记需人工跟进
- [x] 实现 `zd-agent status` 命令（查看 sandbox 状态）
- [x] 实现 `zd-agent log <ticket-id>` 命令

### 验证

```bash
# 同时触发多个工单，观察并发控制和队列行为
uv run zd-agent batch 10001 10002 10003 10004 10005 10006 --dry-run
uv run zd-agent status
```

---

## Phase 4 — 入口层（自动轮询 + 完整 CLI） ✅

**目标：** 实现 Scheduler 自动轮询，完善所有 CLI 命令，系统可以完全自主运行。

### 任务

- [x] 实现 `Scheduler`：
  - 定时轮询 Zendesk API（`apscheduler`）
  - 过滤已处理/正在处理的工单
  - 发现新工单自动调用 `SessionManager.dispatch()`
- [x] 实现仓库管理命令：
  - `zd-agent repos fetch [repo...]`
  - `zd-agent repos status`
  - `zd-agent repos tags <repo>`
- [x] 实现定时仓库 fetch（cron 配置）
- [x] 实现 `zd-agent serve` 守护进程模式
- [x] 实现 `zd-agent stats` 统计命令

### 验证

```bash
# 启动守护进程，观察自动轮询和处理
uv run zd-agent serve
# 在 Zendesk 上创建一个测试工单，观察 Agent 自动处理
# 检查 Internal Reply 是否正确发送
```

---

## Phase 5 — 打磨与生产就绪 ✅

**目标：** 优化稳定性、可观测性，为生产使用做好准备。

### 任务

- [x] Prompt 调优：基于真实工单处理效果迭代 AGENTS.md / SOUL.md / TOOLS.md
- [x] 错误处理加固：
  - LLM API 调用重试（指数退避）— Phase 2 已实现
  - Zendesk API 限流处理（429 重试 + Retry-After）
  - 工具执行异常捕获与优雅降级
- [x] 按需 fetch 实现：git_show / git_grep / git_log / git_diff 版本不存在时自动 fetch
- [x] 处理报告完善：meta.json 统计信息（耗时、token、轮次、工具调用明细）+ runner 保底归档
- [x] 知识库反馈循环：归档的解决方案自动进入 knowledge_search 检索范围（tickets/ 目录已纳入搜索）
- [x] 预留 Webhook 入口接口（aiohttp HTTP server + POST /webhook/zendesk）
- [x] 预留 DockerSandbox 实现骨架（sandbox.py 工厂方法 + DockerSandbox 类）

---

## Phase 6 — 代码分析深化（进行中）

**目标：** 让 Agent 在版本确认后优先进入可浏览的完整代码树中分析，而不是停留在 `git_grep` / `git_show` 的浅层抽查。

### 背景

真实 dry-run 结果表明，当前流程虽然已经能：

- 读取工单字段（`Product` / `Version` / `Environment`）
- 使用 Helm Watchdog 做 chart → appVersion 映射
- 进入企业版相关仓库做 `git_grep`

但仍存在以下问题：

- [ ] 默认分析路径仍以 `git_grep` / `git_show` 为主，深度不足
- [ ] `list_repos` 只返回仓库状态，不返回具体 tag 列表，LLM 容易直接退回 `main`
- [ ] 创建了 worktree 也缺少配套的目录浏览 / 全文搜索工具，无法像在真实代码库中持续追踪调用链
- [ ] Prompt 还没有强制“命中后继续完整读代码”“有精确 tag 时不得直接退回 main”

### 任务

#### 6a. 版本与仓库可见性增强

- [x] 新增 `list_repo_tags` 工具：返回指定仓库的真实 tag 列表（支持 limit）
- [x] 在源码定位流程中优先使用 `list_repo_tags`，避免模型只看到“tag 数量”后直接回退到 `main`
- [x] 收紧版本规则：存在精确 tag 时，不允许直接使用 `main`

#### 6b. Worktree 成为主分析路径

- [x] 调整 Prompt：版本确认后，优先 `sandbox_create_worktree`
- [x] 将 `git_grep` / `git_show` 降级为快速定位或 fallback 工具，而不是默认主路径
- [x] 要求模型在 worktree 中继续做目录浏览、文件阅读、调用链追踪

#### 6c. 完整代码树分析工具

- [x] 新增 `sandbox_list`：列出 sandbox / worktree 下的目录结构
- [x] 新增 `sandbox_search`：在 sandbox / worktree 中全文搜索代码
- [x] 保留 `sandbox_read` 作为读取命中文件内容的主要工具
- [x] 扩展 `sandbox_read` 为片段优先读取（支持按行号读取），提升连续阅读体验并压缩 token 占用
- [x] 收紧 `sandbox_search` 输出策略（命中数/总长度裁剪），避免将大段搜索结果直接注入上下文

#### 6d. 分析深度约束

- [x] Prompt 明确要求：`git_grep` 或 `sandbox_search` 命中后，必须继续阅读相关完整文件
- [x] Prompt 明确要求：至少追踪一到两层调用链/配置链，不能只凭 grep 结果直接下结论
- [x] Prompt 明确要求：如果最终使用 `main` 作为 fallback，必须在结论中说明原因

#### 6e. 统一重试配置

- [x] 新增全局默认重试配置，并允许 `llm` / `zendesk` / `git` / `sandbox` 单独覆盖
- [x] 抽出统一异步重试助手，统一日志格式（attempt / max_attempts / retry_in / error_type）
- [x] 接入 `LLM`、`Zendesk`、`Git auto-fetch`、`sandbox_create_worktree`
- [x] Prompt 细化：Internal Reply 允许使用脱敏、精简的 Markdown 代码块作为证据

#### 6f. 多源知识融合

- [x] 新增在线官方文档工具：`official_docs_search` / `official_docs_read`
- [x] 在 `zendesk_get_ticket` 中加入代码层默认知识源建议（作为起点而非强制顺序）
- [x] Prompt 改为“多源融合 + 自主抉择”，不再写死 Zendesk KB → 官方文档 → 本地知识库 → 源码 的固定顺序
- [x] 用真实工单回归验证：官方文档、Zendesk KB、本地知识库、源码是否会被按问题类型动态组合使用

### 验证

```bash
# 用需要代码分析的真实工单做 dry-run
uv run zd-agent run <ticket-id> --dry-run

# 重点观察：
# 1. 是否先读取工单字段并做版本映射
# 2. 是否优先创建 worktree
# 3. 是否使用 sandbox_search / sandbox_read 连续分析代码
# 4. 是否仅在无精确 tag 时才回退到 main
```

---

## 里程碑总结

| Phase | 交付物 | 核心验证点 |
|-------|--------|-----------|
| 0 | 项目脚手架 | `zd-agent --help` 能运行 |
| 1 | 完整工具集 | 每个工具可独立调用并返回正确结果 |
| 2 | Agent 运行层 | 能用 LLM 驱动工具完成一个工单的完整处理（dry-run） |
| 3 | 并发管理 | 多工单并发处理，队列排队，状态可查 |
| 4 | 自动轮询 | `zd-agent serve` 启动后全自动处理新工单 |
| 5 | 生产就绪 | 稳定运行、错误恢复、可观测、Prompt 效果达标 |

### 推荐实施顺序

Phase 0 → 1 → 2 是核心路径，完成后就能手动触发处理工单看效果。Phase 3 → 4 是从"能用"到"自动化"。Phase 5 是打磨。

建议先快速跑通 Phase 0-2，用真实工单验证 Agent 的分析和回复质量，然后再投入 Phase 3-4 的并发和自动化。
