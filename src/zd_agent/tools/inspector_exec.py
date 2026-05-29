"""InspectorExec tool: drill-down SaaS UUID 查询，跨 main+billing+TiDB。

Inspector 是 langgenius/infra-toolkit 提供的私有 CLI。它把高频查询路径
（账号 / 租户 / 应用 / 工作流 run / quota）打包成命令，避免手写 SQL 漏字段。

何时该用 Inspector 而不是 sql_query
-------------------------------------

- **配额/用量类**（apps / 文档 / 知识库存储）→ ``--section quota``
  手写 SQL 拿不到 TiDB 知识库存储；inspector 自动跨库取。
- **workflow run 失败定位** → ``--workflow-run-id``
  一次拿 run + 节点执行链 + 节点错误，比单独查 workflow_runs +
  workflow_node_executions 两张表全。
- **app run / app 失败排查** → ``--app-id --section runs``
- **UUID 类型未知** → ``--id``
  自动识别是 app_id / dataset_id / workflow_id 等。
- **账号/租户 drill-down** → ``--email`` / ``--account-id`` / ``--tenant-id``
  按 ``--section`` 切 ``members`` / ``apps`` / ``runs`` / ``billing`` 等。

不要用 Inspector 的场景：
- 任意自定义 SQL（多表 join / 聚合 / 表结构未覆盖）→ sql_query
- plugin_daemon 库 → sql_query

安全
----
- 只允许 ``inspector search`` 子命令（Web UI / 其它子命令禁用）
- ``--show-all`` flag 默认禁用（避免 leak Stripe customer ID 到工单回复）
- 子进程 timeout 30s
- stdin 关闭，无任意命令注入面
"""
from __future__ import annotations

import asyncio
import shlex
from typing import Any, TYPE_CHECKING

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession

logger = get_logger(module="tools.inspector")

# 主查询 flag 互斥（必须且只能传一个）
_PRIMARY_QUERY_FLAGS = {
    "email",
    "account_id",
    "tenant_id",
    "app_id",
    "dataset_id",
    "document_id",
    "conversation_id",
    "workflow_id",
    "workflow_run_id",
    "id",
}

# 允许的 section 值（按 inspector 文档）
_VALID_SECTIONS = {
    "all",
    "tenants",
    "apps",
    "runs",
    "conversations",
    "billing",
    "quota",
    "members",
}

# 允许的 query 简写（inspector 内部 enum）
_VALID_QUERIES = {"failed", "succeeded"}


def _flag_to_cli(flag: str) -> str:
    """``account_id`` → ``--account-id``"""
    return "--" + flag.replace("_", "-")


class InspectorExec(Tool):
    name = "inspector_exec"
    description = (
        "调用 Inspector CLI（langgenius/infra-toolkit）做 SaaS DB drill-down。"
        "**优先于 sql_query 使用** 的场景：quota/usage（应用数 / 文档数 / 知识库存储 / "
        "成员数）、workflow_run 失败定位（一次拿 run + 节点链）、UUID 类型未知"
        "（自动识别）、账号或租户的 billing/runs/apps section drill-down。"
        "必须传一个主查询参数（email / account_id / tenant_id / app_id / dataset_id / "
        "document_id / conversation_id / workflow_id / workflow_run_id / id），"
        "可选 section（all / tenants / apps / runs / conversations / billing / quota / members）"
        "+ query（failed / succeeded）+ limit（默认 50）+ from / to（YYYY-MM-DD）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "客户邮箱"},
            "account_id": {"type": "string", "description": "Account UUID"},
            "tenant_id": {"type": "string", "description": "Tenant/Workspace UUID"},
            "app_id": {"type": "string", "description": "App UUID"},
            "dataset_id": {"type": "string", "description": "Dataset/Knowledge base UUID"},
            "document_id": {"type": "string", "description": "Document UUID"},
            "conversation_id": {"type": "string", "description": "Conversation UUID"},
            "workflow_id": {"type": "string", "description": "Workflow UUID"},
            "workflow_run_id": {"type": "string", "description": "Workflow run UUID"},
            "id": {
                "type": "string",
                "description": "类型未知的 UUID。inspector 会自动识别它属于哪张表",
            },
            "section": {
                "type": "string",
                "enum": sorted(_VALID_SECTIONS),
                "description": "数据分区。常用：quota（配额/用量）/ billing（订阅）/ runs（执行历史）/ apps（应用列表）",
            },
            "query": {
                "type": "string",
                "enum": sorted(_VALID_QUERIES),
                "description": "结果筛选：failed（仅失败） / succeeded（仅成功）",
            },
            "limit": {
                "type": "integer",
                "description": "结果集上限，默认 50",
                "minimum": 1,
                "maximum": 500,
            },
            "from_date": {
                "type": "string",
                "description": "起始日期 YYYY-MM-DD（含）",
            },
            "to_date": {
                "type": "string",
                "description": "结束日期 YYYY-MM-DD（含）",
            },
        },
        "required": [],
    }
    group = "database_query"
    concurrency_safe = True
    max_output_tokens = 8000

    async def execute(
        self, args: dict[str, Any], session: "AgentSession",
    ) -> ToolResult:
        # 1. 主查询参数校验：必须且只能传一个
        primary = [
            f for f in _PRIMARY_QUERY_FLAGS if args.get(f)
        ]
        if len(primary) == 0:
            return ToolResult(
                success=False,
                error=(
                    "必须提供一个主查询参数。可选："
                    f"{', '.join(sorted(_PRIMARY_QUERY_FLAGS))}"
                ),
            )
        if len(primary) > 1:
            return ToolResult(
                success=False,
                error=(
                    f"只能提供一个主查询参数，你传了 {len(primary)} 个："
                    f"{primary}。如要 drill-down，先用一个 ID 拿数据再决定下一步。"
                ),
            )

        primary_flag = primary[0]
        primary_value = str(args[primary_flag]).strip()
        if not primary_value:
            return ToolResult(
                success=False, error=f"{primary_flag} value is empty",
            )

        # 2. section 校验
        section = args.get("section")
        if section is not None and section not in _VALID_SECTIONS:
            return ToolResult(
                success=False,
                error=f"invalid section {section!r}; allowed: {sorted(_VALID_SECTIONS)}",
            )

        # 3. query 校验
        query = args.get("query")
        if query is not None and query not in _VALID_QUERIES:
            return ToolResult(
                success=False,
                error=f"invalid query {query!r}; allowed: {sorted(_VALID_QUERIES)}",
            )

        # 4. limit 校验
        limit = args.get("limit")
        if limit is not None:
            try:
                limit_int = int(limit)
                if not (1 <= limit_int <= 500):
                    raise ValueError
            except (TypeError, ValueError):
                return ToolResult(
                    success=False,
                    error=f"invalid limit {limit!r}; must be int in [1, 500]",
                )

        # 5. 拼命令（每个 arg 单独传，不用 shell 拼接，避免注入）
        cfg = get_config()
        cmd: list[str] = [
            cfg.inspector.binary,
            "--config", cfg.inspector.config_file,
            "search",
            _flag_to_cli(primary_flag), primary_value,
        ]
        if section:
            cmd += ["--section", section]
        if query:
            cmd += ["--query", query]
        if limit is not None:
            cmd += ["--limit", str(limit)]
        from_d = args.get("from_date")
        if from_d:
            cmd += ["--from", str(from_d)]
        to_d = args.get("to_date")
        if to_d:
            cmd += ["--to", str(to_d)]

        # 注意：从不传 --show-all，避免 leak Stripe customer ID / token 等敏感字段
        # 到工单回复

        timeout = cfg.inspector.timeout_seconds
        cmd_preview = shlex.join(cmd[:6]) + " ..."
        logger.info(
            "inspector_exec_call",
            primary_flag=primary_flag,
            section=section,
            ticket_id=session.ticket_id,
            cmd_preview=cmd_preview,
        )

        # 6. 跑子进程（超时自动 retry 一次 —— transient 超时很常见，
        #    一次 retry 把"偶发慢"和"真查不到"区分开）
        async def _run_once() -> tuple[int | None, bytes, bytes]:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out_b, err_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
                return proc.returncode, out_b, err_b
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
                raise

        try:
            try:
                rc, stdout_b, stderr_b = await _run_once()
            except asyncio.TimeoutError:
                # retry 一次
                logger.warning(
                    "inspector_exec_timeout_retry",
                    primary_flag=primary_flag,
                    timeout_s=timeout,
                    ticket_id=session.ticket_id,
                )
                try:
                    rc, stdout_b, stderr_b = await _run_once()
                except asyncio.TimeoutError:
                    logger.warning(
                        "inspector_exec_timeout",
                        primary_flag=primary_flag,
                        timeout_s=timeout,
                        ticket_id=session.ticket_id,
                    )
                    # ⚠️ 两次都超时 = 没拿到任何数据。错误文本必须明确告诉
                    # agent：别臆造结果，改用 sql_query 直查具体表，或标 needs_human。
                    cli = _flag_to_cli(primary_flag)
                    return ToolResult(
                        success=False,
                        error=(
                            f"⚠️ inspector 两次执行均超时（{timeout}s × 2），"
                            f"**未获取到任何数据**。\n"
                            f"严禁基于猜测编造查询结果 / error 内容。下一步必须二选一：\n"
                            f"1. 改用 sql_query 直查 prod 库具体表（不要再 section=all）。"
                            f"针对 {cli} {primary_value} 常用：\n"
                            f"   - 知识库/文档：SELECT id,name,indexing_status,error "
                            f"FROM documents WHERE dataset_id='...'（或先查 datasets "
                            f"WHERE tenant_id='{primary_value}'）\n"
                            f"   - 模型凭据：SELECT provider_name,is_valid,updated_at "
                            f"FROM providers WHERE tenant_id='{primary_value}'\n"
                            f"   - workflow run：SELECT ... FROM workflow_runs / "
                            f"workflow_node_executions WHERE ...\n"
                            f"2. 若仍查不到 → Internal Note 标 needs_human=True，"
                            f"诚实写'inspector 超时未获取数据'，**不要**给客户编一个根因。"
                        ),
                    )
        except FileNotFoundError:
            return ToolResult(
                success=False,
                error=(
                    f"inspector binary 不存在：{cfg.inspector.binary}。"
                    "服务器上需先 install + bind-mount 进容器。"
                ),
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"inspector spawn failed: {e}",
            )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        # rc 来自 _run_once 返回值（上面已赋值）

        if rc != 0:
            # 失败：把 stderr 给 LLM 看
            err_text = stderr.strip()[:1500] or stdout.strip()[:1500]
            logger.warning(
                "inspector_exec_failed",
                rc=rc,
                err=err_text[:300],
                ticket_id=session.ticket_id,
            )
            return ToolResult(
                success=False,
                error=f"inspector exited with rc={rc}:\n{err_text}",
            )

        out = stdout.strip()
        if not out:
            return ToolResult(
                output="(inspector 返回空 — 这个 ID 在指定 section 下没数据)",
            )

        logger.info(
            "inspector_exec_ok",
            bytes=len(out),
            ticket_id=session.ticket_id,
        )
        return ToolResult(output=out)
