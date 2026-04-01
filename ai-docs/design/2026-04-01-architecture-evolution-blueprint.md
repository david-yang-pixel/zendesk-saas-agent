# zd-agent 架构演进蓝图

> 基于 Claude Code 源码分析，结合 zd-agent 现状，制定的完整架构升级方案。
>
> 创建日期：2026-04-01
> 约束：向后兼容线上环境（config.yaml / .env / prompts）；支持多厂商 LLM（OpenAI + Anthropic）；蓝图先行，增量实施。

---

## 目录

1. [概述与目标](#1-概述与目标)
2. [Phase 1 — LLM 抽象层（多厂商支持）](#2-phase-1--llm-抽象层多厂商支持)
3. [Phase 2 — Runtime 主循环升级（精简 AsyncGenerator）](#3-phase-2--runtime-主循环升级精简-asyncgenerator)
4. [Phase 3 — Prompt 系统升级（分段注册 + 动态上下文）](#4-phase-3--prompt-系统升级分段注册--动态上下文)
5. [Phase 4 — Tool 系统升级（编排 + 上下文 + 预算）](#5-phase-4--tool-系统升级编排--上下文--预算)
6. [Phase 5 — 上下文压缩升级（多策略分层）](#6-phase-5--上下文压缩升级多策略分层)
7. [Phase 6 — Subagent 系统](#7-phase-6--subagent-系统)
8. [向后兼容策略](#8-向后兼容策略)
9. [目录结构变更](#9-目录结构变更)
10. [实施路线图](#10-实施路线图)

---

## 1. 概述与目标

### 1.1 现状总结

| 模块 | 当前实现 | 核心局限 |
|------|---------|---------|
| Runtime | `while True` 同步循环，`run_tool_loop` 返回 `None` | 循环逻辑与日志输出耦合、不支持子任务派发/复用 |
| LLM | 硬编码 `AsyncOpenAI`，tool schema 为 OpenAI 专用格式 | 无法切换厂商；Anthropic 的 tool calling 格式完全不同 |
| Prompt | `SOUL.md + AGENTS.md + TOOLS.md` 简单拼接 | 无条件组装、无动态上下文注入、无法按工单类型/模型裁剪 |
| Tool | `Tool` ABC + `ToolRegistry` 全部并行执行 | 无并发安全标记、无执行上下文、工具输出无大小控制 |
| 压缩 | 单一策略：LLM 摘要压缩 | 每次压缩都消耗一次 LLM 调用；无 tool result 裁剪；关键信息可能被压缩掉 |
| Subagent | 不存在 | 单一 agent 上下文承载所有步骤，窗口压力大 |

### 1.2 目标

- **保持 Zendesk 专用 Agent 定位**，不做通用平台
- **内部 runtime 达到 Claude Code 级别的架构能力**
- 每个 Phase 独立可交付，可以单独合并上线
- **向后兼容**：现有 config.yaml、.env、prompts 文件不需要修改即可运行

### 1.3 为什么按这个顺序

```
Phase 1 (LLM 抽象层)        ← 其他所有 Phase 都依赖它
  ↓
Phase 2 (Runtime 精简升级)    ← Subagent 需要复用循环逻辑
  ↓
Phase 3 (Prompt 升级)        ← 独立于 Tool/Subagent
Phase 4 (Tool 升级)          ← 独立于 Prompt/Subagent
  ↓
Phase 5 (压缩升级)           ← 依赖 Tool 的 result budget 机制
  ↓
Phase 6 (Subagent)           ← 依赖 Runtime + Tool + Prompt 的全部改造
```

---

## 2. Phase 1 — LLM 抽象层（多厂商支持）

### 2.1 设计目标

- 支持 OpenAI 和 Anthropic 两个 provider
- 架构上可扩展到任意厂商
- Tool schema 格式自动转换
- 向后兼容：`config.yaml` 中 `llm.provider: openai` 继续工作

### 2.2 核心类型定义

```python
# src/zd_agent/runtime/llm/types.py

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

@dataclass
class LLMResponse:
    """厂商无关的 LLM 响应"""
    content: str | None
    tool_calls: list[ToolCallRequest]
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    raw: Any = None  # 原始厂商响应，调试用

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

@dataclass
class ToolCallRequest:
    """厂商无关的工具调用请求"""
    id: str
    name: str
    arguments: str  # JSON 字符串，保持与现有 registry.execute 兼容

@dataclass
class ToolSchema:
    """厂商无关的工具定义"""
    name: str
    description: str
    parameters: dict[str, Any]

class LLMProvider(ABC):
    """LLM 厂商抽象接口"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def format_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将统一的消息格式转换为厂商特定格式"""
        ...

    @abstractmethod
    def format_tool_result(self, tool_call_id: str, name: str, content: str) -> dict[str, Any]:
        """将工具结果格式化为厂商特定的 message 格式"""
        ...
```

### 2.3 Provider 实现

```python
# src/zd_agent/runtime/llm/openai_provider.py

class OpenAIProvider(LLMProvider):
    """OpenAI Chat Completions 实现"""

    def __init__(self, api_key: str, model: str, base_url: str | None = None, timeout: int = 120):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=float(timeout), max_retries=0)
        self._model = model

    async def chat(self, messages, tools=None, temperature=0.2) -> LLMResponse:
        kwargs = {"model": self._model, "messages": messages, "temperature": temperature}
        if tools:
            kwargs["tools"] = [self._to_openai_tool(t) for t in tools]
            kwargs["tool_choice"] = "auto"

        response = await self._client.chat.completions.create(**kwargs)
        return self._parse_response(response)

    def format_messages(self, messages):
        # OpenAI 格式就是当前的 messages 格式，直接透传
        return messages

    def format_tool_result(self, tool_call_id, name, content):
        return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}

    @staticmethod
    def _to_openai_tool(schema: ToolSchema) -> dict:
        return {"type": "function", "function": {"name": schema.name, "description": schema.description, "parameters": schema.parameters}}

    def _parse_response(self, response) -> LLMResponse:
        choice = response.choices[0]
        message = choice.message
        usage = response.usage
        tool_calls = []
        for tc in (message.tool_calls or []):
            tool_calls.append(ToolCallRequest(id=tc.id, name=tc.function.name, arguments=tc.function.arguments))
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            raw=message,
        )
```

```python
# src/zd_agent/runtime/llm/anthropic_provider.py

class AnthropicProvider(LLMProvider):
    """Anthropic Messages API 实现"""

    def __init__(self, api_key: str, model: str, timeout: int = 120):
        self._client = AsyncAnthropic(api_key=api_key, timeout=float(timeout))
        self._model = model

    async def chat(self, messages, tools=None, temperature=0.2) -> LLMResponse:
        # Anthropic 的 system 在顶层参数里，不在 messages 里
        system_content, non_system = self._extract_system(messages)
        kwargs = {
            "model": self._model,
            "messages": self._convert_messages(non_system),
            "max_tokens": 8192,
            "temperature": temperature,
        }
        if system_content:
            kwargs["system"] = system_content
        if tools:
            kwargs["tools"] = [self._to_anthropic_tool(t) for t in tools]

        response = await self._client.messages.create(**kwargs)
        return self._parse_response(response)

    def format_messages(self, messages):
        # 需要将 OpenAI 格式的 tool 消息转换为 Anthropic 的 tool_result content block
        # 具体转换逻辑在此实现
        ...

    def format_tool_result(self, tool_call_id, name, content):
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_call_id, "content": content}],
        }

    @staticmethod
    def _to_anthropic_tool(schema: ToolSchema) -> dict:
        return {"name": schema.name, "description": schema.description, "input_schema": schema.parameters}
```

### 2.4 工厂与配置

```python
# src/zd_agent/runtime/llm/factory.py

def create_llm_provider(cfg: LLMConfig, env: EnvSettings) -> LLMProvider:
    """根据配置创建 LLM Provider"""
    if cfg.provider == "openai":
        return OpenAIProvider(
            api_key=env.openai_api_key,
            model=cfg.model,
            base_url=cfg.base_url,
            timeout=cfg.timeout,
        )
    elif cfg.provider == "anthropic":
        return AnthropicProvider(
            api_key=env.anthropic_api_key,
            model=cfg.model,
            timeout=cfg.timeout,
        )
    else:
        raise ValueError(f"不支持的 LLM provider: {cfg.provider}")
```

### 2.5 配置变更（向后兼容）

```yaml
# config.yaml 变更 — 所有新增字段都有默认值，旧配置无需修改
llm:
  provider: openai        # 新增: "openai" | "anthropic"（默认 "openai"，兼容旧配置）
  model: gpt-5.4
  base_url: https://api.openai.com/v1
  temperature: 0.2
```

```python
# config.py 新增字段
class LLMConfig(BaseModel):
    provider: str = "openai"  # 已存在，无需改动
    model: str = "gpt-4o"
    temperature: float = 0.2
    base_url: str | None = None
    timeout: int = 120

# EnvSettings 新增
class EnvSettings(BaseSettings):
    # ... 现有字段不变 ...
    anthropic_api_key: str = ""  # 新增
```

### 2.6 兼容层：LLMClient 保留

为了向后兼容，保留 `LLMClient` 类作为 facade：

```python
# src/zd_agent/runtime/llm_client.py（改造后）

class LLMClient:
    """向后兼容的 LLM 客户端外壳，内部委托给 LLMProvider"""

    def __init__(self) -> None:
        cfg = get_config()
        env = EnvSettings()
        self._provider = create_llm_provider(cfg.llm, env)
        self._tools_schemas: list[ToolSchema] = []

    async def chat(self, messages, tools=None) -> LLMResponse:
        tool_schemas = None
        if tools:
            # 将 OpenAI 格式的 tool dict 转为 ToolSchema（兼容旧调用方式）
            tool_schemas = [ToolSchema(name=t["function"]["name"], description=t["function"]["description"], parameters=t["function"]["parameters"]) for t in tools]
        return await retry_async(
            operation="llm_chat",
            policy=get_retry_policy("llm"),
            func=lambda: self._provider.chat(messages, tool_schemas),
            should_retry=_is_retryable,
            log_context={"provider": type(self._provider).__name__},
        )
```

### 2.7 消息格式统一

引入 `MessageStore` 负责管理消息列表，屏蔽厂商格式差异：

```python
# src/zd_agent/runtime/messages.py

class MessageStore:
    """消息存储，以厂商无关的规范格式存储，按需转换"""

    def __init__(self, provider: LLMProvider):
        self._messages: list[dict[str, Any]] = []
        self._provider = provider

    def append_system(self, content: str) -> None:
        self._messages.append({"role": "system", "content": content})

    def append_user(self, content: str | list) -> None:
        self._messages.append({"role": "user", "content": content})

    def append_assistant(self, raw_message: dict[str, Any]) -> None:
        self._messages.append(raw_message)

    def append_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        msg = self._provider.format_tool_result(tool_call_id, name, content)
        self._messages.append(msg)

    def to_api_messages(self) -> list[dict[str, Any]]:
        """转为厂商可用的格式"""
        return self._provider.format_messages(self._messages)

    @property
    def raw(self) -> list[dict[str, Any]]:
        """原始消息列表（用于 compaction 等内部处理）"""
        return self._messages

    @raw.setter
    def raw(self, value: list[dict[str, Any]]) -> None:
        self._messages = value

    def __len__(self) -> int:
        return len(self._messages)
```

---

## 3. Phase 2 — Runtime 主循环升级（精简 AsyncGenerator）

> **设计决策**：zd-agent 是后台自动化 Agent（webhook / scheduler / CLI 触发，输出为 Zendesk Internal Note），
> 没有前端实时交互界面，因此**不需要流式输出、暂停/恢复**等交互式能力。
> AsyncGenerator 的唯一目的是让 **Subagent (Phase 6) 能复用同一循环逻辑**，
> 同时把日志输出从核心循环中解耦。事件类型精简到 4 个，不做过度设计。

### 3.1 设计目标

- 主循环改为 `AsyncGenerator`，**仅为 Subagent 复用准备**
- 事件类型精简到 **4 个**（不是 10 个），只覆盖日志和 Subagent 需要的信息
- 保留 `run_tool_loop` 函数签名作为兼容入口
- 现有的 Rich console 输出保持在兼容包装器中，不拆独立模块

### 3.2 精简事件类型

```python
# src/zd_agent/runtime/events.py

from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass
class AgentEvent:
    """事件基类"""
    round: int = 0

@dataclass
class LoopProgress(AgentEvent):
    """每轮进度汇报（合并了 RoundStart + LLMResponse 的关键信息）"""
    message_count: int = 0
    token_count: int = 0
    llm_elapsed: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_call_count: int = 0

@dataclass
class ToolExecuted(AgentEvent):
    """工具执行完成"""
    tool_name: str = ""
    success: bool = True
    output_length: int = 0
    elapsed: float = 0.0
    error: str | None = None

@dataclass
class LoopFinished(AgentEvent):
    """循环结束（正常完成 / 护栏触发 / 异常）"""
    status: str = "completed"  # "completed" | "guardrail" | "error"
    total_rounds: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    reason: str = ""  # 护栏原因或错误信息
```

### 3.3 AsyncGenerator 主循环

```python
# src/zd_agent/runtime/agent_loop.py

from typing import AsyncGenerator

async def agent_loop(
    session: AgentSession,
    provider: LLMProvider,
    message_store: MessageStore,
    registry: ToolRegistry,
    guardrails: Guardrails,
    tool_schemas: list[ToolSchema],
) -> AsyncGenerator[AgentEvent, None]:
    """核心 agent 循环

    与当前 tool_loop.py 的 while True 逻辑完全等价，
    区别仅在于 yield 事件而非直接 print。
    Subagent 可直接调用此函数复用循环。
    """
    session.status = SessionStatus.RUNNING

    try:
        while True:
            guardrails.check_all()

            # 压缩检查
            if guardrails.should_compact():
                message_store.raw = await compact_messages(message_store.raw, provider)

            guardrails.increment_round()
            session.round_count = guardrails.current_round

            # LLM 调用
            t0 = time.monotonic()
            response = await provider.chat(
                message_store.to_api_messages(),
                tools=tool_schemas,
            )
            llm_elapsed = time.monotonic() - t0

            guardrails.add_tokens(response.prompt_tokens, response.completion_tokens)
            session.token_usage.prompt_tokens += response.prompt_tokens
            session.token_usage.completion_tokens += response.completion_tokens

            yield LoopProgress(
                round=guardrails.current_round,
                message_count=len(message_store),
                token_count=guardrails.total_tokens,
                llm_elapsed=llm_elapsed,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                tool_call_count=len(response.tool_calls),
            )

            message_store.append_assistant(response.raw)

            # 无 tool calls → 完成
            if not response.has_tool_calls:
                if response.content:
                    session.analysis += f"\n{response.content}"
                session.status = SessionStatus.COMPLETED
                yield LoopFinished(
                    status="completed",
                    total_rounds=guardrails.current_round,
                    total_tokens=guardrails.total_tokens,
                    elapsed_seconds=guardrails.elapsed_seconds,
                )
                return

            # 执行工具（复用现有的并行执行逻辑）
            for tc in response.tool_calls:
                t0 = time.monotonic()
                result = await registry.execute(tc.name, tc.arguments, session)
                elapsed = time.monotonic() - t0

                output = result.output if result.success else f"错误: {result.error}"
                message_store.append_tool_result(tc.id, tc.name, output)

                yield ToolExecuted(
                    round=guardrails.current_round,
                    tool_name=tc.name,
                    success=result.success,
                    output_length=len(output),
                    elapsed=elapsed,
                    error=result.error,
                )

            # 图片注入
            if session.image_urls and not session.images_injected:
                _inject_images(session, message_store)
                session.images_injected = True

    except GuardrailTriggered as e:
        session.status = SessionStatus.TIMEOUT if "超时" in e.reason else SessionStatus.FAILED
        session.error_message = e.reason
        session.analysis += f"\n\n⚠️ 处理被护栏终止: {e.reason}"
        yield LoopFinished(status="guardrail", reason=e.reason,
                           total_rounds=guardrails.current_round,
                           total_tokens=guardrails.total_tokens,
                           elapsed_seconds=guardrails.elapsed_seconds)

    except Exception as e:
        session.status = SessionStatus.FAILED
        session.error_message = str(e)
        session.analysis += f"\n\n❌ 处理异常: {e}"
        yield LoopFinished(status="error", reason=str(e),
                           total_rounds=guardrails.current_round,
                           total_tokens=guardrails.total_tokens,
                           elapsed_seconds=guardrails.elapsed_seconds)
```

### 3.4 兼容包装器

```python
# src/zd_agent/runtime/tool_loop.py（改造后，保留原有函数签名）

async def run_tool_loop(
    session: AgentSession,
    llm: LLMClient,
    registry: ToolRegistry,
    guardrails: Guardrails,
) -> None:
    """向后兼容入口：消费 agent_loop 事件 + 保持现有 Rich 日志输出

    runner.py 中 `await run_tool_loop(...)` 不需要任何改动。
    Rich console 输出逻辑保留在此函数中（不拆独立模块）。
    """
    provider = llm._provider
    message_store = MessageStore(provider)
    message_store.raw = session.messages

    tool_schemas = [
        ToolSchema(name=t.name, description=t.description, parameters=t.parameters)
        for t in registry.list_tools()
    ]

    async for event in agent_loop(session, provider, message_store, registry, guardrails, tool_schemas):
        # 保持现有的 Rich 日志输出风格
        match event:
            case LoopProgress():
                _console.print(
                    f"\n[bold]📍 Round {event.round}[/bold]  "
                    f"messages={event.message_count}  tokens={event.token_count:,}  "
                    f"LLM {event.llm_elapsed:.1f}s  tools={event.tool_call_count}"
                )
            case ToolExecuted() if event.success:
                _console.print(f"  [green]✓ {event.tool_name}[/green]  {event.elapsed:.1f}s")
            case ToolExecuted():
                _console.print(f"  [red]✗ {event.tool_name}[/red]  {event.error}")
            case LoopFinished(status="completed"):
                _console.print(f"\n[bold green]✅ 完成[/bold green]  rounds={event.total_rounds}  tokens={event.total_tokens:,}")
            case LoopFinished(status="guardrail"):
                _console.print(f"\n[bold yellow]⚠️ 护栏: {event.reason}[/bold yellow]")
            case LoopFinished(status="error"):
                _console.print(f"\n[bold red]❌ 异常: {event.reason}[/bold red]")

    session.messages = message_store.raw
```

### 3.5 设计取舍说明

| 不做 | 原因 |
|------|------|
| 独立的 `ConsoleReporter` 类 | 没有多个消费者的需求，日志输出留在兼容包装器即可 |
| `LLMRequestEvent` / `CompactionEvent` 等细粒度事件 | 后台 Agent 不需要这些中间状态；日志用 structlog 记录即可 |
| 流式 yield thinking/content | 没有前端消费；最终输出是 Zendesk API 调用 |
| 暂停/恢复机制 | 工单处理是一次性任务 |

**核心收益**：Subagent (Phase 6) 直接调用 `agent_loop()` 传入不同的 session + registry + message_store，
无需重复实现循环逻辑。兼容包装器消费事件做日志，subagent 执行器消费事件提取最终结论。

---

## 4. Phase 3 — Prompt 系统升级（分段注册 + 动态上下文）

### 4.1 设计目标

- 保持 SOUL.md / AGENTS.md / TOOLS.md 文件不变（向后兼容）
- 增加分段注册机制，支持条件组装
- 增加动态上下文注入（日期、版本、工单类型、策略等）
- 支持不同 LLM provider 的 prompt 微调

### 4.2 PromptSection 与 PromptBuilder

```python
# src/zd_agent/runtime/prompt/section.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Any

@dataclass
class PromptSection:
    """可条件加载的 prompt 段"""
    name: str
    content: str | Callable[..., str]  # 静态文本或动态生成器
    order: int = 100                    # 排序权重，越小越靠前
    condition: Callable[..., bool] | None = None  # 为 None 则始终包含
    wrap_tag: str | None = None         # 包裹标签，如 "<tools>" ... "</tools>"

@dataclass
class PromptContext:
    """传递给动态 section 的运行时上下文"""
    provider: str = "openai"        # LLM 厂商
    model: str = ""                 # 模型名
    ticket_type: str = ""           # 工单分类
    strategy_content: str = ""      # 策略文件内容
    customer_language: str = ""     # 客户语言
    current_date: str = ""          # 当前日期
    extra: dict[str, Any] = field(default_factory=dict)
```

```python
# src/zd_agent/runtime/prompt/builder.py

class PromptBuilder:
    """分段注册 + 条件组装的 prompt 构建器"""

    def __init__(self):
        self._sections: list[PromptSection] = []

    def register(self, section: PromptSection) -> None:
        self._sections.append(section)

    def build(self, ctx: PromptContext) -> str:
        """按条件过滤、排序、拼接所有 section"""
        active = []
        for s in self._sections:
            if s.condition is not None and not s.condition(ctx):
                continue
            content = s.content(ctx) if callable(s.content) else s.content
            if not content.strip():
                continue
            if s.wrap_tag:
                content = f"<{s.wrap_tag}>\n{content}\n</{s.wrap_tag}>"
            active.append((s.order, s.name, content))

        active.sort(key=lambda x: x[0])
        return "\n\n---\n\n".join(item[2] for item in active)
```

### 4.3 默认 Section 注册

```python
# src/zd_agent/runtime/prompt/defaults.py

def register_default_sections(builder: PromptBuilder) -> None:
    """注册默认的 prompt 段，等价于当前 build_system_prompt() 的行为"""

    # 静态段：直接加载 .md 文件
    builder.register(PromptSection(
        name="soul",
        content=load_prompt("SOUL.md"),
        order=10,
    ))
    builder.register(PromptSection(
        name="agents",
        content=load_prompt("AGENTS.md"),
        order=20,
    ))
    builder.register(PromptSection(
        name="tools",
        content=load_prompt("TOOLS.md"),
        order=30,
    ))

    # 动态段：策略注入（仅当已分类时）
    builder.register(PromptSection(
        name="strategy",
        content=lambda ctx: f"## 当前工单处理策略\n\n{ctx.strategy_content}",
        order=25,
        condition=lambda ctx: bool(ctx.strategy_content),
    ))

    # 动态段：当前日期
    builder.register(PromptSection(
        name="date_context",
        content=lambda ctx: f"当前日期: {ctx.current_date}",
        order=5,
    ))

    # 动态段：Anthropic 特有指令微调
    builder.register(PromptSection(
        name="anthropic_hint",
        content="当使用工具时，请在 thinking 中先解释你的推理过程。",
        order=35,
        condition=lambda ctx: ctx.provider == "anthropic",
    ))
```

### 4.4 向后兼容

```python
# src/zd_agent/runtime/prompt_loader.py（改造后）

def build_system_prompt(**kwargs) -> str:
    """向后兼容入口：无参数时行为与旧版完全一致"""
    builder = PromptBuilder()
    register_default_sections(builder)

    ctx = PromptContext(
        current_date=datetime.now().strftime("%Y-%m-%d"),
        **kwargs,
    )
    return builder.build(ctx)
```

`runner.py` 中的 `build_system_prompt()` 调用不需要改动。只有想要注入额外上下文时才传参数。

---

## 5. Phase 4 — Tool 系统升级（编排 + 上下文 + 预算）

### 5.1 分级编排

为每个 Tool 增加元数据标记：

```python
# src/zd_agent/tools/base.py（扩展）

class Tool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]

    # 新增属性（默认值向后兼容）
    concurrency_safe: bool = True    # 是否可并行执行
    group: str = "default"           # 工具分组
    max_output_tokens: int = 0       # 输出 token 预算（0 = 不限制）
    requires_sandbox: bool = False   # 是否需要 sandbox
    is_write_operation: bool = False # 是否为写操作

    @abstractmethod
    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        ...
```

### 5.2 工具编排器

```python
# src/zd_agent/runtime/tool_orchestrator.py

async def orchestrate_tool_calls(
    tool_calls: list[ToolCallRequest],
    registry: ToolRegistry,
    session: AgentSession,
    message_store: MessageStore,
) -> AsyncGenerator[AgentEvent, None]:
    """编排工具执行：并发安全的并行，非安全的串行

    参考 Claude Code 的 partitionToolCalls 设计。
    """
    safe_batch: list[ToolCallRequest] = []
    serial_queue: list[ToolCallRequest] = []

    for tc in tool_calls:
        tool = registry.get(tc.name)
        if tool and tool.concurrency_safe:
            safe_batch.append(tc)
        else:
            serial_queue.append(tc)

    # 并行执行安全批次
    if safe_batch:
        tasks = [_run_one(tc, registry, session) for tc in safe_batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for tc, result in zip(safe_batch, results):
            yield from _handle_result(tc, result, message_store)

    # 串行执行非安全批次
    for tc in serial_queue:
        result = await _run_one(tc, registry, session)
        yield from _handle_result(tc, result, message_store)
```

### 5.3 ToolUseContext（Phase 6 准备）

```python
# src/zd_agent/runtime/tool_context.py

@dataclass
class ToolUseContext:
    """工具执行上下文，携带 session 级别状态

    当前 Phase 作为 session 的薄包装。
    Phase 6 (Subagent) 时会扩展为支持隔离的子上下文。
    """
    session: AgentSession
    sandbox_dir: Path | None = None
    abort_signal: asyncio.Event | None = None  # 用于取消长时间运行的工具
    is_subagent: bool = False
    parent_session_id: str | None = None

    @classmethod
    def from_session(cls, session: AgentSession) -> ToolUseContext:
        return cls(session=session, sandbox_dir=session.sandbox_dir)
```

### 5.4 Tool Result Budget（输出预算）

```python
# src/zd_agent/runtime/result_budget.py

def apply_result_budget(output: str, tool: Tool, model: str = "gpt-4o") -> str:
    """对工具输出做 token 预算裁剪

    策略：
    1. 如果 tool.max_output_tokens == 0，不限制
    2. 如果输出超出预算，保留头部 + 尾部 + 中间截断提示
    """
    if tool.max_output_tokens == 0:
        return output

    token_count = _estimate_tokens(output, model)
    if token_count <= tool.max_output_tokens:
        return output

    # 头尾各保留 40% 预算，中间截断
    budget = tool.max_output_tokens
    head_budget = int(budget * 0.4)
    tail_budget = int(budget * 0.4)

    head = _truncate_to_tokens(output, head_budget, from_start=True)
    tail = _truncate_to_tokens(output, tail_budget, from_start=False)

    return f"{head}\n\n... [输出过长，已截断 {token_count - budget} tokens] ...\n\n{tail}"
```

### 5.5 工具标记示例

```python
# 需要逐个标记现有工具，以下是几个关键例子

class ZendeskInternalReply(Tool):
    name = "zendesk_internal_reply"
    concurrency_safe = False      # 写操作，不可并行
    is_write_operation = True
    group = "zendesk"

class GitGrep(Tool):
    name = "git_grep"
    concurrency_safe = True       # 只读，可并行
    max_output_tokens = 4000      # 搜索结果可能很长，限制输出
    group = "source_code"

class SandboxCreateWorktree(Tool):
    name = "sandbox_create_worktree"
    concurrency_safe = False      # 文件系统操作，串行更安全
    requires_sandbox = True
    group = "sandbox"
```

---

## 6. Phase 5 — 上下文压缩升级（多策略分层）

### 6.1 设计目标

- 将压缩从单一策略升级为三级分层
- 引入"重要消息锚点"防止关键信息被压缩
- 减少不必要的 LLM 摘要调用

### 6.2 压缩层级

```
Level 0: Tool Result Budget（Phase 4 已实现）
    ↓ 仍然超出阈值
Level 1: History Snip — 丢弃中间轮次的工具输出细节
    ↓ 仍然超出阈值
Level 2: LLM Summary — 当前已有的摘要压缩
```

### 6.3 消息锚点

```python
# src/zd_agent/runtime/compaction.py（扩展）

ANCHOR_ROLES = {"system"}
ANCHOR_TOOL_NAMES = {
    "zendesk_get_ticket",     # 工单原始信息
    "classify_ticket",        # 分类结果
    "zendesk_internal_reply", # 已发送的回复
}

def is_anchor_message(msg: dict[str, Any]) -> bool:
    """判断消息是否为锚点（不可被压缩）"""
    if msg.get("role") in ANCHOR_ROLES:
        return True
    if msg.get("role") == "tool" and msg.get("name") in ANCHOR_TOOL_NAMES:
        return True
    return False
```

### 6.4 History Snip（Level 1）

```python
async def history_snip(messages: list[dict[str, Any]], budget_tokens: int) -> list[dict[str, Any]]:
    """Level 1 压缩：缩减非锚点工具结果的内容长度

    对于非锚点的 tool 消息，将其 content 裁剪为前 200 字符 + "[已精简]"。
    对于非锚点的 assistant 消息中的 thinking/reasoning，移除。
    """
    result = []
    for msg in messages:
        if is_anchor_message(msg):
            result.append(msg)
            continue

        if msg.get("role") == "tool" and msg.get("name") not in ANCHOR_TOOL_NAMES:
            content = msg.get("content", "")
            if len(content) > 300:
                snipped = dict(msg)
                snipped["content"] = content[:200] + "\n\n[... 输出已精简 ...]"
                result.append(snipped)
                continue

        result.append(msg)

    return result
```

### 6.5 多级压缩调度器

```python
async def multi_level_compact(
    messages: list[dict[str, Any]],
    provider: LLMProvider,
    target_tokens: int,
) -> list[dict[str, Any]]:
    """多级压缩调度器

    依次尝试 Level 1 → Level 2，直到 token 数降到目标以下。
    """
    current = messages
    current_tokens = count_tokens(current)

    if current_tokens <= target_tokens:
        return current

    # Level 1: History Snip
    current = await history_snip(current, target_tokens)
    current_tokens = count_tokens(current)
    if current_tokens <= target_tokens:
        logger.info("compaction_level1_sufficient", tokens=current_tokens)
        return current

    # Level 2: LLM Summary（现有逻辑，增加锚点保护）
    current = await compact_messages_with_anchors(current, provider)
    logger.info("compaction_level2_done", tokens=count_tokens(current))
    return current
```

---

## 7. Phase 6 — Subagent 系统

### 7.1 设计目标

- 主 Agent 可通过工具 `run_subagent` 派发子 Agent
- 子 Agent 有独立上下文（独立的 message 列表）
- 子 Agent 使用主 Agent 的 tool 子集
- 子 Agent 完成后返回摘要给主 Agent

### 7.2 Subagent 工具定义

```python
# src/zd_agent/tools/subagent.py

class RunSubagent(Tool):
    name = "run_subagent"
    description = (
        "派发一个子 Agent 执行独立分析任务。子 Agent 有独立上下文，"
        "不会污染主对话。适合用于：独立的源码分析、特定仓库的深度搜索、"
        "跨版本对比等需要大量工具调用但结论可摘要的任务。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "子 Agent 需要完成的任务描述",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "允许子 Agent 使用的工具名称列表。留空则使用默认子集。",
            },
            "context": {
                "type": "string",
                "description": "传递给子 Agent 的背景信息（如当前分析进展、版本号等）",
            },
        },
        "required": ["task"],
    }

    concurrency_safe = False  # 子 Agent 消耗资源较多，串行执行
    group = "subagent"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        task = args["task"]
        tool_names = args.get("tools", [])
        context = args.get("context", "")
        return await run_subagent(task, tool_names, context, session)
```

### 7.3 Subagent 执行器

```python
# src/zd_agent/runtime/subagent.py

SUBAGENT_SYSTEM_PROMPT = """你是一个专注于特定分析任务的子 Agent。

你的任务是完成分配给你的分析工作，然后返回一份简洁的结论摘要。

规则：
- 专注于分配的任务，不要发散
- 完成后直接输出结论，不需要调用 zendesk_internal_reply
- 结论中包含关键发现、证据引用、和你的判断
- 控制在 1000 字以内
"""

SUBAGENT_DEFAULT_TOOLS = [
    "git_show", "git_grep", "git_log", "git_diff",
    "list_repos", "list_repo_tags",
    "sandbox_create_worktree", "sandbox_list", "sandbox_search", "sandbox_read",
    "knowledge_search", "knowledge_read",
    "official_docs_search", "official_docs_read",
    "helm_list_versions", "helm_get_version_details",
]

SUBAGENT_GUARDRAILS = GuardrailsConfig(
    max_rounds=15,           # 子 Agent 轮次更少
    timeout_seconds=180,     # 3 分钟超时
    max_tokens_per_session=200_000,
    compaction_threshold=0.8,
)


async def run_subagent(
    task: str,
    tool_names: list[str],
    context: str,
    parent_session: AgentSession,
) -> ToolResult:
    """执行子 Agent：独立上下文 + 有限工具集 + 精简护栏"""

    # 创建子 session
    sub_session = AgentSession(
        ticket_id=parent_session.ticket_id,
        sandbox_dir=parent_session.sandbox_dir,  # 共享 sandbox
    )

    # 构建子 Agent 的工具注册中心（仅包含允许的子集）
    full_registry = create_tool_registry()
    sub_registry = ToolRegistry()
    allowed = set(tool_names) if tool_names else set(SUBAGENT_DEFAULT_TOOLS)
    for tool in full_registry.list_tools():
        if tool.name in allowed:
            sub_registry.register(tool)

    # 构建消息
    cfg = get_config()
    env = EnvSettings()
    provider = create_llm_provider(cfg.llm, env)
    message_store = MessageStore(provider)

    system_prompt = SUBAGENT_SYSTEM_PROMPT
    if context:
        system_prompt += f"\n\n## 背景信息\n\n{context}"

    message_store.append_system(system_prompt)
    message_store.append_user(f"请完成以下分析任务：\n\n{task}")

    # 执行子 Agent 循环
    guardrails = Guardrails(config=SUBAGENT_GUARDRAILS)
    tool_schemas = [
        ToolSchema(name=t.name, description=t.description, parameters=t.parameters)
        for t in sub_registry.list_tools()
    ]

    final_content = ""
    async for event in agent_loop(sub_session, provider, message_store, sub_registry, guardrails, tool_schemas):
        if isinstance(event, CompleteEvent):
            # 提取最后一条 assistant 消息作为结论
            for msg in reversed(message_store.raw):
                if msg.get("role") == "assistant" and msg.get("content"):
                    final_content = msg["content"]
                    break
        elif isinstance(event, (GuardrailEvent, ErrorEvent)):
            return ToolResult(
                success=False,
                error=f"子 Agent 异常: {getattr(event, 'reason', None) or getattr(event, 'error', '')}",
            )

    # 将子 Agent 的 token 消耗计入父 session
    parent_session.token_usage.prompt_tokens += sub_session.token_usage.prompt_tokens
    parent_session.token_usage.completion_tokens += sub_session.token_usage.completion_tokens

    return ToolResult(
        success=True,
        output=f"[子 Agent 分析结论]\n\n{final_content}\n\n"
               f"(子 Agent 用了 {sub_session.round_count} 轮, "
               f"{sub_session.token_usage.total_tokens:,} tokens)",
    )
```

### 7.4 Subagent 使用场景举例

在 AGENTS.md 中可以增加指导（未来的 prompt 升级）：

```markdown
### 何时使用 run_subagent

当以下场景出现时，考虑使用 run_subagent 而非自己直接分析：

1. 需要在某个仓库中做深度源码追踪（>5 次 git/sandbox 工具调用）
2. 需要跨多个版本对比同一文件的变更
3. 分析中途发现需要调查一个独立的子问题
4. 当前上下文已经较满，新的分析路径可能导致关键信息被压缩

使用时提供清晰的任务描述和必要的背景信息（版本号、文件路径、已有发现等）。
```

---

## 8. 向后兼容策略

### 8.1 配置兼容

| 变更 | 兼容方式 |
|------|---------|
| `config.yaml` 新增 `llm.provider` | 已存在，默认 `"openai"`，旧配置无需改动 |
| `.env` 新增 `ANTHROPIC_API_KEY` | 只在 `provider=anthropic` 时需要 |
| Tool 新增属性 | 全部有默认值，不影响现有 Tool 子类 |
| `PromptBuilder` | `build_system_prompt()` 无参数调用时行为完全一致 |

### 8.2 接口兼容

| 模块 | 对外接口 | 兼容策略 |
|------|---------|---------|
| `LLMClient` | `chat(messages, tools)` → `LLMResponse` | 保留原有类作为 facade，内部委托给 provider |
| `run_tool_loop` | `async def run_tool_loop(session, llm, registry, guardrails) -> None` | 保留函数签名，内部改为消费 `agent_loop` |
| `run_agent` | `async def run_agent(session) -> None` | 不需要改动 |
| `ToolRegistry` | `execute(name, args_json, session)` | 不变 |
| `build_system_prompt` | `def build_system_prompt() -> str` | 无参调用时行为不变 |

### 8.3 数据格式兼容

消息格式（`session.messages`）在内部使用 `MessageStore` 管理，但 `session.messages` 属性仍然返回 `list[dict]`，确保归档、日志等下游代码不受影响。

---

## 9. 目录结构变更

```
src/zd_agent/
├── runtime/
│   ├── agent_loop.py          # 🆕 AsyncGenerator 主循环（精简版，4 个事件类型）
│   ├── events.py              # 🆕 精简事件类型（LoopProgress / ToolExecuted / LoopFinished）
│   ├── tool_loop.py           # 📝 改为 agent_loop 的兼容包装器（保留 Rich 输出）
│   ├── runner.py              # 📝 微调：使用新的 LLMClient
│   ├── tool_orchestrator.py   # 🆕 工具编排器
│   ├── tool_context.py        # 🆕 ToolUseContext
│   ├── result_budget.py       # 🆕 工具输出预算
│   ├── subagent.py            # 🆕 子 Agent 执行器
│   ├── messages.py            # 🆕 MessageStore
│   ├── compaction.py          # 📝 扩展：多级压缩 + 锚点
│   ├── guardrails.py          # 不变
│   ├── retry.py               # 不变
│   ├── prompt_loader.py       # 📝 改为委托给 PromptBuilder
│   ├── source_selector.py     # 不变
│   ├── archive_meta.py        # 不变
│   ├── llm_client.py          # 📝 改为 facade
│   ├── llm/                   # 🆕 LLM 抽象层
│   │   ├── __init__.py
│   │   ├── types.py           # LLMProvider ABC, LLMResponse, ToolCallRequest, ToolSchema
│   │   ├── openai_provider.py
│   │   ├── anthropic_provider.py
│   │   └── factory.py
│   └── prompt/                # 🆕 Prompt 构建系统
│       ├── __init__.py
│       ├── section.py         # PromptSection, PromptContext
│       ├── builder.py         # PromptBuilder
│       └── defaults.py        # 默认 section 注册
├── tools/
│   ├── base.py                # 📝 Tool 增加元数据属性
│   ├── registry.py            # 不变
│   ├── subagent.py            # 🆕 RunSubagent 工具
│   └── ... (其他工具文件不变，逐步补充元数据标记)
└── ... (其他模块不变)
```

图例：🆕 = 新文件，📝 = 修改现有文件

---

## 10. 实施路线图

### Phase 1：LLM 抽象层（预计 2-3 天）

- [ ] 创建 `runtime/llm/types.py`（LLMProvider, LLMResponse, ToolCallRequest, ToolSchema）
- [ ] 实现 `runtime/llm/openai_provider.py`
- [ ] 实现 `runtime/llm/anthropic_provider.py`
- [ ] 实现 `runtime/llm/factory.py`
- [ ] 改造 `runtime/llm_client.py` 为 facade
- [ ] 扩展 `config.py` 增加 `anthropic_api_key`
- [ ] 创建 `runtime/messages.py`（MessageStore）
- [ ] 编写测试：两个 provider 的 schema 转换、消息格式转换
- [ ] 验证：现有 OpenAI 配置无需改动即可运行

### Phase 2：Runtime 主循环（预计 1 天）

- [ ] 创建 `runtime/events.py`（4 个精简事件类型）
- [ ] 创建 `runtime/agent_loop.py`（AsyncGenerator 主循环，逻辑从 tool_loop.py 迁移）
- [ ] 改造 `runtime/tool_loop.py` 为兼容包装器（消费事件 + 保持 Rich 输出）
- [ ] 验证：`runner.py` 中的 `run_tool_loop` 调用不变，行为一致

### Phase 3：Prompt 系统（预计 1-2 天）

- [ ] 创建 `runtime/prompt/section.py`
- [ ] 创建 `runtime/prompt/builder.py`
- [ ] 创建 `runtime/prompt/defaults.py`
- [ ] 改造 `runtime/prompt_loader.py` 为委托入口
- [ ] 验证：`build_system_prompt()` 无参调用时输出与旧版一致

### Phase 4：Tool 系统（预计 2-3 天）

- [ ] 扩展 `tools/base.py`（Tool 增加元数据）
- [ ] 创建 `runtime/tool_orchestrator.py`
- [ ] 创建 `runtime/tool_context.py`
- [ ] 创建 `runtime/result_budget.py`
- [ ] 为所有现有工具补充元数据标记（concurrency_safe, group, max_output_tokens）
- [ ] 将 `agent_loop` 中的工具执行改为使用 orchestrator

### Phase 5：上下文压缩（预计 1-2 天）

- [ ] 扩展 `runtime/compaction.py`（锚点机制 + history snip）
- [ ] 实现多级压缩调度器
- [ ] 将 `agent_loop` 中的压缩调用改为使用新调度器

### Phase 6：Subagent（预计 2-3 天）

- [ ] 创建 `tools/subagent.py`（RunSubagent 工具）
- [ ] 创建 `runtime/subagent.py`（子 Agent 执行器）
- [ ] 在 `registry.py` 中注册 RunSubagent
- [ ] 在 AGENTS.md 中增加 subagent 使用指导
- [ ] 编写集成测试

### 总计预估：9-14 天（按增量迭代，每个 Phase 可独立上线）

---

## 附录 A：Claude Code 架构模式对照

| Claude Code 模式 | 本蓝图对应 | 章节 |
|------------------|-----------|------|
| `async function* queryLoop` | `async def agent_loop` (精简 AsyncGenerator，4 个事件) | Phase 2 |
| `getSystemPrompt()` 分层组装 | `PromptBuilder` + `PromptSection` | Phase 3 |
| `Tool` interface + `ToolUseContext` | `Tool` ABC 扩展 + `ToolUseContext` | Phase 4 |
| `runTools` 并发安全分区 | `orchestrate_tool_calls` | Phase 4 |
| Tool Result Budget | `apply_result_budget` | Phase 4 |
| History Snip + Autocompact | `history_snip` + `multi_level_compact` | Phase 5 |
| `runAgent` subagent + sidechain | `run_subagent` + 独立 MessageStore | Phase 6 |
| `appendSystemContext` / `prependUserContext` | `MessageStore` 上下文注入 | Phase 2 |
| `canUseTool` 权限检查 | Tool 元数据 + orchestrator 过滤 | Phase 4 |
