"""子 Agent 执行器：独立上下文 + 有限工具集 + 精简护栏。"""

from __future__ import annotations

from typing import Any

from zd_agent.config import EnvSettings, get_config
from zd_agent.log import get_logger
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.events import LoopFinished, LoopProgress, ToolExecuted
from zd_agent.runtime.guardrails import Guardrails, GuardrailsConfig
from zd_agent.runtime.llm.factory import create_llm_provider
from zd_agent.runtime.llm.types import ToolSchema
from zd_agent.runtime.log_reporter import LogReporter
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import ToolRegistry, ToolResult
from zd_agent.tools.registry import create_tool_registry

logger = get_logger(module="subagent")

SUBAGENT_SYSTEM_PROMPT = """你是一个专注于特定分析任务的子 Agent。

你的任务是完成分配给你的分析工作，然后返回一份简洁的结论摘要。

# 🚨 自律约束（最重要 — 违反会被 guardrail 强制截断）

- **轮次预算 ≤ 6 轮**：你只有 6 轮 LLM 调用机会。从第 3 轮起评估"再多查能加多少价值"，
  不能 → 立即出结论 return。
- **tool 调用预算 ≤ 8 次**：跨所有轮加起来 ≤ 8 次 tool 调用。每多查一次问自己"上一次
  返回没让我接近结论吗？没有 → 停"。
- **找不到结果就老实说 not_found**：不要无限重 query 换关键词换 filter。第 1-2 次
  查不到就回 "Searched X / Y / Z (synonyms tried) — 0 hit, not in scope" — 然后 return。
- **不要 raw dump**：tool 返回的大段 JSON / log → 在你回复里只引用关键 1-2 条 +
  统计数字，不要复制原文。父 agent 拿到 raw dump 没用。
- **第一句必须是结论**：你的最终回复第一句话就是结论（"找到 / 没找到 / 部分找到"），
  后面再展开证据。让父 agent 一眼看完就能往下走。

# 通用规则

- 专注于分配的任务，不要发散
- 完成后直接输出结论，不需要调用 zendesk_internal_reply
- 结论中包含关键发现、证据引用、和你的判断
- 只有成功工具调用返回的实际结果是证据；参数、失败结果、错误文本、分类输出和历史工单不能证明当前结论
- 严格区分 symptom / failing_boundary / immediate_trigger / root_cause / contributing_factor / recovery_event / current_state；相关性、失败边界和恢复事件不等于根因
- 控制在 1000 字以内
- 最终输出必须使用下面 5 个字段，方便父 agent 直接汇总，不重复查同一证据源：
  - Conclusion: 一句话结论（找到 / 没找到 / 部分找到）
  - Checked: 你实际查过的工具、关键词、时间窗、路径或表
  - Evidence: 最关键 1-3 条证据，带路径/行号/SQL 摘要/日志条件
  - Gaps: 仍缺什么；没有缺口就写 none
  - Parent next action: 建议父 agent 直接回复、补查哪个具体事实，或转人工

# 数据/日志/Trace 类分析任务的统一 Quality Bar

如果你的任务是查 SigNoz / 数据库等数据源（通常 task 描述里出现
"日志"/"trace"/"指标"/"统计"/"用量"/"分析"），下述要求自动适用，不需要 caller
重复列出。**只有 task 明确说"只看一眼"或"快速确认"时才能跳过**：

1. **统计量匹配问题**：延迟类给 count + p50 + p90 + max；错误类给 count /
   error rate / status 或 signature 分布；状态类给对象数、状态分布和等待时长。
   不要为了填模板计算与问题无关的分位数
2. **基线对比**：间歇故障、延迟和容量问题必须找正常时段 / 正常对象 /
   正常 endpoint 作为参考；单一确定状态查询可以不强造基线
3. **维度回退**：如果 tenant_id / workspace_id 维度查不到数据，按
   service.name / endpoint / status_code 重新聚合一次再下结论"无数据"
4. **时间窗口**：默认看最近 7 天（trace 看 1-3 天）；窗口太短查不到时主动
   扩大到 30 天再确认
5. **结论分层**：先给 1 句话定性（异常/正常/不确定），再给数据，再给
   下一步建议。只确认失败边界时就写失败边界；只有完整因果链和替代解释
   核查完成后才能写 root cause confirmed

# 源码 / 仓库 / 知识库类任务

- 不要只看一个文件就下结论；至少 grep 一遍上下文
- 引用具体行号 / commit / 文件路径
- 找不到时先尝试同义词（中英、API 名 / 内部 wording）再说"无相关代码"
"""

# Subagent 返回硬截断长度（字符数，约 ≈ 2K tokens）：父 loop 收到的最大长度。
# 之前 subagent final_content 不截断，复杂工单 4-6 个 subagent 各返回 20-50K
# 字符 → 主 loop history 暴涨 → 触发 max_tokens guardrail 强制总结。
# 4000 字符≈ 1K-2K tokens，足够装结论 + 关键证据；细节可以让 agent 主 loop
# 看 task description / context 自己补查。
_MAX_SUBAGENT_OUTPUT_CHARS = 4000


def _export_evidence_tool_calls(session: AgentSession) -> list[dict[str, Any]]:
    """Namespace child call IDs so the parent can cite them without collisions."""
    exported: list[dict[str, Any]] = []
    for index, item in enumerate(session.tool_call_log, start=1):
        if not isinstance(item, dict):
            continue
        raw_call_id = str(item.get("call_id") or f"call-{index}").strip()
        exported.append(
            {
                "call_id": f"{session.session_id}:{raw_call_id}",
                "tool": str(item.get("tool") or ""),
                "args": dict(item.get("args") or {}),
                "success": item.get("success") is True,
                "error": item.get("error"),
                "result_preview": str(item.get("result_preview") or ""),
            }
        )
    return exported


def _truncate_subagent_output(text: str) -> tuple[str, bool]:
    """把 subagent 输出截断到 _MAX_SUBAGENT_OUTPUT_CHARS。

    返回 (text, truncated) — truncated 为 True 时表示被截了。
    截断策略：保留前 90% 字符 + 末尾"...(已截断)"提示。保留前段而非
    后段因为子 agent 的结论 / 证据通常在开头总结。
    """
    if len(text) <= _MAX_SUBAGENT_OUTPUT_CHARS:
        return text, False
    keep = int(_MAX_SUBAGENT_OUTPUT_CHARS * 0.9)
    return (
        text[:keep]
        + f"\n\n[...subagent 输出已截断，原 {len(text)} 字符 → "
          f"{keep} 字符。若需细节请重新派一个更具体的 subagent 任务]"
    ), True


SUBAGENT_DEFAULT_TOOLS = [
    "git_show", "git_grep", "git_log", "git_diff",
    "list_repos", "list_repo_tags",
    "sandbox_list", "sandbox_search", "sandbox_read",
    "knowledge_search", "knowledge_read",
    "official_docs_search", "official_docs_read",
]

SUBAGENT_GUARDRAILS = GuardrailsConfig(
    # 15 → 6: 观察发现 subagent 经常跑 9-13 轮反复查同样数据。强制 ≤ 6 轮
    # 让 subagent 早出结论，没拿到就老实回 not_found 不要无限挖。
    max_rounds=6,
    timeout_seconds=180,
    # 200K → 600K: 一个复杂 subagent (SigNoz raw search + multi-tool) 一轮
    # 就 30-50K，6 轮可能 300K+。给 600K 留缓冲，加 compaction 双重保险，
    # 不再频繁 forced_summary on token。
    max_tokens_per_session=600_000,
    compaction_threshold=0.5,
)


async def run_subagent(
    task: str,
    tool_names: list[str],
    context: str,
    parent_session: AgentSession,
    log_metadata: dict[str, Any] | None = None,
) -> ToolResult:
    """执行子 Agent：独立上下文 + 有限工具集 + 精简护栏。"""
    cfg = get_config()
    env = EnvSettings()

    # 招 1: subagent 用 cfg.llm.subagent_model（如果配了）替代主模型。
    # subagent 通常做"机械抓数"，mini 模型够用，省 70%+ token 成本。
    # 空字符串 = 用主模型（向后兼容 / 未配置场景）。
    sub_llm_cfg = cfg.llm
    subagent_reasoning_effort = getattr(cfg.llm, "subagent_reasoning_effort", "")
    if cfg.llm.subagent_model or subagent_reasoning_effort:
        # 浅拷贝 + 覆盖 Subagent 独立模型参数，不动主 cfg 实例。
        sub_llm_cfg = cfg.llm.model_copy(
            update={
                "model": cfg.llm.subagent_model or cfg.llm.model,
                "reasoning_effort": subagent_reasoning_effort,
            }
        )
    if cfg.llm.subagent_model:
        logger.info(
            "subagent_using_cheap_model",
            model=cfg.llm.subagent_model,
            reasoning_effort=subagent_reasoning_effort or "model_default",
            ticket_id=parent_session.ticket_id,
        )
    provider = create_llm_provider(sub_llm_cfg, env)

    metadata = log_metadata or {}
    sibling_index = metadata.get("sibling_index")
    sub_session = AgentSession(
        ticket_id=parent_session.ticket_id,
        sandbox_dir=parent_session.sandbox_dir,
        trigger_type="subagent",
        parent_session_id=parent_session.session_id,
        subagent_batch_id=str(metadata.get("batch_id") or ""),
        subagent_index=sibling_index if isinstance(sibling_index, int) else None,
        dry_run=True,
    )

    # 构建子 Agent 可用的工具子集（招法 3）：
    # tool_names 应该由 caller 通过 run_subagents_parallel 的 task.tools 字段
    # 强制指定。如果 caller 没传（向后兼容旧调用），fallback 到 DEFAULT_TOOLS
    # 但会 warn，因为加载所有 default 多花 5-10K token / subagent。
    full_registry = create_tool_registry()
    sub_registry = ToolRegistry()
    if tool_names:
        allowed = set(tool_names)
    else:
        allowed = set(SUBAGENT_DEFAULT_TOOLS)
        logger.warning(
            "subagent_no_tool_subset_fallback_default",
            ticket_id=parent_session.ticket_id,
            default_count=len(SUBAGENT_DEFAULT_TOOLS),
            note="caller 未通过 task.tools 指定 → 全 default 加载，会多用 5-10K token",
        )
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
        session_id=sub_session.session_id,
        parent_session_id=parent_session.session_id,
        batch_id=sub_session.subagent_batch_id,
        sibling_index=sub_session.subagent_index,
        task_preview=task[:200],
        tool_count=len(tool_schemas),
    )

    child_reporter: LogReporter | None = None
    child_run_id: int | None = None
    current_round_id: int | None = None
    finish_reported = False
    parent_run_id = parent_session.remote_log_run_id

    final_content = ""
    try:
        if parent_run_id is not None and log_metadata:
            child_reporter = LogReporter()
            if child_reporter.enabled:
                child_run_id = await child_reporter.report_run_start(
                    ticket_id=parent_session.ticket_id,
                    session_id=sub_session.session_id,
                    trigger_type="subagent",
                    model=sub_llm_cfg.model,
                    parent_run_id=parent_run_id,
                    task_preview=log_metadata.get("task_preview") or task[:500],
                    worker_role=log_metadata.get("worker_role"),
                    question_preview=log_metadata.get("question_preview") or task[:500],
                    batch_id=log_metadata.get("batch_id"),
                    sibling_index=log_metadata.get("sibling_index"),
                )

        async for event in agent_loop(
            sub_session,
            provider,
            message_store,
            sub_registry,
            guardrails,
            tool_schemas,
            enable_vision_input=sub_llm_cfg.enable_vision_input,
        ):
            if (
                isinstance(event, LoopProgress)
                and child_reporter is not None
                and child_run_id is not None
            ):
                current_round_id = await child_reporter.report_round(
                    child_run_id, event, sub_llm_cfg.model,
                )
            elif (
                isinstance(event, ToolExecuted)
                and child_reporter is not None
                and child_run_id is not None
                and current_round_id is not None
            ):
                await child_reporter.report_tool_call(
                    child_run_id, current_round_id, event,
                )
            elif isinstance(event, LoopFinished):
                if child_reporter is not None and child_run_id is not None:
                    await child_reporter.report_run_finish(
                        child_run_id,
                        event,
                        total_input_tokens=sub_session.token_usage.prompt_tokens,
                        total_output_tokens=sub_session.token_usage.completion_tokens,
                    )
                    finish_reported = True
                if event.status != "completed":
                    logger.warning(
                        "subagent_abnormal",
                        ticket_id=parent_session.ticket_id,
                        session_id=sub_session.session_id,
                        parent_session_id=parent_session.session_id,
                        batch_id=sub_session.subagent_batch_id,
                        sibling_index=sub_session.subagent_index,
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
    finally:
        if child_reporter is not None:
            if child_run_id is not None and not finish_reported:
                await child_reporter.report_run_finish(
                    child_run_id,
                    LoopFinished(
                        status="error",
                        total_rounds=sub_session.round_count,
                        total_tokens=sub_session.token_usage.total_tokens,
                        elapsed_seconds=guardrails.elapsed_seconds,
                        reason="subagent exited before finish event",
                    ),
                    total_input_tokens=sub_session.token_usage.prompt_tokens,
                    total_output_tokens=sub_session.token_usage.completion_tokens,
                )
            await child_reporter.close()

    # 累计 token 消耗到父会话
    parent_session.token_usage.prompt_tokens += sub_session.token_usage.prompt_tokens
    parent_session.token_usage.completion_tokens += sub_session.token_usage.completion_tokens

    # P1-4：硬截断 subagent 输出（避免父 loop history 暴涨触发 token guardrail）
    final_content_trunc, was_truncated = _truncate_subagent_output(final_content)
    evidence_tool_calls = _export_evidence_tool_calls(sub_session)
    evidence_call_lines = [
        f"- {item['call_id']} ({item['tool']})"
        for item in evidence_tool_calls
        if item["success"] and item["result_preview"]
    ]
    evidence_call_block = (
        "\n\n[Evidence call IDs for parent ledger]\n"
        + "\n".join(evidence_call_lines)
        if evidence_call_lines
        else ""
    )

    logger.info(
        "subagent_done",
        ticket_id=parent_session.ticket_id,
        session_id=sub_session.session_id,
        parent_session_id=parent_session.session_id,
        batch_id=sub_session.subagent_batch_id,
        sibling_index=sub_session.subagent_index,
        rounds=sub_session.round_count,
        tokens=sub_session.token_usage.total_tokens,
        output_chars=len(final_content),
        truncated=was_truncated,
    )

    return ToolResult(
        success=True,
        output=(
            f"[子 Agent 分析结论]\n\n{final_content_trunc}\n\n"
            f"(子 Agent 用了 {sub_session.round_count} 轮, "
            f"{sub_session.token_usage.total_tokens:,} tokens"
            f"{'，输出已截断' if was_truncated else ''})"
            f"{evidence_call_block}"
        ),
        metadata={"evidence_tool_calls": evidence_tool_calls},
    )
