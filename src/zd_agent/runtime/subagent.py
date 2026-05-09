"""子 Agent 执行器：独立上下文 + 有限工具集 + 精简护栏。"""

from __future__ import annotations

from typing import Any

from zd_agent.config import EnvSettings, get_config
from zd_agent.log import get_logger
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.events import LoopFinished, ToolExecuted
from zd_agent.runtime.guardrails import Guardrails, GuardrailsConfig
from zd_agent.runtime.llm.factory import create_llm_provider
from zd_agent.runtime.llm.types import ToolSchema
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import ToolRegistry, ToolResult
from zd_agent.tools.registry import create_tool_registry

logger = get_logger(module="subagent")

SUBAGENT_SYSTEM_PROMPT = """你是一个专注于特定分析任务的子 Agent。

你的任务是完成分配给你的分析工作，然后返回一份简洁的结论摘要。

# 通用规则

- 专注于分配的任务，不要发散
- 完成后直接输出结论，不需要调用 zendesk_internal_reply
- 结论中包含关键发现、证据引用、和你的判断
- 控制在 1000 字以内

# 数据/日志/Trace 类分析任务的统一 Quality Bar

如果你的任务是查 SigNoz / 数据库 / Grafana 等数据源（通常 task 描述里出现
"日志"/"trace"/"指标"/"统计"/"用量"/"分析"），下述要求自动适用，不需要 caller
重复列出。**只有 task 明确说"只看一眼"或"快速确认"时才能跳过**：

1. **统计量必给**：count + p50 + p90 + max（不只是 list 出 raw 样本）
2. **基线对比必做**：找一个"正常时段 / 正常用户 / 正常 endpoint"作为参考样本，
   把异常侧的数字对照展示。没有基线 = 数据没意义
3. **维度回退**：如果 tenant_id / workspace_id 维度查不到数据，按
   service.name / endpoint / status_code 重新聚合一次再下结论"无数据"
4. **时间窗口**：默认看最近 7 天（trace 看 1-3 天）；窗口太短查不到时主动
   扩大到 30 天再确认
5. **结论分层**：先给 1 句话定性（异常/正常/不确定），再给数据，再给
   下一步建议——不要把数字埋在中间

# 源码 / 仓库 / 知识库类任务

- 不要只看一个文件就下结论；至少 grep 一遍上下文
- 引用具体行号 / commit / 文件路径
- 找不到时先尝试同义词（中英、API 名 / 内部 wording）再说"无相关代码"
"""

SUBAGENT_DEFAULT_TOOLS = [
    "git_show", "git_grep", "git_log", "git_diff",
    "list_repos", "list_repo_tags",
    "sandbox_list", "sandbox_search", "sandbox_read",
    "knowledge_search", "knowledge_read",
    "official_docs_search", "official_docs_read",
]

SUBAGENT_GUARDRAILS = GuardrailsConfig(
    max_rounds=15,
    timeout_seconds=180,
    max_tokens_per_session=200_000,
    compaction_threshold=0.8,
)


async def run_subagent(
    task: str,
    tool_names: list[str],
    context: str,
    parent_session: AgentSession,
) -> ToolResult:
    """执行子 Agent：独立上下文 + 有限工具集 + 精简护栏。"""
    cfg = get_config()
    env = EnvSettings()
    provider = create_llm_provider(cfg.llm, env)

    sub_session = AgentSession(
        ticket_id=parent_session.ticket_id,
        sandbox_dir=parent_session.sandbox_dir,
    )

    # 构建子 Agent 可用的工具子集
    full_registry = create_tool_registry()
    sub_registry = ToolRegistry()
    allowed = set(tool_names) if tool_names else set(SUBAGENT_DEFAULT_TOOLS)
    for tool in full_registry.list_tools():
        if tool.name in allowed:
            sub_registry.register(tool)

    # 初始化独立的消息存储
    message_store = MessageStore(provider)
    system_prompt = SUBAGENT_SYSTEM_PROMPT
    if context:
        system_prompt += f"\n\n## 背景信息\n\n{context}"
    message_store.append_system(system_prompt)
    message_store.append_user(f"请完成以下分析任务：\n\n{task}")

    guardrails = Guardrails(config=SUBAGENT_GUARDRAILS)
    tool_schemas = [
        ToolSchema(name=t.name, description=t.description, parameters=t.parameters)
        for t in sub_registry.list_tools()
    ]

    logger.info(
        "subagent_start",
        ticket_id=parent_session.ticket_id,
        task_preview=task[:200],
        tool_count=len(tool_schemas),
    )

    final_content = ""
    async for event in agent_loop(
        sub_session, provider, message_store, sub_registry, guardrails, tool_schemas,
    ):
        if isinstance(event, LoopFinished):
            if event.status != "completed":
                logger.warning(
                    "subagent_abnormal",
                    ticket_id=parent_session.ticket_id,
                    status=event.status,
                    reason=event.reason,
                )
                return ToolResult(
                    success=False,
                    error=f"子 Agent 异常终止: {event.reason}",
                )
            # 从消息历史尾部提取最终结论
            for msg in reversed(message_store.raw):
                if msg.get("role") == "assistant" and msg.get("content"):
                    content = msg["content"]
                    if isinstance(content, str):
                        final_content = content
                        break

    # 累计 token 消耗到父会话
    parent_session.token_usage.prompt_tokens += sub_session.token_usage.prompt_tokens
    parent_session.token_usage.completion_tokens += sub_session.token_usage.completion_tokens

    logger.info(
        "subagent_done",
        ticket_id=parent_session.ticket_id,
        rounds=sub_session.round_count,
        tokens=sub_session.token_usage.total_tokens,
    )

    return ToolResult(
        success=True,
        output=(
            f"[子 Agent 分析结论]\n\n{final_content}\n\n"
            f"(子 Agent 用了 {sub_session.round_count} 轮, "
            f"{sub_session.token_usage.total_tokens:,} tokens)"
        ),
    )
