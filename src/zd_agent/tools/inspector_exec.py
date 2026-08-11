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
- 子进程单次 timeout 45s，最多重试一次
- stdin 关闭，无任意命令注入面
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
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

_BILLING_PLAN_CODES = {
    "basic",
    "sandbox",
    "professional",
    "team",
    "enterprise",
}

_TENANTS_COUNT_RE = re.compile(r"^TENANTS\s*\((\d+)\)\s*$", re.MULTILINE)
_TENANT_ID_TOKEN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{2,127}$")


def _pipe_field_value(output: str, field: str) -> str:
    pattern = re.compile(
        rf"^\|\s*{re.escape(field)}\s*\|\s*([^|]+?)\s*\|\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(output)
    return match.group(1).strip() if match else ""


def _normalize_plan_code(value: str) -> str:
    lowered = value.strip().lower()
    for code in _BILLING_PLAN_CODES:
        if re.search(rf"(?<![a-z]){re.escape(code)}(?![a-z])", lowered):
            return code
    return ""


def _authoritative_billing_prefix(output: str, section: str | None) -> str:
    """Make the billing source hierarchy machine-readable and fail closed.

    Inspector prints ``TENANT.Plan`` from the Prod database before its Billing
    section.  Those values may legitimately disagree after a subscription
    change.  A model can over-weight the first table, so a billing decision is
    allowed only after a tenant-scoped ``section=billing`` call and the wrapper
    places the authoritative values at the very start of the tool result.
    """
    common = (
        "TENANT/TENANTS Plan is non-authoritative Prod metadata; never use it "
        "as the current subscription plan.\n"
    )
    if section != "billing":
        return (
            "AUTHORITATIVE_BILLING_PLAN=unknown\n"
            "AUTHORITATIVE_BILLING_STATUS=unknown\n"
            "AUTHORITATIVE_BILLING_SOURCE=unverified\n"
            f"{common}"
            "To make a subscription-plan claim, rerun inspector_exec with "
            "tenant_id + section=billing (or query tenant_subscription_settings "
            "in database=billing).\n\n"
        )

    source = _pipe_field_value(output, "Source").lower()
    plan = _normalize_plan_code(
        _pipe_field_value(output, "Plan Code")
        or _pipe_field_value(output, "Current Plan")
    )
    status = _pipe_field_value(output, "Subscription Status").lower()
    if source != "billing_db" or not plan:
        return (
            "AUTHORITATIVE_BILLING_PLAN=unknown\n"
            f"AUTHORITATIVE_BILLING_STATUS={status or 'unknown'}\n"
            f"AUTHORITATIVE_BILLING_SOURCE={source or 'unavailable'}\n"
            f"{common}"
            "The Billing section did not return an authoritative plan. Query "
            "tenant_subscription_settings in database=billing before making a "
            "subscription-plan claim.\n\n"
        )

    return (
        f"AUTHORITATIVE_BILLING_PLAN={plan}\n"
        f"AUTHORITATIVE_BILLING_STATUS={status or 'unknown'}\n"
        "AUTHORITATIVE_BILLING_SOURCE=billing_db\n"
        f"{common}\n"
    )


def _email_workspace_candidates(output: str) -> list[dict[str, str]]:
    """Parse tenant IDs and names from Inspector's email membership table."""
    candidates: list[dict[str, str]] = []
    seen_tenant_ids: set[str] = set()
    in_tenants = False
    tenant_id_index: int | None = None
    name_index: int | None = None
    for line in output.splitlines():
        if _TENANTS_COUNT_RE.fullmatch(line.strip()):
            in_tenants = True
            continue
        if not in_tenants:
            continue
        if line.startswith("提示:"):
            break
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        normalized_headers = [
            re.sub(r"[\s_-]+", " ", cell).strip().casefold() for cell in cells
        ]
        if tenant_id_index is None or name_index is None:
            if (
                "tenant id" not in normalized_headers
                or "name" not in normalized_headers
            ):
                continue
            tenant_id_index = normalized_headers.index("tenant id")
            name_index = normalized_headers.index("name")
            continue
        if len(cells) <= max(tenant_id_index, name_index):
            continue
        tenant_id = cells[tenant_id_index]
        if not _TENANT_ID_TOKEN_RE.fullmatch(tenant_id) or tenant_id in seen_tenant_ids:
            continue
        seen_tenant_ids.add(tenant_id)
        candidates.append({"tenant_id": tenant_id, "name": cells[name_index]})
    return candidates


def _email_workspace_prefix(
    output: str,
) -> tuple[str, dict[str, Any]]:
    """Promote email -> tenant membership resolution ahead of Prod plan fields."""
    count_match = _TENANTS_COUNT_RE.search(output)
    if not count_match:
        count = -1
    else:
        count = int(count_match.group(1))

    candidates = _email_workspace_candidates(output)
    tenant_ids = [candidate["tenant_id"] for candidate in candidates]

    if count == 0 and not tenant_ids:
        resolution = "none"
    elif count == 1 and len(tenant_ids) == 1:
        resolution = "unique"
    elif count > 1 and len(tenant_ids) == count:
        resolution = "ambiguous"
    else:
        resolution = "unverified"

    candidates_json = json.dumps(
        candidates,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    prefix = (
        f"EMAIL_WORKSPACE_RESOLUTION={resolution}\n"
        f"EMAIL_WORKSPACE_COUNT={max(count, 0)}\n"
        f"EMAIL_WORKSPACE_TENANT_IDS={','.join(tenant_ids)}\n"
        f"EMAIL_WORKSPACE_CANDIDATES_JSON={candidates_json}\n"
        "EMAIL_WORKSPACE_SOURCE=prod_membership\n"
        "TENANTS table Plan is non-authoritative Prod metadata; use only "
        "tenant_id/name/status/role for workspace resolution.\n\n"
    )
    metadata = {
        "source": "prod_membership",
        "status": resolution,
        "count": max(count, 0),
        "tenant_ids": tenant_ids,
        "candidates": candidates,
    }
    return prefix, metadata


def _flag_to_cli(flag: str) -> str:
    """``account_id`` → ``--account-id``"""
    return "--" + flag.replace("_", "-")


def _coerce_string_arg(args: dict[str, Any], name: str) -> str:
    value = args.get(name, "")
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value.strip()


def _coerce_optional_string_arg(args: dict[str, Any], name: str) -> str | None:
    value = args.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    cleaned = value.strip()
    return cleaned or None


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate Inspector and any child processes it spawned.

    Inspector can fan out to several database clients. Killing only the CLI
    parent leaves those children alive and lets them keep sockets/resources
    after the tool call has already timed out.
    """
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - production runs on Linux
            proc.kill()
    except ProcessLookupError:
        pass
    except OSError:
        # If a process group could not be created or addressed, still make a
        # best effort to stop the direct child.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        await proc.wait()
    except ProcessLookupError:
        pass


class InspectorExec(Tool):
    name = "inspector_exec"
    description = (
        "调用 Inspector CLI（langgenius/infra-toolkit）做 SaaS DB drill-down。"
        "**优先于 sql_query 使用** 的场景：quota/usage（应用数 / 文档数 / 知识库存储 / "
        "成员数）、workflow_run 失败定位（一次拿 run + 节点链）、UUID 类型未知"
        "（自动识别）、账号或租户的 billing/runs/apps section drill-down。"
        "必须传一个主查询参数（email / account_id / tenant_id / app_id / dataset_id / "
        "document_id / conversation_id / workflow_id / workflow_run_id / id），"
        "可选 section（all / tenants / apps / runs / conversations / billing / quota / members）。"
        "订阅套餐/状态必须先拿 tenant_id，再单独调用 section=billing；email/section=all "
        "和 TENANT/TENANTS Plan 不能作为套餐证据。工作空间定位优先使用用户表单 "
        "Email + section=tenants；若返回多个 workspace，只能用表单中的规范 UUID "
        "或候选集合内唯一精确匹配的 workspace 名称消歧，否则禁止猜测。"
        "+ query（failed / succeeded）+ limit（默认 50）+ from / to（YYYY-MM-DD）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "客户邮箱"},
            "account_id": {"type": "string", "description": "Account UUID"},
            "tenant_id": {"type": "string", "description": "Tenant/Workspace UUID"},
            "app_id": {"type": "string", "description": "App UUID"},
            "dataset_id": {
                "type": "string",
                "description": "Dataset/Knowledge base UUID",
            },
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
        self,
        args: dict[str, Any],
        session: "AgentSession",
    ) -> ToolResult:
        # 1. 主查询参数校验：必须且只能传一个
        primary = [f for f in _PRIMARY_QUERY_FLAGS if args.get(f)]
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
        try:
            primary_value = _coerce_string_arg(args, primary_flag)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        if not primary_value:
            return ToolResult(
                success=False,
                error=f"{primary_flag} value is empty",
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

        try:
            from_d = _coerce_optional_string_arg(args, "from_date")
            to_d = _coerce_optional_string_arg(args, "to_date")
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        # 5. 拼命令（每个 arg 单独传，不用 shell 拼接，避免注入）
        cfg = get_config()
        cmd: list[str] = [
            cfg.inspector.binary,
            "--config",
            cfg.inspector.config_file,
            "search",
            _flag_to_cli(primary_flag),
            primary_value,
        ]
        if section:
            cmd += ["--section", section]
        if query:
            cmd += ["--query", query]
        if limit is not None:
            cmd += ["--limit", str(limit)]
        if from_d:
            cmd += ["--from", from_d]
        if to_d:
            cmd += ["--to", to_d]

        # 注意：从不传 --show-all，避免 leak Stripe customer ID / token 等敏感字段
        # 到工单回复

        timeout = cfg.inspector.timeout_seconds
        cmd_preview = shlex.join(cmd[:5] + ["[REDACTED]"]) + " ..."
        logger.info(
            "inspector_exec_call",
            primary_flag=primary_flag,
            section=section,
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            timeout_s=timeout,
            cmd_preview=cmd_preview,
        )

        # 6. 跑子进程（超时自动 retry 一次 —— transient 超时很常见，
        #    一次 retry 把"偶发慢"和"真查不到"区分开）
        async def _run_once(attempt: int) -> tuple[int | None, bytes, bytes]:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Required so a timeout can terminate Inspector's database
                # client children as one process group.
                start_new_session=True,
            )
            try:
                out_b, err_b = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
                return proc.returncode, out_b, err_b
            except asyncio.CancelledError:
                await _terminate_process_tree(proc)
                logger.warning(
                    "inspector_exec_cancelled",
                    primary_flag=primary_flag,
                    section=section,
                    attempt=attempt,
                    ticket_id=session.ticket_id,
                    session_id=session.session_id,
                    stage="process_wait",
                )
                raise
            except asyncio.TimeoutError:
                await _terminate_process_tree(proc)
                logger.warning(
                    "inspector_exec_attempt_timeout",
                    primary_flag=primary_flag,
                    section=section,
                    attempt=attempt,
                    timeout_s=timeout,
                    ticket_id=session.ticket_id,
                    session_id=session.session_id,
                    stage="process_wait",
                )
                raise

        try:
            try:
                rc, stdout_b, stderr_b = await _run_once(1)
            except asyncio.TimeoutError:
                # retry 一次
                logger.warning(
                    "inspector_exec_timeout_retry",
                    primary_flag=primary_flag,
                    section=section,
                    attempt=1,
                    timeout_s=timeout,
                    ticket_id=session.ticket_id,
                    session_id=session.session_id,
                )
                try:
                    rc, stdout_b, stderr_b = await _run_once(2)
                except asyncio.TimeoutError:
                    logger.warning(
                        "inspector_exec_timeout",
                        primary_flag=primary_flag,
                        section=section,
                        attempt=2,
                        timeout_s=timeout,
                        ticket_id=session.ticket_id,
                        session_id=session.session_id,
                    )
                    # ⚠️ 两次都超时 = 没拿到任何数据。错误文本必须明确告诉
                    # investigator：别臆造结果，改用 sql_query 直查具体表，
                    # 或让 v2 evidence policy 保守决策。
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
                            f"2. 若仍查不到 → 在证据账本记录 inspector 超时，"
                            f"由 v2 决定继续取证、澄清或创建类型化支持归属；"
                            f"**不要**给客户编一个根因。"
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
        result_metadata: dict[str, Any] = {}
        if primary_flag == "email" and section == "tenants":
            workspace_prefix, workspace_resolution = _email_workspace_prefix(out)
            out = workspace_prefix + out
            result_metadata["workspace_resolution"] = workspace_resolution
        if section in {"billing", "all"}:
            out = _authoritative_billing_prefix(out, section) + out
        return ToolResult(output=out, metadata=result_metadata)
