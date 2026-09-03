"""Modern API endpoints for Pi Agent Admin Console (Apple x OpenAI Architecture)."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select, text

from casework_persistence.models import CaseJobRow, RunRecordRow

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

        items: list[TraceListItem] = []
        for job, run in pairs:
            # Filtering
            if status and status != "all":
                if status == "running" and job.status in {"running", "leased"}:
                    pass
                elif job.status != status:
                    continue

            active_q = (run.active_question if run and isinstance(run.active_question, dict) else {})
            outcome = (run.outcome if run and isinstance(run.outcome, dict) else {})
            customer_msg = active_q.get("customer_message") or ""
            email = active_q.get("requester_email") or ""
            model_used = active_q.get("model") or ""
            reply = outcome.get("final_reply") or ""
            delivery = outcome.get("delivery") or {}
            delivery_status = delivery.get("status") if isinstance(delivery, dict) else None

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
                    tool_calls_count=run.tool_calls if run and run.tool_calls is not None else 0,
                    reply_preview=reply[:160] if reply else "",
                    delivery_status=delivery_status,
                    has_trace=bool(run and run.tool_call_records),
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
                .limit(1)
            )
            res = await session.execute(stmt)
            pair = res.first()

        if not pair:
            raise HTTPException(status_code=404, detail="Trace for ticket not found")

        job, run = pair
        active_q = (run.active_question if run and isinstance(run.active_question, dict) else {})
        outcome = (run.outcome if run and isinstance(run.outcome, dict) else {})
        steps = (run.tool_call_records if run and isinstance(run.tool_call_records, list) else [])

        return {
            "ticket_id": job.ticket_id,
            "job_id": job.job_id,
            "status": job.status,
            "created_at": _utc(job.created_at),
            "finished_at": _utc(job.finished_at),
            "duration": _calc_duration(job.created_at, job.finished_at),
            "customer_message": active_q.get("customer_message", ""),
            "requester_email": active_q.get("requester_email", ""),
            "model": active_q.get("model", ""),
            "steps": steps,
            "final_reply": outcome.get("final_reply", ""),
            "delivery": outcome.get("delivery"),
            "error": outcome.get("error") or job.dead_letter_reason,
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
                message=f"Network error connecting to {url}: {str(exc)}",
            )

    # -----------------------------------------------------------------------
    # Endpoints: MCP Mesh (7 Services)
    # -----------------------------------------------------------------------

    MCP_SERVICES_SPEC = [
        {"id": "db-gateway", "name": "db-gateway", "label": "Dify Database Gateway", "type": "query", "url": "https://zendesk.smlershou.top/db-gateway/mcp", "key_env": "CASEWORK_CREDENTIAL_DB_GATEWAY"},
        {"id": "zendesk", "name": "zendesk", "label": "Zendesk MCP API", "type": "query", "url": "https://zendesk.smlershou.top/zendesk-mcp/mcp", "key_env": "CASEWORK_CREDENTIAL_ZENDESK_MCP"},
        {"id": "signoz", "name": "signoz", "label": "SigNoz APM & Logs", "type": "query", "url": "https://zendesk.smlershou.top/signoz-mcp/mcp", "key_env": "CASEWORK_CREDENTIAL_SIGNOZ_MCP"},
        {"id": "source", "name": "source", "label": "Dify Codebase Search", "type": "query", "url": "https://zendesk.smlershou.top/source-mcp/mcp", "key_env": "CASEWORK_CREDENTIAL_SOURCE_MCP"},
        {"id": "github", "name": "github", "label": "GitHub Issues & PRs", "type": "query", "url": "https://zendesk.smlershou.top/github-mcp/mcp", "key_env": "CASEWORK_CREDENTIAL_GITHUB_MCP"},
        {"id": "docs", "name": "docs", "label": "Official Docs MCP", "type": "query", "url": "https://zendesk.smlershou.top/docs-tool-service/mcp", "key_env": "CASEWORK_CREDENTIAL_DOCS_MCP"},
        {"id": "zendesk-browser-relay", "name": "zendesk-browser-relay", "label": "Zendesk Browser Relay (Delivery)", "type": "action", "url": "https://zendesk.smlershou.top/zendesk-browser-relay", "key_env": "CASEWORK_BROWSER_RELAY_API_KEY"},
    ]

    @router.get("/mcp/mesh", response_model=McpMeshResponse)
    async def get_mcp_mesh() -> McpMeshResponse:
        results: list[McpServiceStatus] = []
        total_tools = 0

        async with httpx.AsyncClient(timeout=8.0) as client:
            for spec in MCP_SERVICES_SPEC:
                key = os.environ.get(spec["key_env"], "")
                start = time.perf_counter()
                online = False
                latency = 0
                tools: list[dict[str, Any]] = []
                err_msg: str | None = None

                try:
                    if spec["type"] == "action":
                        res = await client.get(
                            f"{spec['url']}/ready",
                            headers={"Authorization": f"Bearer {key}"},
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
                        res = await client.post(
                            spec["url"],
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": f"Bearer {key}",
                            },
                            json=rpc_payload,
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

                results.append(
                    McpServiceStatus(
                        id=spec["id"],
                        name=spec["name"],
                        label=spec["label"],
                        service_type=spec["type"],
                        endpoint=spec["url"],
                        online=online,
                        latency_ms=latency,
                        tool_count=len(tools),
                        tools=tools,
                        error=err_msg,
                    )
                )
                total_tools += len(tools)

        return McpMeshResponse(services=results, total_tools=total_tools)

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

    # Mount modern routes
    app.include_router(router)
