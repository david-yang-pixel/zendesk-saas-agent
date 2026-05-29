# 工单日志 Web 查看系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 zd-agent 做一个按工单查看处理日志的 Web 界面（工单列表 + 事件级时间线），用 Google 登录（限 @dify.ai）保护。

**Architecture:** agent 在现有 stdout 控制台日志之外，额外把每条日志以 JSON 写到按天的 jsonl 文件；独立 FastAPI 服务（log-viewer）只读这些 jsonl，聚合出列表与时间线；nginx 反代 + oauth2-proxy 做 Google 认证。三块互相解耦。

**Tech Stack:** Python 3.13 / structlog（agent 侧）/ FastAPI + uvicorn（log-viewer，独立 pyproject）/ 原生 JS 单文件前端 / nginx + systemd + oauth2-proxy（部署）

**Design doc:** `docs/superpowers/specs/2026-05-29-log-viewer-web-design.md`

---

## 背景事实（实现前必读，避免踩坑）

- `src/zd_agent/log.py` 的 `setup_logging()` **是活代码**，在 `cli.py:38` 的 typer
  `@app.callback()` 里被调用（每个命令前跑）。`json_output` 默认 `False` → 走
  `ConsoleRenderer` → 这就是 journald 里带 ANSI 颜色的原因。**不要"修 bug"，它没坏。**
- 用的是 `structlog.PrintLoggerFactory()`（只写 stdout）+ `cache_logger_on_first_use=True`。
- **关键不变量：stdout 必须保持 ConsoleRenderer 带色输出**（裸看 journalctl 不变）。
  jsonl 文件 sink 作为一个**独立的 structlog processor**（写文件副作用）插在
  ConsoleRenderer **之前**，ConsoleRenderer 仍是处理链终点。
- 容器以 `--log-driver=journald` 跑，需把 `/opt/zd-agent/logs` 用 `-v` 挂进容器，
  jsonl 才能落到宿主磁盘给 log-viewer 读。
- 部署命令（踩过坑）：`docker build --network host`（clash 代理），改 config 只 restart 无需 rebuild。

---

## Phase 1: agent jsonl sink（日志落盘）

### Task 1.1: config 加 LoggingConfig

**Files:**
- Modify: `src/zd_agent/config.py`（在 `class AppConfig` 之前加新 class；AppConfig 内挂字段）
- Test: `tests/test_logging_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_logging_config.py
from zd_agent.config import AppConfig, LoggingConfig


def test_logging_config_defaults():
    cfg = AppConfig()
    assert cfg.logging.jsonl_enabled is True
    assert cfg.logging.log_dir == "/opt/zd-agent/logs"


def test_logging_config_override():
    lc = LoggingConfig(jsonl_enabled=False, log_dir="/tmp/x")
    assert lc.jsonl_enabled is False
    assert lc.log_dir == "/tmp/x"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_logging_config.py -v`
Expected: FAIL（`ImportError: cannot import name 'LoggingConfig'`）

- [ ] **Step 3: 实现**

在 `src/zd_agent/config.py` 中 `class AppConfig(BaseModel):` 这一行**之前**插入：

```python
class LoggingConfig(BaseModel):
    """日志配置。stdout 始终走带色 ConsoleRenderer（不可配）；
    jsonl_enabled 控制是否额外写 JSON 行到 log_dir/events-<date>.jsonl 供 log-viewer 读。
    """
    jsonl_enabled: bool = True
    log_dir: str = "/opt/zd-agent/logs"
```

在 `class AppConfig` 字段区（`public_reply: ...` 那行后面）加：

```python
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_logging_config.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/zd_agent/config.py tests/test_logging_config.py
git commit -m "feat(log): 加 LoggingConfig（jsonl_enabled + log_dir）"
```

---

### Task 1.2: jsonl 文件 sink processor

**Files:**
- Modify: `src/zd_agent/log.py`
- Test: `tests/test_log_jsonl_sink.py`

实现思路：写一个 structlog processor `_make_jsonl_sink(log_dir)`，它接收
`(logger, method_name, event_dict)`，把 event_dict 复制一份 `json.dumps` 追加到
`<log_dir>/events-<UTC date>.jsonl`，**原样返回 event_dict**（不消费，让后续
ConsoleRenderer 继续处理 stdout）。写文件任何异常都吞掉（不拖垮 agent）。
插在 processors 链里 ConsoleRenderer **之前**。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_log_jsonl_sink.py
import json
from datetime import datetime, timezone

from zd_agent.log import _make_jsonl_sink


def test_jsonl_sink_writes_line(tmp_path):
    sink = _make_jsonl_sink(str(tmp_path))
    event_dict = {"event": "agent_done", "ticket_id": 2709, "rounds": 7}
    out = sink(None, "info", event_dict)
    # processor 必须原样返回 event_dict（不消费）
    assert out == event_dict
    # 文件按当天 UTC 日期命名
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / f"events-{date}.jsonl"
    assert f.exists()
    line = json.loads(f.read_text().strip())
    assert line["event"] == "agent_done"
    assert line["ticket_id"] == 2709


def test_jsonl_sink_appends(tmp_path):
    sink = _make_jsonl_sink(str(tmp_path))
    sink(None, "info", {"event": "a", "ticket_id": 1})
    sink(None, "info", {"event": "b", "ticket_id": 2})
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = (tmp_path / f"events-{date}.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "a"
    assert json.loads(lines[1])["event"] == "b"


def test_jsonl_sink_swallows_write_error(tmp_path):
    # 不可写目录 → 不能抛异常（降级），仍返回 event_dict
    sink = _make_jsonl_sink("/nonexistent/cannot/create/here")
    out = sink(None, "info", {"event": "x", "ticket_id": 1})
    assert out == {"event": "x", "ticket_id": 1}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_log_jsonl_sink.py -v`
Expected: FAIL（`ImportError: cannot import name '_make_jsonl_sink'`）

- [ ] **Step 3: 实现**

在 `src/zd_agent/log.py` 顶部 import 区加 `import json`、`import os`、
`from datetime import datetime, timezone`、`from typing import Any`。
在 `setup_logging` 之前加：

```python
def _make_jsonl_sink(log_dir: str):
    """返回一个 structlog processor：把 event_dict 以 JSON 追加到
    <log_dir>/events-<UTC date>.jsonl，然后**原样返回 event_dict**（不消费，
    让后续 ConsoleRenderer 继续渲染 stdout）。写文件失败时静默降级。
    """
    def _sink(logger: Any, method_name: str, event_dict: dict) -> dict:
        try:
            os.makedirs(log_dir, exist_ok=True)
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = os.path.join(log_dir, f"events-{date}.jsonl")
            line = json.dumps(event_dict, ensure_ascii=False, default=str)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            # 日志 sink 绝不能拖垮主流程：写失败就只剩 stdout
            pass
        return event_dict

    return _sink
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_log_jsonl_sink.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/zd_agent/log.py tests/test_log_jsonl_sink.py
git commit -m "feat(log): jsonl 文件 sink processor（写文件副作用，不消费 event）"
```

---

### Task 1.3: 把 sink 接进 setup_logging

**Files:**
- Modify: `src/zd_agent/log.py`（`setup_logging` 签名 + processors 链）
- Modify: `src/zd_agent/entry/cli.py`（传 log_dir）
- Test: `tests/test_log_jsonl_sink.py`（加集成测试）

- [ ] **Step 1: 写失败测试**

在 `tests/test_log_jsonl_sink.py` 追加：

```python
import json as _json
from datetime import datetime as _dt, timezone as _tz

from zd_agent.log import setup_logging, get_logger


def test_setup_logging_with_jsonl_writes_file(tmp_path):
    setup_logging(json_output=False, log_level="INFO", jsonl_dir=str(tmp_path))
    log = get_logger(module="test")
    log.info("agent_done", ticket_id=999, rounds=5)
    date = _dt.now(_tz.utc).strftime("%Y-%m-%d")
    f = tmp_path / f"events-{date}.jsonl"
    assert f.exists()
    found = [
        _json.loads(l) for l in f.read_text().strip().splitlines()
        if _json.loads(l).get("ticket_id") == 999
    ]
    assert found and found[0]["event"] == "agent_done"


def test_setup_logging_jsonl_dir_none_no_file(tmp_path):
    # jsonl_dir=None → 不写文件（向后兼容）
    setup_logging(json_output=False, log_level="INFO", jsonl_dir=None)
    log = get_logger(module="test")
    log.info("x", ticket_id=1)
    assert not list(tmp_path.glob("events-*.jsonl"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_log_jsonl_sink.py::test_setup_logging_with_jsonl_writes_file -v`
Expected: FAIL（`setup_logging() got an unexpected keyword argument 'jsonl_dir'`）

- [ ] **Step 3: 实现**

改 `src/zd_agent/log.py` 的 `setup_logging` 签名加 `jsonl_dir: str | None = None`，
并在 `ConsoleRenderer`/`JSONRenderer` 这步**之前**插入 sink：

```python
def setup_logging(
    *,
    json_output: bool = False,
    log_level: str = "INFO",
    jsonl_dir: str | None = None,
) -> None:
    level = _LEVEL_MAP.get(log_level.upper(), logging.INFO)

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # jsonl 文件 sink：插在最终 renderer 之前，原样透传 event_dict
    if jsonl_dir:
        processors.append(_make_jsonl_sink(jsonl_dir))

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

改 `src/zd_agent/entry/cli.py` 的 `main` callback：

```python
@app.callback()
def main(
    json_log: bool = typer.Option(False, "--json-log", help="输出 JSON 格式日志"),
) -> None:
    """Zendesk 工单自动处理 Agent"""
    from zd_agent.config import EnvSettings, get_config
    env = EnvSettings()
    cfg = get_config()
    jsonl_dir = cfg.logging.log_dir if cfg.logging.jsonl_enabled else None
    setup_logging(
        json_output=json_log, log_level=env.zd_log_level, jsonl_dir=jsonl_dir,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_log_jsonl_sink.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 跑全量回归**

Run: `uv run pytest -q`
Expected: 全绿（之前 639 + 新增 ≈ 646 passed）

- [ ] **Step 6: 提交**

```bash
git add src/zd_agent/log.py src/zd_agent/entry/cli.py tests/test_log_jsonl_sink.py
git commit -m "feat(log): setup_logging 接入 jsonl_dir，cli 按 config 启用"
```

---

### Task 1.4: systemd 挂 logs 卷 + 部署验证

**Files:**
- Modify: `deploy/systemd/zd-agent.service`（ExecStart 的 docker run 加 `-v`）

- [ ] **Step 1: 改 systemd unit**

在 `deploy/systemd/zd-agent.service` 的 `docker run ...` 那段，与其它 `-v` 并列加一行：

```
    -v /opt/zd-agent/logs:/opt/zd-agent/logs \
```

- [ ] **Step 2: 提交并 push**

```bash
git add deploy/systemd/zd-agent.service
git commit -m "deploy: zd-agent 容器挂载 /opt/zd-agent/logs 卷（jsonl 落盘）"
git push origin main
```

- [ ] **Step 3: 服务器部署（按顺序，单条 ssh 串起来避免抖断）**

```bash
ssh root@180.76.243.249 'mkdir -p /opt/zd-agent/logs && cd /opt/zd-agent/code && git pull origin main && cp deploy/systemd/zd-agent.service /etc/systemd/system/zd-agent.service && systemctl daemon-reload && docker build --network host -f deploy/Dockerfile -t zd-agent:latest . > /tmp/b.log 2>&1 && echo BUILD_OK && systemctl restart zd-agent && sleep 6 && systemctl is-active zd-agent'
```

- [ ] **Step 4: 验证落盘**

```bash
ssh root@180.76.243.249 'ls -la /opt/zd-agent/logs/ && tail -3 /opt/zd-agent/logs/events-*.jsonl | python3 -c "import sys,json; [json.loads(l) for l in sys.stdin if l.strip()]; print(\"all lines valid JSON\")"'
```
Expected: 有 `events-<today>.jsonl`，每行合法 JSON

- [ ] **Step 5: 验证 stdout 未被改坏**

```bash
ssh root@180.76.243.249 'journalctl -u zd-agent --since "2 minutes ago" --no-pager | grep saas_app_started | tail -1'
```
Expected: 仍是带色控制台格式（不是 JSON）；服务 active

**Phase 1 Success Criteria:**
- [ ] `uv run pytest` 全绿
- [ ] `/opt/zd-agent/logs/events-<today>.jsonl` 有内容，每行合法 JSON
- [ ] journalctl 裸看仍带色（stdout 未变 JSON）
- [ ] 处理一个真实工单后，该 ticket_id 的 trigger_verdict / round_tool_calls / agent_done 都在 jsonl 里

---

## Phase 2: parser 解析层（纯函数，本地可测）

### Task 2.1: log-viewer 独立项目骨架

**Files:**
- Create: `log-viewer/pyproject.toml`
- Create: `log-viewer/__init__.py`
- Create: `log-viewer/tests/__init__.py`

- [ ] **Step 1: 建 pyproject**

```toml
# log-viewer/pyproject.toml
[project]
name = "zd-log-viewer"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.110", "uvicorn>=0.29"]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: 建空 __init__**

```bash
mkdir -p log-viewer/tests && touch log-viewer/__init__.py log-viewer/tests/__init__.py
```

- [ ] **Step 3: 提交**

```bash
git add log-viewer/pyproject.toml log-viewer/__init__.py log-viewer/tests/__init__.py
git commit -m "feat(log-viewer): 独立项目骨架"
```

---

### Task 2.2: read_events（读 jsonl + 坏行容错）

**Files:**
- Create: `log-viewer/parser.py`
- Create: `log-viewer/tests/test_parser.py`

- [ ] **Step 1: 写失败测试**

```python
# log-viewer/tests/test_parser.py
import json
from datetime import datetime, timezone

import parser as P


def _write_jsonl(tmp_path, rows):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / f"events-{date}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return f


def test_read_events_basic(tmp_path):
    _write_jsonl(tmp_path, [
        {"event": "a", "ticket_id": 1},
        {"event": "b", "ticket_id": 2},
    ])
    events = P.read_events(str(tmp_path), days=1)
    assert len(events) == 2
    assert {e["event"] for e in events} == {"a", "b"}


def test_read_events_skips_bad_lines(tmp_path):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / f"events-{date}.jsonl"
    f.write_text('{"event":"ok","ticket_id":1}\nNOT JSON GARBAGE\n{"event":"ok2","ticket_id":2}\n')
    events = P.read_events(str(tmp_path), days=1)
    assert len(events) == 2  # 坏行被跳过


def test_read_events_empty_dir(tmp_path):
    assert P.read_events(str(tmp_path), days=1) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd log-viewer && uv run --with pytest pytest tests/test_parser.py::test_read_events_basic -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'parser'` 或 AttributeError）

- [ ] **Step 3: 实现**

```python
# log-viewer/parser.py
"""读取 agent 落的 jsonl，聚合成工单列表 + 单工单时间线。纯函数，无 web 依赖。"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta, timezone


def read_events(log_dir: str, days: int = 7) -> list[dict]:
    """读最近 days 天的 events-*.jsonl，逐行 json.loads，坏行跳过。"""
    if not os.path.isdir(log_dir):
        return []
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    events: list[dict] = []
    for path in sorted(glob.glob(os.path.join(log_dir, "events-*.jsonl"))):
        base = os.path.basename(path)
        datestr = base.removeprefix("events-").removesuffix(".jsonl")
        try:
            fdate = datetime.strptime(datestr, "%Y-%m-%d").date()
        except ValueError:
            continue
        if fdate < cutoff:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # 坏行跳过
        except OSError:
            continue
    return events
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd log-viewer && uv run --with pytest pytest tests/test_parser.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add log-viewer/parser.py log-viewer/tests/test_parser.py
git commit -m "feat(log-viewer): read_events 读 jsonl + 坏行容错"
```

---

### Task 2.3: group_by_ticket + summarize_tickets + ticket_timeline

**Files:**
- Modify: `log-viewer/parser.py`
- Modify: `log-viewer/tests/test_parser.py`

- [ ] **Step 1: 写失败测试**

在 `log-viewer/tests/test_parser.py` 追加：

```python
def test_group_by_ticket():
    events = [
        {"event": "a", "ticket_id": 1},
        {"event": "b", "ticket_id": 2},
        {"event": "c", "ticket_id": 1},
        {"event": "startup"},  # 无 ticket_id → 丢弃
    ]
    grouped = P.group_by_ticket(events)
    assert set(grouped.keys()) == {1, 2}
    assert len(grouped[1]) == 2


def test_summarize_tickets():
    events = [
        {"event": "trigger_verdict", "ticket_id": 5, "verdict": "substantial_issue",
         "timestamp": "2026-05-29T04:00:00Z"},
        {"event": "ticket_classified", "ticket_id": 5, "ticket_type": "knowledge_base",
         "timestamp": "2026-05-29T04:00:05Z"},
        {"event": "agent_done", "ticket_id": 5, "rounds": 7, "total_tokens": 370297,
         "reply_sent": True, "status": "completed", "elapsed": 150.6,
         "timestamp": "2026-05-29T04:02:30Z"},
    ]
    summaries = P.summarize_tickets(P.group_by_ticket(events))
    assert len(summaries) == 1
    s = summaries[0]
    assert s["ticket_id"] == 5
    assert s["status"] == "completed"
    assert s["verdict"] == "substantial_issue"
    assert s["rounds"] == 7
    assert s["total_tokens"] == 370297
    assert s["reply_sent"] is True
    assert s["last_time"] == "2026-05-29T04:02:30Z"


def test_summarize_sorts_desc_by_last_time():
    events = [
        {"event": "agent_done", "ticket_id": 1, "timestamp": "2026-05-29T01:00:00Z"},
        {"event": "agent_done", "ticket_id": 2, "timestamp": "2026-05-29T03:00:00Z"},
        {"event": "agent_done", "ticket_id": 3, "timestamp": "2026-05-29T02:00:00Z"},
    ]
    summaries = P.summarize_tickets(P.group_by_ticket(events))
    assert [s["ticket_id"] for s in summaries] == [2, 3, 1]  # 时间倒序


def test_ticket_timeline_sorted_asc():
    events = [
        {"event": "agent_done", "ticket_id": 9, "timestamp": "2026-05-29T04:02:00Z"},
        {"event": "trigger_verdict", "ticket_id": 9, "timestamp": "2026-05-29T04:00:00Z"},
    ]
    tl = P.ticket_timeline(P.group_by_ticket(events), 9)
    assert [e["event"] for e in tl] == ["trigger_verdict", "agent_done"]


def test_summarize_missing_fields_no_crash():
    events = [{"event": "trigger_verdict", "ticket_id": 7, "timestamp": "2026-05-29T04:00:00Z"}]
    summaries = P.summarize_tickets(P.group_by_ticket(events))
    assert summaries[0]["ticket_id"] == 7
    assert summaries[0]["status"] == ""      # 没 agent_done → 空
    assert summaries[0]["rounds"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd log-viewer && uv run --with pytest pytest tests/test_parser.py -v`
Expected: FAIL（`AttributeError: module 'parser' has no attribute 'group_by_ticket'`）

- [ ] **Step 3: 实现**

在 `log-viewer/parser.py` 末尾追加：

```python
def group_by_ticket(events: list[dict]) -> dict[int, list[dict]]:
    """按 ticket_id 分组；无 ticket_id 的行（startup/poller 等）丢弃。"""
    grouped: dict[int, list[dict]] = {}
    for e in events:
        tid = e.get("ticket_id")
        if tid is None:
            continue
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(tid, []).append(e)
    return grouped


def _last_time(events: list[dict]) -> str:
    times = [e.get("timestamp", "") for e in events if e.get("timestamp")]
    return max(times) if times else ""


def summarize_tickets(grouped: dict[int, list[dict]]) -> list[dict]:
    """每工单聚合一行摘要，按 last_time 倒序。
    字段从 agent_done / trigger_verdict / ticket_classified 里取。
    """
    out: list[dict] = []
    for tid, events in grouped.items():
        done = next((e for e in reversed(events) if e.get("event") == "agent_done"), {})
        verdict_ev = next(
            (e for e in reversed(events) if e.get("event") == "trigger_verdict"), {}
        )
        classify_ev = next(
            (e for e in reversed(events) if e.get("event") == "ticket_classified"), {}
        )
        out.append({
            "ticket_id": tid,
            "status": done.get("status", ""),
            "verdict": verdict_ev.get("verdict", ""),
            "ticket_type": classify_ev.get("ticket_type", ""),
            "rounds": done.get("rounds"),
            "total_tokens": done.get("total_tokens"),
            "elapsed": done.get("elapsed"),
            "reply_sent": done.get("reply_sent"),
            "last_time": _last_time(events),
            "event_count": len(events),
        })
    out.sort(key=lambda s: s["last_time"], reverse=True)
    return out


def ticket_timeline(grouped: dict[int, list[dict]], ticket_id: int) -> list[dict]:
    """单工单全事件，按 timestamp 升序。"""
    events = grouped.get(int(ticket_id), [])
    return sorted(events, key=lambda e: e.get("timestamp", ""))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd log-viewer && uv run --with pytest pytest tests/test_parser.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add log-viewer/parser.py log-viewer/tests/test_parser.py
git commit -m "feat(log-viewer): group_by_ticket + summarize + timeline 聚合"
```

---

### Task 2.4: 真实样本 fixture 验证

**Files:**
- Create: `log-viewer/tests/fixtures/sample.jsonl`
- Modify: `log-viewer/tests/test_parser.py`

- [ ] **Step 1: 从服务器抓真实样本**

```bash
ssh root@180.76.243.249 'tail -100 /opt/zd-agent/logs/events-*.jsonl' > log-viewer/tests/fixtures/sample.jsonl
```
（若 Phase 1 刚部署样本少，可多等一两单后再抓；至少要含一个完整工单的
trigger_verdict + agent_done）

- [ ] **Step 2: 写基于真实样本的测试**

```python
# 追加到 test_parser.py
import os as _os

def test_real_sample_parses(tmp_path):
    fixture = _os.path.join(_os.path.dirname(__file__), "fixtures", "sample.jsonl")
    # 复制到 events-<today>.jsonl 让 read_events 能按日期匹配
    from datetime import datetime as _d, timezone as _t
    date = _d.now(_t.utc).strftime("%Y-%m-%d")
    (tmp_path / f"events-{date}.jsonl").write_text(open(fixture, encoding="utf-8").read())
    events = P.read_events(str(tmp_path), days=1)
    assert len(events) > 0
    grouped = P.group_by_ticket(events)
    assert len(grouped) >= 1
    summaries = P.summarize_tickets(grouped)
    # 至少有一个工单能聚合出 ticket_id
    assert all("ticket_id" in s for s in summaries)
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd log-viewer && uv run --with pytest pytest tests/test_parser.py -v`
Expected: PASS（9 passed）

- [ ] **Step 4: 提交**

```bash
git add log-viewer/tests/fixtures/sample.jsonl log-viewer/tests/test_parser.py
git commit -m "test(log-viewer): 真实日志样本 fixture 验证 parser"
```

**Phase 2 Success Criteria:**
- [ ] `cd log-viewer && pytest` 全绿（9 passed）
- [ ] 用真实样本聚合出的轮数/token 与手动 grep 一致

---

## Phase 3: FastAPI app + 前端

### Task 3.1: FastAPI 3 路由

**Files:**
- Create: `log-viewer/main.py`
- Create: `log-viewer/tests/test_api.py`

- [ ] **Step 1: 写失败测试**

```python
# log-viewer/tests/test_api.py
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient


def _setup(tmp_path, monkeypatch):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = [
        {"event": "trigger_verdict", "ticket_id": 42, "verdict": "substantial_issue",
         "timestamp": "2026-05-29T04:00:00Z"},
        {"event": "agent_done", "ticket_id": 42, "rounds": 7, "total_tokens": 370297,
         "status": "completed", "reply_sent": True, "timestamp": "2026-05-29T04:02:00Z"},
    ]
    (tmp_path / f"events-{date}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )
    monkeypatch.setenv("ZD_LOG_DIR", str(tmp_path))
    import importlib
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_api_tickets_list(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.get("/api/tickets")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["ticket_id"] == 42
    assert data[0]["rounds"] == 7


def test_api_ticket_detail(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.get("/api/tickets/42")
    assert r.status_code == 200
    tl = r.json()
    assert [e["event"] for e in tl] == ["trigger_verdict", "agent_done"]


def test_api_index_served(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    assert "工单" in r.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd log-viewer && uv run --with pytest --with fastapi --with httpx pytest tests/test_api.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'main'`）

- [ ] **Step 3: 实现**

```python
# log-viewer/main.py
"""log-viewer：只读 agent jsonl，提供工单列表 + 时间线。零认证（由 oauth2-proxy 兜）。"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

import parser as P

LOG_DIR = os.environ.get("ZD_LOG_DIR", "/opt/zd-agent/logs")
DAYS = int(os.environ.get("ZD_LOG_DAYS", "14"))
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(_STATIC, "index.html"))
```

注意：测试用 `ZD_LOG_DIR` 环境变量覆盖 LOG_DIR，所以 LOG_DIR 必须在 import 时读 env。
为支持 `index()` 测试，Step 4 之前需先建一个最小 `static/index.html`（含"工单"字样）。

```bash
mkdir -p log-viewer/static && printf '<!doctype html><meta charset=utf-8><title>工单日志</title><div id=app>工单日志</div>' > log-viewer/static/index.html
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd log-viewer && uv run --with pytest --with fastapi --with httpx pytest tests/test_api.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add log-viewer/main.py log-viewer/tests/test_api.py log-viewer/static/index.html
git commit -m "feat(log-viewer): FastAPI 3 路由（列表/详情/首页）"
```

---

### Task 3.2: 前端单文件（列表 + 详情）

**Files:**
- Modify: `log-viewer/static/index.html`（完整实现替换最小占位）

- [ ] **Step 1: 实现完整前端**

把 `log-viewer/static/index.html` 整文件替换为（原生 JS，两屏，fetch 相对路径）：

```html
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>工单日志</title>
<style>
  body { font: 14px/1.5 -apple-system,system-ui,sans-serif; margin: 0; background:#f7f8fa; color:#1a1a2e; }
  header { background:#1a1a2e; color:#fff; padding:12px 20px; font-weight:600; }
  #app { padding:20px; max-width:1100px; margin:0 auto; }
  table { width:100%; border-collapse:collapse; background:#fff; border-radius:8px; overflow:hidden; }
  th,td { padding:8px 12px; text-align:left; border-bottom:1px solid #eee; }
  th { background:#f0f1f5; font-size:12px; color:#666; }
  tr.row { cursor:pointer; } tr.row:hover { background:#f5f7ff; }
  .bad { color:#c0392b; font-weight:600; }
  .warn { color:#d68910; }
  input { padding:6px 10px; border:1px solid #ccc; border-radius:6px; margin-bottom:12px; width:200px; }
  .back { cursor:pointer; color:#3b5bdb; margin-bottom:12px; display:inline-block; }
  .ev { background:#fff; border-bottom:1px solid #eee; padding:6px 12px; font-family:ui-monospace,monospace; font-size:13px; }
  .ev .t { color:#888; } .ev.err { background:#fdecea; }
  .ev pre { display:none; margin:6px 0 0; padding:8px; background:#2d2d3a; color:#e0e0e0; border-radius:6px; overflow:auto; }
  .ev.open pre { display:block; }
</style>
</head>
<body>
<header>zd-agent 工单日志</header>
<div id="app">加载中…</div>
<script>
const ICON = {
  ticket_classified:"📋", trigger_verdict:"🎯", round_tool_calls:"🔧",
  public_reply_sent:"💬", internal_reply_auto_public_sent:"💬",
  compaction_triggered:"🗜️", agent_done:"✅",
};
function iconFor(ev){
  if(/timeout|failed|error/.test(ev)) return "⚠️";
  return ICON[ev] || "·";
}
function fmtTime(ts){ return ts ? ts.replace("T"," ").replace("Z","").slice(5,19) : ""; }

async function showList(){
  const app = document.getElementById("app");
  const rows = await (await fetch("api/tickets")).json();
  let html = `<input id=q placeholder="搜工单号…" oninput="filter()">
    <table><thead><tr><th>工单</th><th>状态</th><th>verdict</th><th>类型</th>
    <th>轮数</th><th>token</th><th>耗时</th><th>最后时间</th></tr></thead><tbody>`;
  for(const s of rows){
    const tokBad = s.total_tokens && s.total_tokens > 1000000 ? "bad" : "";
    const stBad = s.status === "failed" ? "bad" : "";
    const tok = s.total_tokens ? (s.total_tokens/1000).toFixed(0)+"K" : "-";
    const el = s.elapsed ? s.elapsed.toFixed(0)+"s" : "-";
    html += `<tr class=row data-id="${s.ticket_id}" onclick="showDetail(${s.ticket_id})">
      <td>#${s.ticket_id}</td><td class="${stBad}">${s.status||"-"}</td>
      <td>${s.verdict||"-"}</td><td>${s.ticket_type||"-"}</td>
      <td>${s.rounds??"-"}</td><td class="${tokBad}">${tok}</td>
      <td>${el}</td><td>${fmtTime(s.last_time)}</td></tr>`;
  }
  html += "</tbody></table>";
  app.innerHTML = html;
}
function filter(){
  const q = document.getElementById("q").value.trim();
  document.querySelectorAll("tr.row").forEach(tr=>{
    tr.style.display = !q || tr.dataset.id.includes(q) ? "" : "none";
  });
}
async function showDetail(id){
  const app = document.getElementById("app");
  const tl = await (await fetch("api/tickets/"+id)).json();
  let html = `<span class=back onclick="showList()">← 返回列表</span>
    <h3>工单 #${id} · ${tl.length} 条事件</h3>`;
  for(const e of tl){
    const ev = e.event||"";
    const err = /timeout|failed|error/.test(ev) ? "err" : "";
    const extra = Object.entries(e).filter(([k])=>
      !["event","timestamp","level","module","ticket_id"].includes(k))
      .map(([k,v])=>`${k}=${typeof v==="object"?JSON.stringify(v):v}`).join("  ");
    html += `<div class="ev ${err}" onclick="this.classList.toggle('open')">
      <span class=t>${fmtTime(e.timestamp)}</span> ${iconFor(ev)} <b>${ev}</b> ${extra}
      <pre>${JSON.stringify(e,null,2)}</pre></div>`;
  }
  app.innerHTML = html;
}
showList();
</script>
</body>
</html>
```

- [ ] **Step 2: 跑 API 测试确认没破坏（首页仍含"工单"）**

Run: `cd log-viewer && uv run --with pytest --with fastapi --with httpx pytest tests/test_api.py -v`
Expected: PASS（3 passed）

- [ ] **Step 3: 本地手测**

```bash
cd log-viewer && ZD_LOG_DIR=tests/fixtures_dir uv run --with fastapi --with uvicorn uvicorn main:app --port 8099
```
（先把一份 sample.jsonl 放成 `events-<today>.jsonl` 到某目录指给 ZD_LOG_DIR）
浏览器开 `http://localhost:8099/`，确认列表能显示、点进去看到时间线、tool_names 可见。

- [ ] **Step 4: 提交**

```bash
git add log-viewer/static/index.html
git commit -m "feat(log-viewer): 单文件前端（列表 + 事件时间线）"
```

**Phase 3 Success Criteria:**
- [ ] 本地浏览器看到工单列表，点进详情看到时间线，tool_names 可见
- [ ] API 测试通过

---

## Phase 4: 部署 systemd + nginx 反代（暂无认证）

### Task 4.1: systemd unit

**Files:**
- Create: `deploy/systemd/zd-log-viewer.service`

- [ ] **Step 1: 写 unit**

```ini
# deploy/systemd/zd-log-viewer.service
[Unit]
Description=zd-agent log viewer (read-only jsonl web UI)
After=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=10
WorkingDirectory=/opt/zd-agent/log-viewer
Environment=ZD_LOG_DIR=/opt/zd-agent/logs
Environment=ZD_LOG_DAYS=14
ExecStart=/opt/zd-agent/log-viewer/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8081
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: 提交 + push**

```bash
git add deploy/systemd/zd-log-viewer.service
git commit -m "deploy: zd-log-viewer systemd unit"
git push origin main
```

- [ ] **Step 3: 服务器建 venv + 装服务**

```bash
ssh root@180.76.243.249 'cd /opt/zd-agent/code && git pull origin main && cp -r log-viewer /opt/zd-agent/log-viewer && cd /opt/zd-agent/log-viewer && python3 -m venv .venv && .venv/bin/pip install -q fastapi uvicorn && cp /opt/zd-agent/code/deploy/systemd/zd-log-viewer.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now zd-log-viewer && sleep 3 && systemctl is-active zd-log-viewer'
```
Expected: `active`

- [ ] **Step 4: 本地端口验证**

```bash
ssh root@180.76.243.249 'curl -s http://127.0.0.1:8081/api/tickets | head -c 300'
```
Expected: 返回工单 JSON 数组

---

### Task 4.2: nginx 反代 /logs/（暂无认证）

**Files:**
- Modify: `deploy/nginx/zd-agent.conf`

- [ ] **Step 1: 加 location**

在 `deploy/nginx/zd-agent.conf` 的 443 server 块里加：

```nginx
    # log-viewer（Phase 5 会在前面叠 oauth2-proxy 认证）
    location /logs/ {
        proxy_pass http://127.0.0.1:8081/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
```

- [ ] **Step 2: 提交 + push + 部署 nginx**

```bash
git add deploy/nginx/zd-agent.conf
git commit -m "deploy: nginx 反代 /logs/ → log-viewer 8081"
git push origin main
ssh root@180.76.243.249 'cd /opt/zd-agent/code && git pull origin main && cp deploy/nginx/zd-agent.conf /etc/nginx/conf.d/zd-agent.conf && nginx -t && systemctl reload nginx'
```

- [ ] **Step 3: 公网验证**

```bash
curl -s https://zendesk.smlershou.top/logs/api/tickets | head -c 300
```
Expected: 工单 JSON（注意：此时还没认证，是临时的，Phase 5 立即补上）

**Phase 4 Success Criteria:**
- [ ] `systemctl is-active zd-log-viewer` = active
- [ ] `/logs/` 与 `/logs/api/tickets` 公网可达
- [ ] 主 agent 仍 active（不受影响）

⚠️ **完成 Phase 4 后尽快做 Phase 5**——此窗口 `/logs/` 裸奔含 PII，不要久留。

---

## Phase 5: oauth2-proxy + Google 登录（限 @dify.ai）

### Task 5.1: 用户建 Google OAuth client（用户自做，不由 AI 代做）

- [ ] **Step 1（用户操作）：** 去 https://console.cloud.google.com →
  APIs & Services → Credentials → Create OAuth client ID → 类型 **Web application**
- [ ] **Step 2（用户操作）：** Authorized redirect URIs 填精确值：
  `https://zendesk.smlershou.top/oauth2/callback`
- [ ] **Step 3（用户操作）：** 记下 **Client ID** 和 **Client Secret**（secret 用户自己保管）

### Task 5.2: 装 oauth2-proxy + systemd

**Files:**
- Create: `deploy/systemd/oauth2-proxy.service`

- [ ] **Step 1: 生成 cookie secret（提供命令，输出由用户保管）**

```bash
python3 -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

- [ ] **Step 2: 写 unit（client id/secret/cookie secret 用占位，用户填实际值）**

```ini
# deploy/systemd/oauth2-proxy.service
[Unit]
Description=oauth2-proxy for zd-agent log viewer
After=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=10
ExecStart=/usr/local/bin/oauth2-proxy \
  --provider=google \
  --email-domain=dify.ai \
  --client-id=__GOOGLE_CLIENT_ID__ \
  --client-secret=__GOOGLE_CLIENT_SECRET__ \
  --cookie-secret=__COOKIE_SECRET__ \
  --cookie-secure=true \
  --http-address=127.0.0.1:4180 \
  --reverse-proxy=true \
  --upstream=http://127.0.0.1:8081/ \
  --redirect-url=https://zendesk.smlershou.top/oauth2/callback \
  --proxy-prefix=/oauth2 \
  --whitelist-domain=zendesk.smlershou.top
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: 提交 + push**

```bash
git add deploy/systemd/oauth2-proxy.service
git commit -m "deploy: oauth2-proxy systemd unit（Google 登录限 @dify.ai）"
git push origin main
```

- [ ] **Step 4: 服务器装二进制 + 配置（用户填三个密钥后执行）**

```bash
ssh root@180.76.243.249 'cd /tmp && curl -L -o o2p.tar.gz https://github.com/oauth2-proxy/oauth2-proxy/releases/download/v7.6.0/oauth2-proxy-v7.6.0.linux-amd64.tar.gz && tar xzf o2p.tar.gz && cp oauth2-proxy-*/oauth2-proxy /usr/local/bin/ && oauth2-proxy --version'
```
然后把 unit 里三个 `__...__` 占位替换成真实值（用户提供），`systemctl daemon-reload && systemctl enable --now oauth2-proxy && systemctl is-active oauth2-proxy`。

### Task 5.3: nginx 叠 auth_request

**Files:**
- Modify: `deploy/nginx/zd-agent.conf`

- [ ] **Step 1: 改 nginx —— /logs/ 加认证，/oauth2/ 走 proxy**

把 Phase 4 的 `location /logs/` 块改成下面这套（auth_request + oauth2 端点）：

```nginx
    # oauth2-proxy 认证端点
    location /oauth2/ {
        proxy_pass http://127.0.0.1:4180;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header X-Auth-Request-Redirect $request_uri;
    }
    location = /oauth2/auth {
        proxy_pass http://127.0.0.1:4180;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Content-Length "";
        proxy_pass_request_body off;
    }
    # log-viewer：先问 oauth2-proxy 认证，未登录跳 Google
    location /logs/ {
        auth_request /oauth2/auth;
        error_page 401 = /oauth2/sign_in;
        proxy_pass http://127.0.0.1:8081/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
```

- [ ] **Step 2: 提交 + push + 部署**

```bash
git add deploy/nginx/zd-agent.conf
git commit -m "deploy: nginx /logs/ 叠 oauth2 auth_request（Google 登录）"
git push origin main
ssh root@180.76.243.249 'cd /opt/zd-agent/code && git pull origin main && cp deploy/nginx/zd-agent.conf /etc/nginx/conf.d/zd-agent.conf && nginx -t && systemctl reload nginx'
```

- [ ] **Step 3: RUNBOOK 补段**

在 `deploy/RUNBOOK.md` 加 "## 工单日志 Web 界面" 段：组件（log-viewer 8081 +
oauth2-proxy 4180）、更新流程（改 log-viewer 代码 → `cp -r` + restart）、
Google client 创建步骤、三个密钥位置。提交。

- [ ] **Step 4: 验收登录流**

浏览器开 `https://zendesk.smlershou.top/logs/`：
1. 未登录 → 跳 Google 登录页
2. 用非 @dify.ai 账号 → 被拒
3. 用 @dify.ai 账号 → 进入，看到工单列表

**Phase 5 Success Criteria:**
- [ ] 未登录访问 `/logs/` → 跳 Google
- [ ] 非 @dify.ai 邮箱被拒
- [ ] @dify.ai 邮箱登录后看到日志界面
- [ ] 主 agent + log-viewer 均 active

---

## Testing Strategy

- Phase 1：主仓 `uv run pytest`（jsonl sink 单测 + 全量回归不破坏 639）
- Phase 2-3：`cd log-viewer && pytest`（parser 纯函数 + FastAPI TestClient），独立 pyproject 隔离
- Phase 4-5：部署后 curl / 浏览器端到端（基础设施层无单测）

## Risks & Mitigations

| 风险 | 缓解 |
|---|---|
| jsonl sink 写失败拖垮 agent | sink try/except 吞异常降级；Task 1.2 有失败路径测试 |
| 容器内 jsonl 路径与宿主挂载不一致 | Task 1.4 systemd 卷挂载一致 + 部署即验证落盘 |
| Phase 4 裸奔窗口泄露 PII | Phase 4→5 尽快连做；计划已标红警告 |
| Google redirect URI 配错 | 计划写死精确 URI；Task 5.3 Step 4 明确测登录流 |
| log-viewer 占内存影响 2G 机 | 纯只读、按需扫描、uvicorn 单 worker（默认） |
| oauth2-proxy 配错锁死/裸奔 | Phase 4/5 分离：先验链路再叠认证，各自独立验收 |
```
