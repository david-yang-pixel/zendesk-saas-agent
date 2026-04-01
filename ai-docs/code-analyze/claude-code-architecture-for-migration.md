---
name: claude-code-architecture-for-migration
description: Claude Code 架构拆解与迁移蓝图（面向 zd-agent 项目）
created_at: 2026-04-01
---

# Claude Code 架构拆解与迁移蓝图

> 本文档基于 `example/claude-code-source`（v2.1.88）源码分析，面向 zd-agent 项目的演进方向 1+3（保持 Zendesk 主场景 + 预留通用化扩展）。

---

## 总览：Claude Code 的 7 个核心架构模式

```
┌─────────────────────────────────────────────────────────────┐
│  模式 1: 分层入口与快路径                                      │
│  模式 2: 异步生成器驱动的 Agent 主循环                          │
│  模式 3: 多策略上下文压缩管线                                   │
│  模式 4: 工具注册 + 并发编排 + 权限审批                          │
│  模式 5: 分层 Prompt 组装（静态 + 动态 + workspace）             │
│  模式 6: 子代理与上下文隔离                                     │
│  模式 7: 安全边界与 feature gating                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 模式 1: 分层入口与快路径

### Claude Code 怎么做

入口 `cli.tsx` 不直接加载完整应用，而是先按 argv 做分流：

- `--version` → 零 import 立即返回
- `remote-control` / `daemon` / `ps|logs|attach|kill` → 各自 dynamic import 后返回
- 常规路径 → 才加载 `main.tsx`（~200 个 import）

`main.tsx` 再用 Commander 建子命令体系，默认 action 才启动 REPL。

**为什么这么做**：冷启动性能。CLI 工具如果每次都加载全量模块，用户体感会很差。

### zd-agent 现在的状态

`entry/cli.py`（Typer）、`entry/webhook.py`（aiohttp）、`entry/scheduler.py`（APScheduler）三个入口各自独立，都会走到 `runtime/runner.py` 的 `run_agent()`。没有做快路径优化，但 Python 场景下冷启动不是核心痛点。

### 迁移建议

**低优先级**。你的三个入口已经很清晰。但建议统一收敛到一个 `dispatch_run()` 函数：

```python
async def dispatch_run(
    trigger: Trigger,  # CLI / Webhook / Scheduler
    ticket_id: int,
    *,
    hint: str | None = None,
    dry_run: bool = False,
    agent_config: AgentConfig | None = None,  # 预留多 agent
) -> RunResult:
    """统一入口，所有触发源最终调用这里"""
```

这样未来加新入口（Slack bot、API server 等）不用重复编排逻辑。

---

## 模式 2: 异步生成器驱动的 Agent 主循环

### Claude Code 怎么做

`query.ts` 的 `queryLoop` 是一个 `async function*`，每一轮 `while(true)` 做：

```
解构 state → 技能预取 → 上下文压缩管线（5 层）→ 拼 prompt →
调模型（流式）→ 收集 tool_use blocks →
  若无工具调用 → 错误恢复 / stop hook / 返回
  若有工具调用 → 执行工具 → 收集结果 → 拼入 messages → continue
```

关键设计：
- **yield 边界事件**：compact 完成、流开始、工具进度等都通过 yield 通知上层
- **state 对象**：每轮开头解构，continue 前重建，避免零散变量
- **多层错误恢复**：fallback model、reactive compact、max_output_tokens 渐进式恢复
- **turnCount + maxTurns**：防止无限循环

### zd-agent 现在的状态

`runtime/tool_loop.py` 是同步风格的 while 循环：

```python
while True:
    response = await llm_client.chat(messages)
    if no tool_calls: break
    for call in tool_calls:
        result = await registry.execute(call)
        messages.append(result)
```

已经有了核心骨架，但缺少：压缩管线、错误恢复、yield 式事件通知、turnCount 保护。

### 迁移建议

**高优先级**。改造 `tool_loop.py` 为 async generator：

```python
async def agent_loop(
    params: AgentLoopParams,
) -> AsyncGenerator[AgentEvent, None]:
    """主循环：模型 ↔ 工具的多轮迭代"""
    state = LoopState(
        messages=params.messages,
        turn_count=0,
        max_turns=params.max_turns or 30,
    )

    while True:
        state.turn_count += 1
        if state.turn_count > state.max_turns:
            yield MaxTurnsReached(state.turn_count)
            return

        # 1. 上下文压缩（按需）
        state.messages = await compress_if_needed(state.messages, params.context)

        # 2. 调模型
        yield TurnStarted(state.turn_count)
        response = await params.llm.chat(
            messages=state.messages,
            tools=params.tools,
            system_prompt=params.system_prompt,
        )

        # 3. 收集 assistant 消息
        state.messages.append(response.message)

        # 4. 检查是否有工具调用
        tool_calls = extract_tool_calls(response)
        if not tool_calls:
            yield TurnCompleted(response)
            return

        # 5. 执行工具
        for result in execute_tools(tool_calls, params.tool_context):
            yield ToolExecuted(result)
            state.messages.append(result.to_message())

        # continue → 下一轮
```

好处：调用方可以灵活消费事件（日志、归档、UI 更新），主循环本身保持纯粹。

---

## 模式 3: 多策略上下文压缩管线

### Claude Code 怎么做

每轮 API 调用前，按固定顺序跑 5 层压缩：

| 层 | 名称 | 作用 | 触发条件 |
|----|------|------|----------|
| 1 | Tool Result Budget | 截断/替换过大的工具结果 | 始终 |
| 2 | History Snip | 剪掉早期历史 | feature flag |
| 3 | Microcompact | 缓存级微压缩 | 始终 |
| 4 | Context Collapse | 折叠旧对话段 | token 接近阈值时 |
| 5 | Autocompact | 调模型做摘要压缩 | token 超过阈值 |

还有一个事后层：**Reactive Compact**（API 返回 413 后触发）。

### zd-agent 现在的状态

`runtime/compaction.py` 已有基础压缩能力，但只有一层策略。

### 迁移建议

**中优先级**。分两步走：

第一步（立刻可做）：在 `compress_if_needed()` 里加 tool result truncation：

```python
async def compress_if_needed(messages: list[Message], ctx: Context) -> list[Message]:
    # 第 1 层：截断过大的工具结果
    messages = truncate_tool_results(messages, max_chars=8000)

    # 第 2 层：token 超阈值时做摘要压缩
    token_count = estimate_tokens(messages)
    if token_count > ctx.compact_threshold:
        messages = await summarize_compress(messages, ctx)

    return messages
```

第二步（按需）：加 reactive compact（处理 API 返回上下文过长错误时自动压缩重试）。

---

## 模式 4: 工具注册 + 并发编排 + 权限审批

### Claude Code 怎么做

**注册**：`tools.ts` 中 `getAllBaseTools()` 返回静态数组，运行时与 MCP 工具合并。每个 Tool 是一个结构对象，核心接口：

```typescript
{
  name: string
  inputSchema: ZodSchema
  call(args, context, canUseTool, parentMessage): Promise<ToolResult>
  isConcurrencySafe(input): boolean  // 是否可并发
  isReadOnly(): boolean
  checkPermissions(input): PermissionResult
}
```

**并发编排**：`toolOrchestration.ts` 把一批 tool_use blocks 分成：
- 连续只读工具 → 并发执行（最大 10 并发）
- 非只读工具 → 串行执行

**权限审批**：`canUseTool` 是 async 函数，在工具执行前调用。如果需要用户确认，Promise 会挂起直到用户响应。

### zd-agent 现在的状态

`tools/base.py` 的 `Tool` 基类 + `ToolRegistry` 已经具备注册和执行能力。但：
- 没有并发安全标记（所有工具串行执行）
- 没有权限审批机制（所有工具直接执行）
- 没有 MCP 或动态工具扩展

### 迁移建议

**高优先级**（并发编排部分）。

第一步：给 `Tool` 基类加并发标记：

```python
class Tool(ABC):
    name: str
    description: str

    @property
    def is_concurrency_safe(self) -> bool:
        """只读工具返回 True，允许并发执行"""
        return False

    @abstractmethod
    async def execute(self, args: dict, session: AgentSession) -> ToolResult:
        ...
```

第二步：在 `tool_loop` 里做分批执行：

```python
async def execute_tools(
    tool_calls: list[ToolCall],
    registry: ToolRegistry,
    session: AgentSession,
) -> list[ToolResult]:
    batches = partition_by_concurrency(tool_calls, registry)
    results = []
    for batch in batches:
        if batch.concurrent:
            batch_results = await asyncio.gather(
                *[registry.execute(tc, session) for tc in batch.calls]
            )
        else:
            batch_results = []
            for tc in batch.calls:
                batch_results.append(await registry.execute(tc, session))
        results.extend(batch_results)
    return results
```

权限审批：当前 Zendesk 场景是自动化运行，暂不需要交互式审批。但预留 `can_use_tool` 回调接口，未来加 dry-run 审批、敏感操作确认等会很方便。

---

## 模式 5: 分层 Prompt 组装

### Claude Code 怎么做

System prompt 分三层：

```
┌─────────────────────────────────────┐
│  静态层（缓存友好）                    │
│  - 角色定义、工具使用规范、输出风格     │
│  - 构建时确定，跨会话复用              │
├─────────────────────────────────────┤
│  动态层（会话级）                      │
│  - 环境信息、MCP 说明、技能列表        │
│  - memory prompt、scratchpad          │
├─────────────────────────────────────┤
│  Workspace 层（用户可覆盖）            │
│  - CLAUDE.md / .claude/rules/*.md    │
│  - 以 userContext 注入消息前缀         │
└─────────────────────────────────────┘
```

子代理不走完整的 `getSystemPrompt()`，而是用精简版 `getAgentSystemPrompt()`，可选省略 claudeMd / gitStatus。

### zd-agent 现在的状态

`runtime/prompt_loader.py` 把 `SOUL.md + AGENTS.md + TOOLS.md` 拼成单一字符串。这些文件是包内资源，用户不可覆盖。

### 迁移建议

**中优先级**。分两步：

第一步：把 prompt 组装改成分段式：

```python
async def build_system_prompt(
    session: AgentSession,
    agent_config: AgentConfig | None = None,
) -> str:
    sections = []

    # 静态层：角色 + 通用规范
    sections.append(load_bundled_prompt("SOUL.md"))
    sections.append(load_bundled_prompt("AGENTS.md"))
    sections.append(load_bundled_prompt("TOOLS.md"))

    # 动态层：工单上下文、策略
    strategy = resolve_strategy(session)
    if strategy:
        sections.append(load_bundled_prompt(f"strategies/{strategy}.md"))

    # 动态层：工具列表（基于实际注册的工具）
    sections.append(format_tool_descriptions(session.registry))

    # Workspace 层（未来：用户自定义覆盖）
    workspace_prompt = load_workspace_prompt(agent_config)
    if workspace_prompt:
        sections.append(workspace_prompt)

    return "\n\n".join(sections)
```

第二步（预留）：支持 workspace 级覆盖。比如不同客户线的 `AGENTS.md` 可以不一样，放在 `config/agents/<agent_id>/` 下按需加载。

---

## 模式 6: 子代理与上下文隔离

### Claude Code 怎么做

`runAgent` 创建子代理时：

**共享**：
- 任务注册表（`setAppStateForTasks`）
- 取消信号传播（父级 abort → 子级 abort）
- 文件读取缓存（克隆）

**隔离**：
- 消息历史（子代理拿快照，不回写主会话）
- 权限状态（子代理默认不弹 UI）
- 工具集（可裁剪）
- System prompt（精简版）

子代理的输出最终**折叠为一条 tool_result** 回到主会话。

### zd-agent 现在的状态

没有子代理概念。所有工作都在一个 `tool_loop` 里完成。

### 迁移建议

**低优先级（但架构上要预留）**。

当前 zd-agent 的"策略分支"（code-bug vs 普通工单）是通过 prompt 切换实现的。如果未来策略越来越复杂（比如需要一个子代理专门做源码分析，另一个专门做知识库检索），可以参考 Claude Code 的模式：

```python
async def run_sub_agent(
    parent_session: AgentSession,
    task: str,
    *,
    tools: list[Tool] | None = None,
    system_prompt: str | None = None,
    max_turns: int = 10,
) -> SubAgentResult:
    """在隔离上下文中运行子代理"""
    child_messages = [
        # 从父级复制必要上下文
        *extract_context_messages(parent_session),
        # 子代理专属任务
        user_message(task),
    ]

    child_session = parent_session.fork(
        messages=child_messages,
        tools=tools or parent_session.registry.readonly_tools(),
    )

    result_messages = []
    async for event in agent_loop(child_session.to_params()):
        result_messages.append(event)

    return SubAgentResult(
        summary=summarize(result_messages),
        messages=result_messages,
    )
```

关键原则：**子代理的输出折叠为一条消息回到主会话**，不要共享 messages 列表。

---

## 模式 7: 安全边界与 Feature Gating

### Claude Code 怎么做

三层安全边界：

1. **构建期 gating**：`feature('X')` 在 Bun bundle 时裁剪代码路径
2. **运行时 gating**：每个 Command 的 `isEnabled()` + `availability` 条件
3. **执行时权限**：`canUseTool` + `checkPermissions` + REMOTE_SAFE / BRIDGE_SAFE 白名单

### zd-agent 现在的状态

- 没有 feature flag 体系
- 工具直接执行，没有权限层
- dry-run 是入口参数，不是工具级别的能力

### 迁移建议

**低优先级但值得做一点**。

最简版本：给 `AgentSession` 加一个 `mode` 字段：

```python
class RunMode(str, Enum):
    FULL = "full"           # 完整执行，可写 Zendesk
    DRY_RUN = "dry_run"     # 只分析不发送
    REVIEW = "review"       # 生成草稿，等人工确认

class AgentSession:
    mode: RunMode

    def can_write_zendesk(self) -> bool:
        return self.mode == RunMode.FULL
```

然后在 `zendesk.py` 的写操作工具里检查：

```python
async def execute(self, args, session):
    if not session.can_write_zendesk():
        return ToolResult(success=True, output="[dry-run] 已跳过写入操作")
    # ... 实际执行
```

---

## 迁移优先级总结

| 优先级 | 模式 | 理由 |
|--------|------|------|
| **高** | 模式 2: async generator 主循环 | 直接提升可观测性和错误恢复能力 |
| **高** | 模式 4: 工具并发编排 | Zendesk + 知识库 + 源码搜索场景下并发收益明显 |
| **中** | 模式 3: 多层压缩管线 | 长工单分析时 token 会爆，需要主动管理 |
| **中** | 模式 5: 分层 prompt 组装 | 为多 agent 配置和策略切换打基础 |
| **低** | 模式 1: 分层入口 | 当前入口已够用 |
| **低** | 模式 6: 子代理 | 当前单循环够用，但架构上预留 |
| **低** | 模式 7: 安全边界 | 最简版 dry-run mode 即可 |

---

## 附录：Claude Code 关键文件索引

| 模块 | 文件 | 核心函数/类型 |
|------|------|---------------|
| CLI 入口 | `src/entrypoints/cli.tsx` | `main()` |
| 主程序 | `src/main.tsx` | `main()`, `run()` |
| 命令注册 | `src/commands.ts` | `getCommands()`, `COMMANDS()` |
| **主循环** | `src/query.ts` | `query()`, `queryLoop()` |
| 工具类型 | `src/Tool.ts` | `Tool<I,O,P>`, `ToolUseContext` |
| 工具列表 | `src/tools.ts` | `getAllBaseTools()`, `getTools()` |
| **工具编排** | `src/services/tools/toolOrchestration.ts` | `runTools()`, `partitionToolCalls()` |
| 工具执行 | `src/services/tools/toolExecution.ts` | `runToolUse()` |
| **权限** | `src/utils/permissions/permissions.ts` | `hasPermissionsToUseTool()` |
| Prompt | `src/constants/prompts.ts` | `getSystemPrompt()` |
| Context | `src/context.ts` | `getUserContext()`, `getSystemContext()` |
| Workspace | `src/utils/claudemd.ts` | `getMemoryFiles()`, `getClaudeMds()` |
| **子代理** | `src/tools/AgentTool/runAgent.ts` | `runAgent()` |
| 子代理上下文 | `src/utils/forkedAgent.ts` | `createSubagentContext()` |
| 桥接 | `src/bridge/replBridge.ts` | `ReplBridgeHandle` |
| 传输 | `src/cli/transports/HybridTransport.ts` | WebSocket + HTTP |
| 安全 | `src/commands.ts` | `REMOTE_SAFE_COMMANDS`, `BRIDGE_SAFE_COMMANDS` |
