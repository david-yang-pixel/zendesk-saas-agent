---
name: agent-architecture
description: OpenClaw Agent 架构设计文档，涵盖核心模块、生命周期、并发模型、工作区文件系统、Skills、MCP 与隔离机制
created_at: 2026-03-25
---

# Agent 架构设计

## 目录

- [整体架构](#整体架构)
- [核心模块](#核心模块)
- [工作区文件系统](#工作区文件系统)
- [Skills 技能系统](#skills-技能系统)
- [MCP 集成](#mcp-集成)
- [内置工具全览](#内置工具全览)
- [文件系统访问策略](#文件系统访问策略)
- [生命周期](#生命周期)
- [会话标识与路由](#会话标识与路由)
- [并发模型](#并发模型)
- [工作区隔离](#工作区隔离)
- [子代理](#子代理subagent)
- [扩展点](#扩展点)

---

## 整体架构

OpenClaw 的 Agent 是**配置里的逻辑主体**加上**基于 `sessionKey` 的会话作用域**，再由 `runEmbeddedPiAgent` 驱动执行的完整运行时单元。

```
通道消息（Telegram / Discord / Slack / Signal / iMessage / WhatsApp ...）
    ↓
routing（解析 agentId + sessionKey）
    ↓
auto-reply / queue（排队、防重入）
    ↓
runReplyAgent → runEmbeddedPiAgent
    ↓
工具循环（openclaw-tools + 插件工具）
    ↓
投递回复
```

---

## 核心模块

### 1. 配置层

| 文件 | 职责 |
|------|------|
| `src/config/types.agents.ts` | `AgentConfig`、`AgentsConfig`、`AgentBinding` 等核心类型 |
| `src/config/bindings.ts` | 从 `cfg.bindings` 解析路由与 ACP 绑定 |
| `src/routing/session-key.ts` | 生成 `agent:<agentId>:...` 格式的会话键 |
| `src/routing/resolve-route.ts` | 根据来源 channel/account/peer 决定分发给哪个 `agentId` |

每个 Agent 在配置文件中以 `agents.list[]` 形式定义，支持的字段包括：

- `id`：唯一标识符
- `model`：模型配置（支持 primary + fallbacks）
- `workspace`：工作目录
- `agentDir`：agent 文件目录（AGENTS.md / SOUL.md 等）
- `sandbox`：沙箱配置
- `tools`：工具策略
- `skills`：技能过滤
- `subagents`：子代理配置

### 2. 执行层

| 文件 | 职责 |
|------|------|
| `src/agents/agent-scope.ts` | "配置 → 单次运行上下文"中枢，解析 sessionAgentId、工作区、技能过滤等 |
| `src/agents/pi-embedded-runner/run.ts` | 核心入口 `runEmbeddedPiAgent`：模型解析、插件钩子、工具循环、compaction、失败重试 |
| `src/agents/pi-embedded-runner/runs.ts` | 多轮运行状态管理：`ACTIVE_EMBEDDED_RUNS` Map，支持 abort 和中途注入消息 |
| `src/agents/agent-command.ts` | CLI 侧入口，组装会话后调用嵌入式运行器 |

#### 两种运行模式

| 模式 | 说明 |
|------|------|
| `embedded`（默认） | `pi-embedded-runner` 直接在进程内运行 |
| `acp` | 远程 Agent 协议，通过网关 `gateway/server-methods/agent*.ts` 调用外部 agent |

### 3. 自动回复管线

| 文件 | 职责 |
|------|------|
| `src/auto-reply/reply/agent-runner.ts` | `runReplyAgent` → `runAgentTurnWithFallback`，连接通道消息与执行层 |
| `src/auto-reply/reply/queue-policy.ts` | 决定新消息是立即执行、排队还是丢弃 |
| `src/auto-reply/reply/queue/state.ts` | 每个 sessionKey 的队列状态（`FOLLOWUP_QUEUES` Map） |

通道（Telegram/Discord/Slack 等）通过路由层把消息派发到自动回复管线，**不直接依赖 `agents/` 内部实现**，保持解耦。

### 4. 工具集

`src/agents/openclaw-tools.ts` 中的 `createOpenClawTools` 聚合了所有内置工具，详见 [内置工具全览](#内置工具全览)。

---

## 工作区文件系统

Agent 运行时使用一套约定的 **workspace 目录**（`agentDir`），存放控制 agent 行为的配置文件。这些文件在每次运行时被读取并注入 system prompt，是 agent 知识和行为规则的核心来源。

### 标准 Workspace 文件

| 文件名 | 作用 |
|--------|------|
| `AGENTS.md` | Agent 的主要指令文档，定义行为规则（等同于 CLAUDE.md / CODEX.md） |
| `SOUL.md` | Agent 的个性、价值观与底层行为倾向 |
| `TOOLS.md` | 工具使用规范与额外工具说明 |
| `IDENTITY.md` | Agent 的自我认知描述（给模型读的） |
| `USER.md` | 用户偏好与个性化设置 |
| `BOOTSTRAP.md` | 额外的 system prompt 补充内容 |
| `HEARTBEAT.md` | 定时任务触发时的行为指令 |
| `MEMORY.md` / `memory.md` | 持久化记忆内容 |

### 文件读取机制

- 默认工作区路径：`~/.openclaw/workspace`（或多 profile 下 `~/.openclaw/workspace-<profile>`）
- 通过 `openBoundaryFile` 安全读取（防路径穿越），支持 **inode/size/mtime 文件缓存**，避免重复 IO
- 最大单文件读取：**2 MB**

### 额外 Bootstrap 文件

可通过配置注入额外的 bootstrap 文件（`cfg.agents.defaults.extraBootstrap`），支持多文件合并注入 system prompt。安全策略与标准文件相同：必须在 workspaceDir 边界内，不允许硬链接。

### Workspace 目录结构

```
~/.openclaw/workspace/
  AGENTS.md          ← 主要行为指令
  SOUL.md            ← 个性设置
  TOOLS.md           ← 工具规范
  IDENTITY.md        ← 身份描述
  USER.md            ← 用户偏好
  BOOTSTRAP.md       ← 额外 system prompt
  HEARTBEAT.md       ← 定时任务指令
  MEMORY.md          ← 持久化记忆
  skills/            ← workspace 级别的 Skills 目录
    <skill-name>/
      SKILL.md
  .agents/
    skills/          ← 项目级别 Skills（project-scope）
```

---

## Skills 技能系统

Skills 是注入到 system prompt 的**可复用专项知识文档**，每个 skill 是一个含有 `SKILL.md` 的目录。模型读取 skill 列表后，在遇到对应任务时自行 `read` 对应的 skill 文件获取详细指令。

### Skill 加载来源（优先级从低到高）

| 来源 | 路径 | 说明 |
|------|------|------|
| `openclaw-extra` | `cfg.skills.load.extraDirs[]` 配置 + 插件 skill 目录 | 外部自定义/插件提供 |
| `openclaw-bundled` | 内置 bundled skills 目录 | OpenClaw 随包提供 |
| `openclaw-managed` | `~/.openclaw/skills/` | 用户通过 `openclaw skills` 命令安装 |
| `agents-skills-personal` | `~/.agents/skills/` | 个人全局 skills |
| `agents-skills-project` | `<workspaceDir>/.agents/skills/` | 项目级 skills |
| `openclaw-workspace` | `<workspaceDir>/skills/` | **最高优先级**，workspace 本地 skills |

同名 skill 后面的来源会**覆盖**前面的来源。

### Skill 文件结构

每个 skill 目录包含 `SKILL.md`，支持 YAML frontmatter 控制行为：

```yaml
---
always: true               # 是否始终注入（不等用户触发）
primaryEnv: node           # 主要运行环境
requires:
  bins: [git, node]        # 需要系统有这些二进制文件
  env: [GITHUB_TOKEN]      # 需要这些环境变量存在
install:                   # 可选的自动安装规范
  - kind: brew
    formula: gh
os: [darwin, linux]        # 限定操作系统
---
```

### 注入到 System Prompt 的方式

- **完整模式**：name + description + 文件路径，让模型按需 read
- **紧凑模式**（token 不足时降级）：仅 name + 文件路径，省略 description
- **截断**：超出 `maxSkillsInPrompt`（默认 150）或 `maxSkillsPromptChars`（默认 30,000）时截断并警告

### Agent 级别过滤

可在 `agents.list[].skills` 中配置白名单，只允许特定 skill 生效：

```yaml
agents:
  list:
    - id: my-agent
      skills: [github, docker, python]  # 只加载这三个 skill
```

### 插件 Skills

插件（`extensions/*`）可以在 `openclaw.plugin.json` 中声明 `skills` 字段，指向插件内部的 skill 目录。启用插件后，其 skill 自动合并进 extra 来源。安全检查：skill 路径必须在插件根目录内，禁止路径逃逸。

### Skills → Sandbox 同步

当 sandbox 以 `scope=session`（非 `rw` 模式）运行时，skills 会被**自动同步**到沙箱工作区副本（`syncSkillsToWorkspace`），确保沙箱内 agent 也能读取 skills。

---

## MCP 集成

OpenClaw 通过 **MCP（Model Context Protocol）** 将外部工具服务接入 agent 工具集，使用 `@modelcontextprotocol/sdk` 实现。

### 配置来源（两层合并）

```
束包（bundle）MCP 配置         用户 cfg.mcp.servers 配置
       ↓                              ↓
loadEnabledBundleMcpConfig    normalizeConfiguredMcpServers
       ↓                              ↓
         合并（用户配置覆盖 bundle 默认值）
                    ↓
             loadEmbeddedPiMcpConfig
```

- **bundle MCP**：插件通过 `openclaw.plugin.json` 声明 MCP server 配置，插件启用后自动生效
- **用户配置**：`cfg.mcp.servers` 中直接配置，优先级更高，可覆盖 bundle 默认值

### 运行时接入流程

每次 agent run 启动时（`createBundleMcpToolRuntime`）：

1. 读取合并后的 MCP server 配置
2. 为每个 server 创建 `StdioClientTransport`（子进程通信）
3. 连接后调用 `listTools` 获取所有工具定义
4. 将 MCP tools 包装为 `AnyAgentTool`，合并进工具列表
5. run 结束时统一 `dispose`，关闭所有 MCP server 子进程

### 安全限制

- 项目本地 pi 设置（`.pi-settings.json`）默认策略为 `sanitize`：**禁止**覆盖 `shellPath`、`shellCommandPrefix` 等 shell 执行相关配置
- MCP server 名称冲突时，后注册的工具会被跳过并记录警告

### 相关文件

| 文件 | 职责 |
|------|------|
| `src/agents/embedded-pi-mcp.ts` | 合并 bundle + 用户配置 |
| `src/agents/pi-bundle-mcp-tools.ts` | 启动 MCP server、包装工具、管理生命周期 |
| `src/agents/mcp-stdio.ts` | Stdio transport 配置解析 |
| `src/agents/pi-project-settings.ts` | 项目 pi 设置加载与 sanitize |

---

## 内置工具全览

`src/agents/openclaw-tools.ts` 中 `createOpenClawTools` 注册的完整工具集：

### 通信类

| 工具 | 文件 | 说明 |
|------|------|------|
| `message` | `tools/message-tool.ts` | 向通道发送消息（支持多账号、线程路由） |
| `tts` | `tools/tts-tool.ts` | 文字转语音 |

### 会话与子代理类

| 工具 | 文件 | 说明 |
|------|------|------|
| `sessions_list` | `tools/sessions-list-tool.ts` | 列出当前活跃会话 |
| `sessions_history` | `tools/sessions-history-tool.ts` | 读取会话历史 |
| `sessions_send` | `tools/sessions-send-tool.ts` | 向指定会话发送消息（跨 session 通信） |
| `sessions_spawn` | `tools/sessions-spawn-tool.ts` | 创建新会话（子代理） |
| `sessions_yield` | `tools/sessions-yield-tool.ts` | 将控制权让给调用方 |
| `session_status` | `tools/session-status-tool.ts` | 查询当前会话状态 |
| `subagents` | `tools/subagents-tool.ts` | 子代理管理 |
| `agents_list` | `tools/agents-list-tool.ts` | 列出所有配置的 agents |

### 网页类

| 工具 | 文件 | 说明 |
|------|------|------|
| `web_search` | `tools/web-search.ts` | 网络搜索（支持多 provider） |
| `web_fetch` | `tools/web-fetch.ts` | 抓取网页内容（支持 Firecrawl） |
| `browser` | `tools/browser-tool.ts` | 浏览器自动化（CDP，支持沙箱 bridge） |

### 媒体类

| 工具 | 文件 | 说明 |
|------|------|------|
| `image` | `tools/image-tool.ts` | 读取/处理本地图片 |
| `image_generate` | `tools/image-generate-tool.ts` | 生成图片 |
| `pdf` | `tools/pdf-tool.ts` | 读取 PDF 文件 |
| `canvas` | `tools/canvas-tool.ts` | 渲染画布/图表 |

### 系统类

| 工具 | 文件 | 说明 |
|------|------|------|
| `cron` | `tools/cron-tool.ts` | 创建/管理定时任务 |
| `gateway` | `tools/gateway-tool.ts` | 与 OpenClaw gateway 通信 |
| `nodes` | `tools/nodes-tool.ts` | 多媒体节点操作（图片/音视频内容） |

### 记忆类

记忆工具（`tools/memory-tool.ts`）是**可选工具**，仅当配置了 `memorySearch` 时启用：

- `memory_search`：语义搜索记忆内容
- `memory_get`：按路径读取记忆文件片段

记忆存储可由插件扩展（通过 memory slot 机制），支持替换默认的本地文件存储。

### 插件工具

所有插件通过 `resolvePluginTools` 注入，与内置工具合并，名称冲突时插件工具被跳过。

---

## 文件系统访问策略

`src/agents/tool-fs-policy.ts` 定义了 `ToolFsPolicy`，控制工具的文件访问范围：

```typescript
type ToolFsPolicy = {
  workspaceOnly: boolean;  // 为 true 时限制工具只能访问 workspaceDir 内的路径
};
```

- 全局配置：`cfg.tools.fs.workspaceOnly`
- agent 级别覆盖：`agents.list[].tools.fs.workspaceOnly`（优先级更高）
- 生效范围：image、pdf、image_generate 等文件读写类工具

---

## 生命周期

```
1. 入站
   通道消息 → routing 解析 agentId + sessionKey

2. 排队
   queue-policy 判断：run-now / enqueue-followup / drop

3. 运行开始
   生成 runId → infra/agent-events.ts 注册 runContext
   → 触发插件 beforeAgentStart 钩子

4. 执行（pi-embedded-runner）
   runEmbeddedAttempt → 流式输出 + 工具循环
   可中途：abortEmbeddedPiRun / queueEmbeddedPiMessage

5. Compaction（上下文窗口管理）
   超出 token 限制时自动压缩历史，支持失败重试

6. 运行结束
   更新 session store → 发出事件 → clearAgentRunContext → 投递回复
```

---

## 会话标识与路由

### sessionKey 格式

```
agent:<agentId>:<channel>:<peer>
```

不同的通道/对话/用户各自拥有独立的 `sessionKey`，决定了会话的隔离粒度。

### 路由绑定

`cfg.bindings` 中配置从 channel/account/peer/guild/team/roles 到 `agentId` 的映射，支持：

- `AgentRouteBinding`：基于通道路由
- `AgentAcpBinding`：ACP 远程绑定

---

## 并发模型

### 跨会话：完全并发

`ACTIVE_EMBEDDED_RUNS` 是一个 `Map<sessionId, handle>`，同一时刻可以存在**任意数量**的活跃会话，彼此完全并行，互不阻塞。

### 同一会话内：队列序列化

当同一 session 正在处理时，新消息到来触发 `resolveActiveRunQueueAction`：

```
isActive=false  → run-now（直接执行）
isActive=true
  ├─ isHeartbeat       → drop（丢弃）
  ├─ shouldFollowup    → enqueue-followup（排队）
  ├─ mode="steer"      → enqueue-followup（注入当前 run）
  └─ 其他              → run-now（部分模式允许抢占）
```

#### 队列模式（QueueMode）

| 模式 | 行为 |
|------|------|
| `collect`（默认） | 收集消息，当前 run 结束后合并处理 |
| `followup` | 当前 run 结束后依次执行后续消息 |
| `steer` | 新消息注入正在运行的 agent（中途转向） |
| `interrupt` | 中断当前 run，立即处理新消息 |
| `queue` | 严格串行队列 |

#### 队列参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `debounceMs` | 1000ms | 短时间内多条消息合并的等待窗口 |
| `cap` | 20 条 | 队列上限 |
| `dropPolicy` | `summarize` | 超限策略：丢弃旧消息/新消息/自动汇总 |

---

## 工作区隔离

### 无 sandbox（默认）

**没有隔离**。所有会话共享同一个宿主机 `workspaceDir`，并发写入同一文件会产生 race condition，后写覆盖前写，系统不作任何协调。

> 设计前提：不同会话对应不同用户/对话，正常情况下不会同时修改同一文件。

### 有 sandbox 时

通过 `SandboxScope` 控制隔离级别：

| scope | 隔离粒度 | 20 个会话同时写同一文件的结果 |
|-------|---------|---------------------------|
| `session` | 每个 sessionKey 独立目录/容器 | 完全隔离，各改各的副本 |
| `agent` | 同一 agentId 共享目录/容器 | 同 agent 的会话仍然竞争 |
| `shared` | 所有会话共享根目录 | 等同于无隔离 |

scope=session 时，目录由 sessionKey 的哈希决定：

```
workspaceRoot/<sessionKey-slug-hash>/
```

### `workspaceAccess` 与宿主机访问权限

| workspaceAccess | 效果 |
|----------------|------|
| `rw` | 容器直接读写宿主机真实目录（沙箱对文件隔离无效） |
| `ro` | 宿主机只读挂载，容器写操作落到沙箱自己目录 |
| `none` | 不挂载宿主机目录 |

**最强隔离**：`scope=session` + `workspaceAccess=ro`（每会话独立副本，不写回宿主机）

---

## 子代理（Subagent）

支持在一次 agent 运行中 spawn 子会话：

- `src/agents/subagent-spawn.ts`：创建子会话
- 子代理继承父代理的 `spawnedBy` 上下文，可追溯调用链
- 子代理完成后通过 `internal-events.ts` 通知父代理
- 投递目标可通过插件钩子 `subagent_delivery_target` 自定义

---

## 扩展点

扩展（`extensions/*`）通过 `openclaw/plugin-sdk/agent-runtime` 这个公开 surface 接入 Agent，主要钩子：

| 钩子 | 触发时机 | 用途 |
|------|---------|------|
| `beforeAgentStart` | Agent 启动前 | 注入额外工具、修改配置 |
| `subagent_spawning` | spawn 子代理时 | 控制子代理行为 |
| `subagent_delivery_target` | 子代理投递回复时 | 自定义回复路由 |

关键文件索引：`src/plugins/types.ts`、`src/plugin-sdk/agent-runtime.ts`
