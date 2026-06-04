"""log-viewer：只读 agent jsonl，提供工单列表 + 时间线。零认证（由 oauth2-proxy 兜）。"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

import admin as A
import parser as P

LOG_DIR = os.environ.get("ZD_LOG_DIR", "/opt/zd-agent/logs")
RECORDS_DIR = os.environ.get(
    "ZD_RECORDS_DIR", "/opt/zd-agent/resources/zendesk-cli/工单记录"
)
# 0 = 读取全部历史日志（磁盘上的 jsonl 始终不删）。可用 ZD_LOG_DAYS 覆盖成 N 天窗口。
DAYS = int(os.environ.get("ZD_LOG_DAYS", "0"))
_STATIC = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="zd-agent log viewer")


@app.get("/api/tickets")
def list_tickets() -> JSONResponse:
    events = P.read_events(LOG_DIR, days=DAYS)
    return JSONResponse(P.summarize_tickets(P.group_by_ticket(events)))


@app.get("/api/tickets/{ticket_id}")
def ticket_detail(ticket_id: int) -> JSONResponse:
    events = P.read_events(LOG_DIR, days=DAYS)
    timeline = P.ticket_timeline(P.group_by_ticket(events), ticket_id)
    if not timeline:
        raise HTTPException(status_code=404, detail="ticket not found")
    return JSONResponse(timeline)


@app.get("/api/tickets/{ticket_id}/review")
def ticket_review(ticket_id: int) -> JSONResponse:
    events = P.read_events(LOG_DIR, days=DAYS)
    timeline = P.ticket_timeline(P.group_by_ticket(events), ticket_id)
    if not timeline:
        raise HTTPException(status_code=404, detail="ticket not found")
    return JSONResponse(P.build_ticket_review(timeline, RECORDS_DIR, ticket_id))


@app.get("/api/meta")
def meta() -> JSONResponse:
    """前端设置面板用：只读展示日志读取范围。days=0 表示全部历史。"""
    return JSONResponse({"days": DAYS, "all": DAYS <= 0})


@app.get("/api/admin/status")
def admin_status() -> JSONResponse:
    return JSONResponse(A.status())


@app.get("/api/admin/config")
def admin_config() -> JSONResponse:
    return JSONResponse(A.read_config())


@app.put("/api/admin/config")
async def admin_update_config(request: Request) -> JSONResponse:
    payload = await request.json()
    return JSONResponse(A.update_config(payload, request))


@app.post("/api/admin/control/pause")
def admin_pause(request: Request) -> JSONResponse:
    return JSONResponse(A.write_control(True, request))


@app.post("/api/admin/control/resume")
def admin_resume(request: Request) -> JSONResponse:
    return JSONResponse(A.write_control(False, request))


@app.post("/api/admin/service/restart")
def admin_restart(request: Request) -> JSONResponse:
    return JSONResponse(A.restart(request))


@app.get("/api/admin/audit")
def admin_audit() -> JSONResponse:
    return JSONResponse(A.read_audit())


@app.get("/dify-avatar.svg")
def dify_avatar() -> FileResponse:
    return FileResponse(
        os.path.join(_STATIC, "dify-avatar.svg"), media_type="image/svg+xml"
    )


@app.get("/")
def index() -> FileResponse:
    # 不缓存 index.html：UI 更新后浏览器立即拿到新页面，避免"还是旧版"。
    return FileResponse(
        os.path.join(_STATIC, "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
