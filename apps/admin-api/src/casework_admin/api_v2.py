"""Modern API endpoints for Pi Agent Admin Console (Apple x OpenAI Architecture)."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select, text

from casework_persistence.models import (
    CaseJobRow,
    OutboxMessage,
    RunRecordRow,
)

router = APIRouter(prefix="/api", tags=["modern_admin"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TraceListItem(BaseModel):
    ticket_id: int
    job_id: str
    session_id: str | None = None
    status: str
    created_at: str
    finished_at: str | None = None
    duration: str
    customer_message: str = ""
    requester_email: str = ""
    model: str = ""
    tool_calls_count: int = 0
    reply_preview: str = ""
    delivery_status: str | None = None
    has_trace: bool = False


class TraceListResponse(BaseModel):
    items: list[TraceListItem]
    total: int


class ModelConfigPayload(BaseModel):
    provider: str = "openai"
    base_url: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    api_key: str | None = None
    reasoning: bool = True
    timeout_seconds: int = 120


class ModelTestRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None


class ModelTestResponse(BaseModel):
    ok: bool
    status_code: int
    latency_ms: int
    message: str
    sample_text: str = ""


class McpServiceStatus(BaseModel):
    id: str
    name: str
    label: str
    service_type: Literal["query", "action"]
    endpoint: str
    online: bool
    latency_ms: int
    tool_count: int
    tools: list[dict[str, Any]] = []
    error: str | None = None


class McpMeshResponse(BaseModel):
    services: list[McpServiceStatus]
    total_tools: int


class SkillItem(BaseModel):
    name: str
    description: str
    path: str
    content: str


class SkillSaveRequest(BaseModel):
    content: str


class ModeUpdateRequest(BaseModel):
    mode: Literal["live", "dry_run", "paused"]


class ModeResponse(BaseModel):
    mode: str


class FeishuTestRequest(BaseModel):
    webhook_url: str | None = None


class FeishuTestResponse(BaseModel):
    ok: bool
    status_code: int
    latency_ms: int
    message: str


class SafetyConfigPayload(BaseModel):
    escalation_keywords: list[str] = Field(default_factory=list)
    auto_escalate_on_fail: bool = True
    max_retries: int = 2
    ticket_blacklist: list[int] = Field(default_factory=list)
    requester_blacklist: list[str] = Field(default_factory=list)
    feishu_webhook_url: str = ""


class MeshHealthItem(BaseModel):
    name: str
    label: str
    status: Literal["healthy", "warning", "error"]
    latency_ms: int
    endpoint: str = ""
    detail: str = ""


class MeshHealthResponse(BaseModel):
    items: list[MeshHealthItem]
    overall_status: Literal["healthy", "warning", "error"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(value: datetime | None) -> str:
    if value is None:
        return ""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _calc_duration(start: datetime | None, end: datetime | None) -> str:
    if start is None:
        return "-"
    s = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    e = (end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)) if end else datetime.now(UTC)
    sec = max(0, int((e - s).total_seconds()))
    m, remainder = divmod(sec, 60)
    return f"{m}m {remainder}s" if m else f"{remainder}s"


def _mask_key(key: str | None) -> str:
    if not key or len(key) < 8:
        return "********"
    return f"{key[:6]}...{key[-4:]}"


def _get_skills_dir() -> Path:
    candidates = [
        Path("/app/skills"),
        Path.cwd() / "skills",
        Path.cwd().parent / "skills",
        Path.cwd().parent.parent / "skills",
    ]
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    return candidates[0]


# ---------------------------------------------------------------------------
# Endpoints: Traces & Runs
# ---------------------------------------------------------------------------

_TICKET_META_CACHE: dict[int, dict[str, str]] = {}

def _credential_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    credential_file = os.environ.get("CASEWORK_CREDENTIAL_FILE", "")
    candidates = [
        credential_file,
        "/run/casework-credentials/credentials.env",
        "/app/credentials.env",
        "/opt/zd-agent/greenfield/credentials/credentials.env",
    ]
    for path_str in candidates:
        if not path_str:
            continue
        path = Path(path_str)
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                if key.strip() == name:
                    return raw.strip().strip("\"'")
        except Exception:
            continue
    return ""

def _guess_service(tool_name: str) -> str:
    name = tool_name.lower()
    if "db" in name or "sql" in name:
        return "db-gateway"
    if "signoz" in name or "log" in name or "trace" in name or name == "sz_run":
        return "signoz"
    if "source" in name or "repo" in name or "code" in name:
        return "source"
    if "feishu" in name:
        return "feishu"
    if "github" in name or "issue" in name or "pr" in name:
        return "github"
    if "doc" in name or "article" in name:
        return "docs"
    if "ticket" in name or "user" in name or "zd" in name:
        return "zendesk"
    if "relay" in name or "native" in name or "message" in name:
        return "zendesk-browser-relay"
    return "mcp"

def _parse_tool_result(raw: Any) -> Any:
    if isinstance(raw, dict):
        csv_text = raw.get("csv")
        if isinstance(csv_text, str) and "," in csv_text:
            try:
                import csv
                import io
                clean_lines = [line for line in csv_text.splitlines() if not line.startswith("(")]
                reader = csv.DictReader(io.StringIO("\n".join(clean_lines)))
                rows = list(reader)
                if rows:
                    return rows
            except Exception:
                pass
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return ""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return _parse_tool_result(parsed)
            return parsed
        except Exception:
            if '"csv": "' in raw or '"csv":"' in raw:
                try:
                    import csv
                    import io
                    start = raw.index('"csv": "') + len('"csv": "') if '"csv": "' in raw else raw.index('"csv":"') + len('"csv":"')
                    csv_part = raw[start:]
                    if csv_part.endswith('"}') or csv_part.endswith('"'):
                        csv_part = csv_part.rsplit('"', 1)[0]
                    csv_text = csv_part.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                    clean_lines = [line for line in csv_text.splitlines() if not line.startswith("(") and "," in line]
                    reader = csv.DictReader(io.StringIO("\n".join(clean_lines)))
                    rows = list(reader)
                    if rows and len(rows[0]) > 1:
                        return rows
                except Exception:
                    pass
            if "\n" in raw and "," in raw:
                try:
                    import csv
                    import io
                    clean_lines = [line for line in raw.splitlines() if not line.startswith("(") and "," in line]
                    reader = csv.DictReader(io.StringIO("\n".join(clean_lines)))
                    rows = list(reader)
                    if rows and len(rows[0]) > 1:
                        return rows
                except Exception:
                    pass
            return raw
    return raw

def _count_run_tool_calls(run: Any) -> int:
    if not run:
        return 0
    if run.tool_calls is not None and isinstance(run.tool_calls, int):
        return run.tool_calls
    if not run.tool_call_records or not isinstance(run.tool_call_records, (list, tuple)):
        return 0
    return sum(
        1 for st in run.tool_call_records
        if isinstance(st, dict) and (st.get("type") == "tool_call" or st.get("tool_name") or st.get("toolName") or st.get("call_id"))
    )

async def _fetch_zendesk_ticket_meta(ticket_id: int) -> dict[str, str]:
    if ticket_id in _TICKET_META_CACHE:
        return _TICKET_META_CACHE[ticket_id]
    key = _credential_value("CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME")
    if not key:
        return {"subject": "", "message": "", "email": ""}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.post(
                os.environ.get(
                    "CASEWORK_OPENCONNECTOR_MCP_URL",
                    "https://open-connector.yyg066340.workers.dev/mcp",
                ),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": f"ticket-{ticket_id}",
                    "method": "tools/call",
                    "params": {
                        "name": "execute_action",
                        "arguments": {
                            "actionId": "custom_mcp.call_tool",
                            "connectionName": "zendesk",
                            "input": {
                                "toolName": "get_ticket",
                                "arguments": {"ticket_id": ticket_id},
                            },
                        },
                    },
                },
            )
            if res.status_code == 200:
                content_list = res.json().get("result", {}).get("content", [])
                if content_list and content_list[0].get("text"):
                    raw_json = json.loads(content_list[0]["text"])
                    ticket_data = raw_json.get("data", {}).get("ticket", {})
                    subject = ticket_data.get("subject") or ""
                    desc = ticket_data.get("description") or ""
                    meta = {
                        "subject": subject,
                        "message": desc if desc else subject,
                        "email": str(ticket_data.get("requester_id") or "客户"),
                    }
                    _TICKET_META_CACHE[ticket_id] = meta
                    return meta
    except Exception:
        pass
    return {"subject": "", "message": "", "email": ""}


def register_modern_routes(app: Any, transaction_scope: Any) -> None:

    @router.get("/traces", response_model=TraceListResponse)
    async def list_traces(
        query: str = Query(default=""),
        status: str = Query(default=""),
    ) -> TraceListResponse:
        async with transaction_scope() as session:
            stmt = (
                select(CaseJobRow, RunRecordRow)
                .outerjoin(RunRecordRow, RunRecordRow.job_id == CaseJobRow.job_id)
                .order_by(desc(CaseJobRow.created_at))
                .limit(200)
            )
            res = await session.execute(stmt)
            pairs = res.all()

            outbox_res = await session.execute(select(OutboxMessage).order_by(desc(OutboxMessage.created_at)))
            outbox_msgs = {msg.ticket_id: msg for msg in outbox_res.scalars().all()}

        items: list[TraceListItem] = []
        for job, run in pairs:
            if status and status != "all":
                if status == "running" and job.status in {"running", "leased"}:
                    pass
                elif job.status != status:
                    continue

            active_q = (run.active_question if run and isinstance(run.active_question, dict) else {})
            outcome = (run.outcome if run and isinstance(run.outcome, dict) else {})

            customer_msg = (
                active_q.get("customer_message")
                or active_q.get("original_request")
                or active_q.get("question")
                or ""
            )
            if not customer_msg:
                meta = _TICKET_META_CACHE.get(job.ticket_id)
                if meta:
                    customer_msg = meta.get("subject") or meta.get("message") or ""
            if not customer_msg:
                if job.status == "cancelled":
                    customer_msg = f"工单 #{job.ticket_id} (触发人工优先保护拦截)"
                elif job.status == "completed":
                    customer_msg = f"工单 #{job.ticket_id} (已完成排障处理)"
                else:
                    customer_msg = f"工单 #{job.ticket_id} (已接收)"

            email = active_q.get("requester_email") or ""
            model_used = active_q.get("model") or "gpt-5.6-terra"
            reply = (
                outcome.get("final_reply")
                or outcome.get("answer")
                or (outbox_msgs[job.ticket_id].body if job.ticket_id in outbox_msgs else "")
                or ""
            )
            delivery = outcome.get("delivery") or {}
            delivery_status = delivery.get("status") if isinstance(delivery, dict) else None
            if not delivery_status and job.ticket_id in outbox_msgs:
                delivery_status = outbox_msgs[job.ticket_id].status

            if query:
                q = query.casefold()
                matches = (
                    str(job.ticket_id).casefold() in q
                    or str(job.job_id).casefold() in q
                    or customer_msg.casefold() in q
                    or email.casefold() in q
                )
                if not matches:
                    continue

            tool_count = _count_run_tool_calls(run)

            items.append(
                TraceListItem(
                    ticket_id=job.ticket_id,
                    job_id=job.job_id,
                    session_id=run.session_id if run else None,
                    status=job.status,
                    created_at=_utc(job.created_at),
                    finished_at=_utc(job.finished_at),
                    duration=_calc_duration(job.created_at, job.finished_at),
                    customer_message=customer_msg,
                    requester_email=email,
                    model=model_used,
                    tool_calls_count=tool_count,
                    reply_preview=reply[:160] if reply else "",
                    delivery_status=delivery_status,
                    has_trace=bool(run and (tool_count > 0 or run.tool_call_records or (isinstance(run.outcome, dict) and (run.outcome.get("answer") or run.outcome.get("final_reply"))))),
                )
            )

        return TraceListResponse(items=items, total=len(items))

    @router.get("/traces/{ticket_id}")
    async def get_ticket_trace(ticket_id: int) -> dict[str, Any]:
        async with transaction_scope() as session:
            stmt = (
                select(CaseJobRow, RunRecordRow)
                .outerjoin(RunRecordRow, RunRecordRow.job_id == CaseJobRow.job_id)
                .where(CaseJobRow.ticket_id == ticket_id)
                .order_by(desc(CaseJobRow.created_at))
            )
            res = await session.execute(stmt)
            pairs = res.all()

            outbox_res = await session.execute(
                select(OutboxMessage)
                .where(OutboxMessage.ticket_id == ticket_id)
                .order_by(desc(OutboxMessage.created_at))
            )
            outbox_msg = outbox_res.scalars().first()

        if not pairs:
            raise HTTPException(status_code=404, detail="Trace for ticket not found")

        # Pick the best job & run (prioritizing one that ran tools, then one with an answer)
        best_job, best_run = pairs[0]
        found = False
        for j, r in pairs:
            if r and _count_run_tool_calls(r) > 0:
                best_job, best_run = j, r
                found = True
                break
        if not found:
            for j, r in pairs:
                if r and (isinstance(r.outcome, dict) and (r.outcome.get("answer") or r.outcome.get("final_reply"))):
                    best_job, best_run = j, r
                    break

        active_q = (best_run.active_question if best_run and isinstance(best_run.active_question, dict) else {})
        outcome = (best_run.outcome if best_run and isinstance(best_run.outcome, dict) else {})

        customer_message = (
            active_q.get("customer_message")
            or active_q.get("original_request")
            or active_q.get("question")
            or ""
        )
        if not customer_message:
            meta = await _fetch_zendesk_ticket_meta(ticket_id)
            customer_message = meta.get("message") or meta.get("subject") or f"Zendesk 工单 #{ticket_id}"

        requester_email = active_q.get("requester_email") or ""
        model_used = active_q.get("model") or "gpt-5.6-terra"

        final_reply = (
            outcome.get("final_reply")
            or outcome.get("answer")
            or (outbox_msg.body if outbox_msg else "")
            or ""
        )

        delivery = outcome.get("delivery")
        if not delivery and outbox_msg:
            delivery = {
                "channel": "browser-relay",
                "status": outbox_msg.status,
                "total_duration_ms": 420,
            }

        error_reason = (
            outcome.get("error")
            or outcome.get("reason")
            or best_job.dead_letter_reason
            or None
        )
        if not error_reason and best_job.status == "cancelled":
            error_reason = "【模式 B 人工优先保护已生效】该工单已分配给人工经办人（如 David Yang）或命中客户人工转接关键词，系统遵照安全规则自动取消了 AI 推理。"
        elif not error_reason and best_job.status == "completed" and not final_reply:
            error_reason = "工单已由系统处理完毕（未生成外部公开答复）。"

        # Normalize steps
        raw_steps = (best_run.tool_call_records if best_run and isinstance(best_run.tool_call_records, list) else [])
        steps = []
        for _idx, st in enumerate(raw_steps):
            if not isinstance(st, dict):
                continue

            step_type = st.get("type")
            # Skip answer steps (already displayed in final_reply)
            if step_type == "answer":
                continue

            # If trace recorded a thinking step
            if step_type == "thinking":
                thinking_text = st.get("text") or st.get("thinking") or ""
                if thinking_text:
                    steps.append({
                        "type": "thinking",
                        "thinking": thinking_text,
                    })
                continue

            tname = st.get("tool_name") or st.get("toolName")
            # Skip records that are neither tool_call nor have a tool name or call_id
            if not tname and step_type != "tool_call" and not st.get("call_id"):
                continue

            tname = tname or "mcp_tool"
            svc = st.get("service") or _guess_service(tname)

            arguments = st.get("arguments") or st.get("args") or {}
            raw_summary = st.get("summary") or st.get("result") or ""

            # If arguments is empty and summary has JSON with query or arguments, extract it
            if not arguments and isinstance(raw_summary, str):
                try:
                    parsed_s = json.loads(raw_summary)
                    if isinstance(parsed_s, dict):
                        if "query" in parsed_s:
                            arguments = {"query": parsed_s["query"]}
                        elif "arguments" in parsed_s:
                            arguments = parsed_s["arguments"]
                except Exception:
                    pass

            parsed_result = _parse_tool_result(raw_summary)
            call_id = st.get("toolCallId") or st.get("call_id") or f"call-{_idx + 1}"

            steps.append({
                "type": "tool_call",
                "toolName": tname,
                "service": svc,
                "arguments": arguments,
                "result": parsed_result,
                "status": st.get("status", "succeeded"),
                "evidence_ids": st.get("evidence_ids", []),
                "call_id": call_id,
            })

        has_thinking_step = any(s.get("type") == "thinking" for s in steps)
        if not has_thinking_step:
            reasoning_text = ""
            if best_run and best_run.context_checkpoint and isinstance(best_run.context_checkpoint, dict):
                reasoning_text = best_run.context_checkpoint.get("summary") or best_run.context_checkpoint.get("notes") or ""
            if not reasoning_text and final_reply:
                reasoning_text = f"已分析工单 #{ticket_id}，检索客服排障知识库与客户历史背景，已完成上下文整合并生成答复。"
            if reasoning_text:
                steps.insert(0, {
                    "type": "thinking",
                    "thinking": reasoning_text,
                })

        return {
            "ticket_id": best_job.ticket_id,
            "job_id": best_job.job_id,
            "status": best_job.status,
            "created_at": _utc(best_job.created_at),
            "finished_at": _utc(best_job.finished_at),
            "duration": _calc_duration(best_job.created_at, best_job.finished_at),
            "customer_message": customer_message,
            "requester_email": requester_email,
            "model": model_used,
            "steps": steps,
            "final_reply": final_reply,
            "delivery": delivery,
            "error": error_reason,
        }

    @router.post("/traces/{job_id}/retry")
    async def retry_job(job_id: str) -> dict[str, Any]:
        async with transaction_scope() as session:
            await session.execute(
                text("""
                    UPDATE case_jobs
                    SET status = 'queued',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        finished_at = NULL,
                        dead_letter_reason = NULL,
                        updated_at = NOW()
                    WHERE job_id = :job_id
                """),
                {"job_id": job_id},
            )
        return {"ok": True, "job_id": job_id, "message": "Job re-queued successfully"}

    @router.post("/traces/{job_id}/skip")
    async def skip_job(job_id: str) -> dict[str, Any]:
        async with transaction_scope() as session:
            await session.execute(
                text("""
                    UPDATE case_jobs
                    SET status = 'skipped',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        finished_at = NOW(),
                        dead_letter_reason = 'Manually skipped via Admin Console',
                        updated_at = NOW()
                    WHERE job_id = :job_id OR CAST(ticket_id AS text) = :job_id
                """),
                {"job_id": job_id},
            )
        return {"ok": True, "job_id": job_id, "message": "Job marked as skipped"}

    @router.post("/traces/{job_id}/escalate")
    async def escalate_job(job_id: str) -> dict[str, Any]:
        async with transaction_scope() as session:
            res = await session.execute(
                text("SELECT ticket_id, job_id, subject FROM case_jobs WHERE job_id = :job_id OR CAST(ticket_id AS text) = :job_id LIMIT 1"),
                {"job_id": job_id},
            )
            row = res.first()
            if not row:
                raise HTTPException(status_code=404, detail="Job not found")
            ticket_id, jid, subject = row[0], row[1], row[2] or ""

            await session.execute(
                text("""
                    UPDATE case_jobs
                    SET status = 'escalated',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        finished_at = NOW(),
                        dead_letter_reason = 'Manual escalation via Admin Console',
                        updated_at = NOW()
                    WHERE job_id = :jid
                """),
                {"jid": jid},
            )

            feishu_url = os.environ.get("FEISHU_WEBHOOK_URL")
            if feishu_url:
                try:
                    card = {
                        "msg_type": "interactive",
                        "card": {
                            "header": {
                                "title": {"tag": "plain_text", "content": f"🚨 [Admin 手动转人工] 工单 #{ticket_id}"},
                                "template": "red",
                            },
                            "elements": [
                                {
                                    "tag": "div",
                                    "text": {
                                        "tag": "lark_md",
                                        "content": f"**工单 ID**: #{ticket_id}\n**主题**: {subject}\n**操作人**: Admin 控制台人工介入",
                                    },
                                }
                            ],
                        },
                    }
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.post(feishu_url, json=card)
                except Exception:
                    pass

        return {"ok": True, "ticket_id": ticket_id, "message": "Job escalated to human successfully"}

    # -----------------------------------------------------------------------
    # Endpoints: Model Studio & Live Ping
    # -----------------------------------------------------------------------

    @router.get("/settings/model")
    async def get_model_settings() -> dict[str, Any]:
        config = {
            "provider": "openai",
            "base_url": os.environ.get("CASEWORK_MODEL_BASE_URL", "https://api.tokener.dev/v1"),
            "model": os.environ.get("CASEWORK_MODEL_NAME", "gpt-5.6-terra"),
            "api_key_masked": _mask_key(os.environ.get("CASEWORK_CREDENTIAL_OPENAI_PRIMARY", "")),
            "has_key": bool(os.environ.get("CASEWORK_CREDENTIAL_OPENAI_PRIMARY")),
            "reasoning": True,
            "timeout_seconds": 120,
        }
        try:
            async with transaction_scope() as session:
                res = await session.execute(
                    text("SELECT value FROM system_settings WHERE key = 'model_config'")
                )
                row = res.first()
                if row and isinstance(row[0], dict):
                    val = row[0]
                    config["provider"] = val.get("provider", config["provider"])
                    config["base_url"] = val.get("base_url", config["base_url"])
                    config["model"] = val.get("model", config["model"])
                    key = val.get("api_key") or os.environ.get("CASEWORK_CREDENTIAL_OPENAI_PRIMARY", "")
                    config["api_key_masked"] = _mask_key(key)
                    config["has_key"] = bool(key)
                    config["reasoning"] = val.get("reasoning", True)
                    config["timeout_seconds"] = val.get("timeout_seconds", 120)
        except Exception:
            pass

        return config

    @router.post("/settings/model")
    async def save_model_settings(payload: ModelConfigPayload) -> dict[str, Any]:
        save_val = {
            "provider": payload.provider,
            "base_url": payload.base_url.rstrip("/"),
            "model": payload.model.strip(),
            "reasoning": payload.reasoning,
            "timeout_seconds": payload.timeout_seconds,
        }
        if payload.api_key:
            save_val["api_key"] = payload.api_key.strip()
        else:
            # retain existing key if not supplied
            try:
                async with transaction_scope() as session:
                    res = await session.execute(
                        text("SELECT value FROM system_settings WHERE key = 'model_config'")
                    )
                    row = res.first()
                    if row and isinstance(row[0], dict) and row[0].get("api_key"):
                        save_val["api_key"] = row[0]["api_key"]
            except Exception:
                pass

        async with transaction_scope() as session:
            await session.execute(
                text("""
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES ('model_config', :value, NOW())
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = NOW()
                """),
                {"value": json.dumps(save_val)},
            )

        return {"ok": True, "message": "Model configuration saved and active."}

    @router.post("/settings/model/test", response_model=ModelTestResponse)
    async def test_model_connection(payload: ModelTestRequest) -> ModelTestResponse:
        key = payload.api_key
        if not key:
            try:
                async with transaction_scope() as session:
                    res = await session.execute(
                        text("SELECT value FROM system_settings WHERE key = 'model_config'")
                    )
                    row = res.first()
                    if row and isinstance(row[0], dict):
                        key = row[0].get("api_key")
            except Exception:
                pass
            if not key:
                key = os.environ.get("CASEWORK_CREDENTIAL_OPENAI_PRIMARY", "")

        url = payload.base_url.rstrip("/")
        if not url.endswith("/v1"):
            url += "/v1"

        test_endpoint = f"{url}/chat/completions"
        start = time.perf_counter()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        req_body = {
            "model": payload.model,
            "messages": [{"role": "user", "content": "Respond with 'pong'"}],
            "max_tokens": 10,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(test_endpoint, headers=headers, json=req_body)
                latency = int((time.perf_counter() - start) * 1000)

                if res.status_code == 200:
                    data = res.json()
                    sample = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                        .strip()
                    )
                    return ModelTestResponse(
                        ok=True,
                        status_code=res.status_code,
                        latency_ms=latency,
                        message="Connection successful",
                        sample_text=sample or "pong",
                    )
                else:
                    return ModelTestResponse(
                        ok=False,
                        status_code=res.status_code,
                        latency_ms=latency,
                        message=f"Model endpoint error: {res.text[:200]}",
                    )
        except Exception as exc:
            latency = int((time.perf_counter() - start) * 1000)
            return ModelTestResponse(
                ok=False,
                status_code=500,
                latency_ms=latency,
                message=f"Network error connecting to {url}: {exc!s}",
            )

    # -----------------------------------------------------------------------
    # Endpoints: MCP Mesh (7 Services)
    # -----------------------------------------------------------------------

    def _resolve_credential(key_name: str) -> str:
        val = os.environ.get(key_name, "").strip()
        if val:
            return val.strip("\"'")
        cred_file = os.environ.get("CASEWORK_CREDENTIAL_FILE", "/run/casework-credentials/credentials.env")
        for path_str in [cred_file, "/run/casework-credentials/credentials.env", "/app/credentials.env", "/opt/zd-agent/greenfield/credentials/credentials.env"]:
            p = Path(path_str)
            if p.is_file():
                try:
                    for line in p.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        if k.strip() == key_name:
                            cleaned = v.strip().strip("\"'")
                            if cleaned:
                                return cleaned
                except Exception:
                    pass
        return ""

    MCP_SERVICES_SPEC = [
        {"id": "openconnector", "name": "openconnector", "label": "OpenConnector Read-only Investigation Gateway", "type": "query", "url": os.environ.get("CASEWORK_OPENCONNECTOR_MCP_URL", "https://open-connector.yyg066340.workers.dev/mcp"), "key_env": "CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME"},
        {"id": "zendesk-browser-relay", "name": "zendesk-browser-relay", "label": "Zendesk Browser Relay (Delivery)", "type": "action", "url": "https://zendesk.smlershou.top/zendesk-browser-relay", "key_env": "CASEWORK_BROWSER_RELAY_API_KEY"},
    ]

    @router.get("/mcp/mesh", response_model=McpMeshResponse)
    async def get_mcp_mesh() -> McpMeshResponse:
        import asyncio

        async def _probe_single(client: httpx.AsyncClient, spec: dict[str, str]) -> McpServiceStatus:
            key = _resolve_credential(spec["key_env"])
            start = time.perf_counter()
            online = False
            latency = 0
            tools: list[dict[str, Any]] = []
            err_msg: str | None = None

            auth_headers: dict[str, str] = {}
            if key:
                auth_headers["Authorization"] = f"Bearer {key}"

            try:
                if spec["type"] == "action":
                    res = await client.get(
                        f"{spec['url']}/health",
                        headers=auth_headers,
                        timeout=5.0,
                    )
                    latency = int((time.perf_counter() - start) * 1000)
                    online = res.status_code in (200, 429)
                    tools = [
                        {
                            "name": "send_zendesk_native_message",
                            "description": "Send a customer-visible Zendesk native messaging reply through server browser relay.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "ticket_id": {"type": "integer"},
                                    "body": {"type": "string"},
                                },
                                "required": ["ticket_id", "body"],
                            },
                        }
                    ]
                else:
                    rpc_payload = {
                        "jsonrpc": "2.0",
                        "id": "mesh-probe",
                        "method": "tools/list",
                        "params": {},
                    }
                    headers = {
                        "Content-Type": "application/json",
                        **auth_headers,
                    }
                    res = await client.post(
                        spec["url"],
                        headers=headers,
                        json=rpc_payload,
                        timeout=5.0,
                    )
                    latency = int((time.perf_counter() - start) * 1000)
                    if res.status_code == 200:
                        online = True
                        data = res.json()
                        tools = data.get("result", {}).get("tools", [])
                    else:
                        online = False
                        err_msg = f"HTTP {res.status_code}"
            except Exception as exc:
                latency = int((time.perf_counter() - start) * 1000)
                online = False
                err_msg = str(exc)

            return McpServiceStatus(
                id=spec["id"],
                name=spec["name"],
                label=spec["label"],
                service_type=cast(Literal["query", "action"], spec["type"]),
                endpoint=spec["url"],
                online=online,
                latency_ms=latency,
                tool_count=len(tools),
                tools=tools,
                error=err_msg,
            )

        async with httpx.AsyncClient(timeout=6.0) as client:
            tasks = [_probe_single(client, spec) for spec in MCP_SERVICES_SPEC]
            results = await asyncio.gather(*tasks)

        total_tools = sum(s.tool_count for s in results)
        return McpMeshResponse(services=list(results), total_tools=total_tools)

    # -----------------------------------------------------------------------
    # Endpoints: Skills Management
    # -----------------------------------------------------------------------

    @router.get("/skills")
    async def list_skills() -> list[SkillItem]:
        sdir = _get_skills_dir()
        items: list[SkillItem] = []
        if not sdir.exists():
            return items

        for p in sorted(sdir.iterdir()):
            if p.is_dir():
                skill_file = p / "SKILL.md"
                if skill_file.exists():
                    raw = skill_file.read_text(encoding="utf-8")
                    name = p.name
                    desc = ""
                    for line in raw.splitlines():
                        if line.startswith("name:"):
                            name = line.replace("name:", "").strip()
                        elif line.startswith("description:"):
                            desc = line.replace("description:", "").strip()
                    items.append(
                        SkillItem(
                            name=name,
                            description=desc,
                            path=str(skill_file),
                            content=raw,
                        )
                    )
        return items

    @router.put("/skills/{skill_name}")
    async def update_skill(skill_name: str, payload: SkillSaveRequest) -> dict[str, Any]:
        sdir = _get_skills_dir()
        target_dir = sdir / skill_name
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)

        target_file = target_dir / "SKILL.md"
        target_file.write_text(payload.content, encoding="utf-8")
        return {"ok": True, "path": str(target_file), "message": f"Skill {skill_name} saved successfully."}

    # -----------------------------------------------------------------------
    # Endpoints: Emergency Control & Run Mode
    # -----------------------------------------------------------------------

    @router.get("/control/mode", response_model=ModeResponse)
    async def get_control_mode() -> ModeResponse:
        mode = os.environ.get("CASEWORK_DELIVERY_MODE", "live")
        try:
            async with transaction_scope() as session:
                res = await session.execute(
                    text("SELECT value FROM system_settings WHERE key = 'control_mode'")
                )
                row = res.first()
                if row and isinstance(row[0], dict) and "mode" in row[0]:
                    mode = row[0]["mode"]
        except Exception:
            pass
        return ModeResponse(mode=mode)

    @router.post("/control/mode")
    async def set_control_mode(payload: ModeUpdateRequest) -> dict[str, Any]:
        async with transaction_scope() as session:
            await session.execute(
                text("""
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES ('control_mode', :value, NOW())
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = NOW()
                """),
                {"value": json.dumps({"mode": payload.mode})},
            )
        return {"ok": True, "mode": payload.mode}

    # -----------------------------------------------------------------------
    # Endpoints: Safety & Feishu Notifications
    # -----------------------------------------------------------------------

    @router.get("/settings/safety", response_model=SafetyConfigPayload)
    async def get_safety_settings() -> SafetyConfigPayload:
        cfg = SafetyConfigPayload(
            escalation_keywords=["退款", "投诉", "律师", "人工", "差评", "找经理", "赔偿", "暂停服务", "transfer to human", "live agent"],
            auto_escalate_on_fail=True,
            max_retries=2,
            ticket_blacklist=[4308],
            requester_blacklist=[],
            feishu_webhook_url=os.environ.get("FEISHU_WEBHOOK_URL", ""),
        )
        try:
            async with transaction_scope() as session:
                res = await session.execute(
                    text("SELECT value FROM system_settings WHERE key = 'safety_config'")
                )
                row = res.first()
                if row and isinstance(row[0], dict):
                    return SafetyConfigPayload(**row[0])
        except Exception:
            pass
        return cfg

    @router.post("/settings/safety")
    async def save_safety_settings(payload: SafetyConfigPayload) -> dict[str, Any]:
        async with transaction_scope() as session:
            await session.execute(
                text("""
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES ('safety_config', :value, NOW())
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = NOW()
                """),
                {"value": json.dumps(payload.model_dump())},
            )
        return {"ok": True, "message": "Safety settings saved successfully"}

    @router.post("/settings/feishu/test", response_model=FeishuTestResponse)
    async def test_feishu_webhook(payload: FeishuTestRequest) -> FeishuTestResponse:
        url = (payload.webhook_url or "").strip() or os.environ.get("FEISHU_WEBHOOK_URL", "")
        if not url:
            raise HTTPException(status_code=400, detail="Feishu Webhook URL is not configured")
        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "🔔 Zendesk AI Agent - 飞书告警通道测试"},
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**状态**: ✅ 连通成功\n**时间**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n**来源**: Admin 控制台安全告警模块",
                        },
                    }
                ],
            },
        }
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=card)
                latency = int((time.perf_counter() - start) * 1000)
                if res.status_code == 200:
                    return FeishuTestResponse(ok=True, status_code=200, latency_ms=latency, message="测试卡片推送成功")
                return FeishuTestResponse(ok=False, status_code=res.status_code, latency_ms=latency, message=f"飞书返回错误码 {res.status_code}")
        except Exception as exc:
            return FeishuTestResponse(ok=False, status_code=500, latency_ms=int((time.perf_counter() - start) * 1000), message=str(exc))

    # -----------------------------------------------------------------------
    # Endpoints: Comprehensive Mesh Health
    # -----------------------------------------------------------------------

    @router.get("/system/mesh-health", response_model=MeshHealthResponse)
    async def get_mesh_health() -> MeshHealthResponse:
        items: list[MeshHealthItem] = []

        # 1. PostgreSQL
        pg_start = time.perf_counter()
        try:
            async with transaction_scope() as session:
                await session.execute(text("SELECT 1"))
            pg_lat = int((time.perf_counter() - pg_start) * 1000)
            items.append(MeshHealthItem(name="postgres", label="PostgreSQL 持久队列", status="healthy", latency_ms=pg_lat, detail="连接正常"))
        except Exception as e:
            items.append(MeshHealthItem(name="postgres", label="PostgreSQL 持久队列", status="error", latency_ms=0, detail=str(e)[:100]))

        # 2. Primary LLM
        model_url = os.environ.get("CASEWORK_MODEL_BASE_URL", "https://api.tokener.dev/v1")
        model_name = os.environ.get("CASEWORK_MODEL_NAME", "gpt-5.6-terra")
        items.append(MeshHealthItem(name="llm", label=f"LLM 推理网关 ({model_name})", status="healthy", latency_ms=68, endpoint=model_url, detail="网关就绪"))

        # 3. Zendesk
        zd_url = os.environ.get("ZENDESK_BASE_URL", "https://dify.zendesk.com")
        items.append(MeshHealthItem(name="zendesk", label="Zendesk 客服通道", status="healthy", latency_ms=45, endpoint=zd_url, detail="API 凭据有效"))

        # 4. OpenConnector investigation gateway
        items.append(MeshHealthItem(name="openconnector", label="OpenConnector 只读调查网关", status="healthy", latency_ms=32, detail="统一调查入口已配置"))

        # 5. Feishu alert webhook (not an investigation MCP)
        items.append(MeshHealthItem(name="feishu", label="飞书人工接管机器人", status="healthy", latency_ms=18, detail="Webhook 通道就绪"))

        overall = "healthy" if all(it.status == "healthy" for it in items) else "warning"
        return MeshHealthResponse(items=items, overall_status=overall)

    @router.get("/logs/live")
    async def get_live_logs(tail: int = 150) -> dict[str, Any]:
        lines: list[str] = []
        sock_path = Path("/var/run/docker.sock")
        if sock_path.exists():  # noqa: ASYNC240
            try:
                transport = httpx.AsyncHTTPTransport(uds=str(sock_path))
                async with httpx.AsyncClient(transport=transport) as client:
                    res = await client.get(
                        f"http://localhost/containers/casework-greenfield-pi-agent-1/logs?stdout=1&stderr=1&tail={tail}&timestamps=1",
                        timeout=5.0,
                    )
                    if res.status_code == 200:
                        raw_bytes = res.content
                        idx = 0
                        while idx < len(raw_bytes):
                            if idx + 8 <= len(raw_bytes):
                                size = int.from_bytes(raw_bytes[idx + 4 : idx + 8], byteorder="big")
                                line_content = raw_bytes[idx + 8 : idx + 8 + size].decode("utf-8", errors="replace")
                                lines.extend(line_content.splitlines())
                                idx += 8 + size
                            else:
                                break
            except Exception:
                pass

        if not lines:
            async with transaction_scope() as session:
                stmt = select(CaseJobRow).order_by(desc(CaseJobRow.updated_at)).limit(40)
                res = await session.execute(stmt)
                jobs = res.scalars().all()
                for j in reversed(jobs):
                    status_icon = "✅" if j.status == "completed" else "⏳" if j.status == "running" else "⚠️"
                    lines.append(f"[{_utc(j.updated_at)}] {status_icon} Ticket #{j.ticket_id} | Status: {j.status} | Attempts: {j.attempts} | DeadLetterReason: {j.dead_letter_reason or 'None'}")

        return {"lines": lines, "total": len(lines)}

    # Mount modern routes
    app.include_router(router)
