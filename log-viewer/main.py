"""log-viewer：只读 agent jsonl，提供工单列表 + 时间线。零认证（由 oauth2-proxy 兜）。"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

import parser as P

LOG_DIR = os.environ.get("ZD_LOG_DIR", "/opt/zd-agent/logs")
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


@app.get("/api/meta")
def meta() -> JSONResponse:
    """前端设置面板用：只读展示日志读取范围。days=0 表示全部历史。"""
    return JSONResponse({"days": DAYS, "all": DAYS <= 0})


@app.get("/dify-avatar.svg")
def dify_avatar() -> FileResponse:
    return FileResponse(
        os.path.join(_STATIC, "dify-avatar.svg"), media_type="image/svg+xml"
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(_STATIC, "index.html"))
