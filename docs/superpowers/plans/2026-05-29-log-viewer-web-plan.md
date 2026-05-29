# 工单日志 Web 查看系统 — 实现计划

## Overview

给 zd-agent 做一个按工单查看处理日志的 Web 界面：agent 落 jsonl 持久化 →
独立 FastAPI 服务读取并提供列表 + 事件级时间线 → oauth2-proxy + Google 登录
（限 @dify.ai）。bottom-up：先让日志落盘，再建解析层，再 API，再前端，最后认证。

**Design doc:** `docs/superpowers/specs/2026-05-29-log-viewer-web-design.md`

## Current Status

**Active Phase:** Not started

| Phase | Status | Notes |
|-------|--------|-------|
| 1. agent jsonl sink | Not Started | 改 log.py，重部署验证落盘 |
| 2. parser 解析层 + 测试 | Not Started | 纯函数，jsonl → 聚合，本地可测 |
| 3. FastAPI app + 前端 | Not Started | 3 路由 + 单文件 HTML |
| 4. 部署 systemd + nginx 反代 | Not Started | 先不加认证，curl 验证 |
| 5. oauth2-proxy + Google 登录 | Not Started | 用户建 OAuth client，验收登录 |

## Prerequisites

- 服务器 `/opt/zd-agent/` 可写、nginx + systemd 已在用（现状满足）
- 主仓已在 main 分支，本地测试基线 639 passed
- 阶段 5 需用户本人在 Google Cloud Console 建 OAuth client

## Implementation Phases

### Phase 1: agent jsonl sink（日志落盘）

**Goal:** agent 每条日志在保持现有 stdout 控制台输出的同时，额外写一份 JSON 行到
`/opt/zd-agent/logs/events-<date>.jsonl`。这是后面所有阶段的数据源。

**Files:**
- `src/zd_agent/log.py` - `setup_logging` 增加文件 sink；stdout ConsoleRenderer 不动
- `src/zd_agent/config.py` - 加 `LoggingConfig`（log_dir 路径，默认 `/opt/zd-agent/logs`）
- `tests/test_log_jsonl_sink.py` - 新测试
- `deploy/systemd/zd-agent.service` - 容器加 `-v /opt/zd-agent/logs:/opt/zd-agent/logs`

**实现要点：**
- structlog 已有 JSON renderer 能力。做法：用一个独立的 logger factory / 第二条
  processor 链把 JSON 行写到按天文件。优先用 Python 标准 `logging` 的
  `TimedRotatingFileHandler`（按天切）+ structlog 的 `ProcessorFormatter`，避免自己管文件句柄/轮转。
- 关键不变量：**stdout 仍是带色 ConsoleRenderer**（裸看 journalctl 不变），只新增文件 JSON。
- 写文件失败（磁盘满/权限）不能拖垮 agent → sink 异常吞掉 + 降级（只 stdout）。

**Tasks:**
1. [ ] `config.py` 加 `LoggingConfig(log_dir, jsonl_enabled=True)`，挂到 AppConfig
2. [ ] `log.py` `setup_logging` 加 jsonl 文件 handler（TimedRotatingFileHandler，按天，
      文件名 `events-YYYY-MM-DD.jsonl`），用 structlog ProcessorFormatter 输出 JSON
3. [ ] 确保每行含：timestamp / level / event / module / 及业务字段（ticket_id 等）
4. [ ] 文件写失败时 try/except 降级，不抛到主流程
5. [ ] 测试：调用 logger 后断言 jsonl 文件存在、每行可 json.loads、含 ticket_id 字段
6. [ ] 测试：jsonl_enabled=False 时不写文件
7. [ ] 改 systemd unit 加 logs 卷挂载

**Success Criteria:**
- [ ] 本地 `uv run pytest` 全绿（含新测试）
- [ ] 部署后 `/opt/zd-agent/logs/events-<today>.jsonl` 有内容，每行合法 JSON
- [ ] `journalctl -u zd-agent` 裸看仍是带色文本（未被改成 JSON）
- [ ] 处理一个真实工单后，该 ticket_id 的 verdict/round_tool_calls/agent_done 都在 jsonl 里

### Phase 2: parser 解析层 + 测试（纯函数，可本地测）

**Goal:** 一个独立模块，输入 jsonl 文件路径集合，输出「工单列表聚合」和「单工单事件流」。
纯函数、零 web 依赖，可完全本地测。

**Files:**
- `log-viewer/parser.py` - 解析 + 聚合函数
- `log-viewer/tests/test_parser.py` - 单测
- `log-viewer/tests/fixtures/sample.jsonl` - 真实日志样本（从服务器抓几单脱敏/原样）
- `log-viewer/pyproject.toml` - 独立依赖（fastapi, uvicorn, pytest）

**实现要点（函数签名）：**
- `read_events(log_dir, days=N) -> list[dict]`：读最近 N 天 jsonl，逐行 json.loads，
  坏行（非 JSON）跳过容错
- `group_by_ticket(events) -> dict[int, list[dict]]`：按 ticket_id 分组，无 ticket_id 行丢弃
- `summarize_tickets(grouped) -> list[TicketSummary]`：每工单聚合出
  状态/verdict/总轮数/token/耗时/reply_sent/最后时间，按时间倒序
- `ticket_timeline(grouped, ticket_id) -> list[dict]`：单工单事件按时间升序

**Tasks:**
1. [ ] `log-viewer/pyproject.toml` 独立依赖
2. [ ] `parser.read_events` + 坏行容错
3. [ ] `parser.group_by_ticket`
4. [ ] `parser.summarize_tickets`（从 agent_done / trigger_verdict / ticket_classified 提字段）
5. [ ] `parser.ticket_timeline`
6. [ ] 抓真实样本存 fixtures/sample.jsonl
7. [ ] 测试：分组正确、聚合字段对、坏行跳过、空目录返回空、缺字段不崩

**Success Criteria:**
- [ ] `cd log-viewer && pytest` 全绿
- [ ] 用真实样本能聚合出和我们手动 grep 一致的轮数/token

### Phase 3: FastAPI app + 前端（端到端可看）

**Goal:** 起一个本地 FastAPI，浏览器能看到列表 + 点进时间线。先不接认证。

**Files:**
- `log-viewer/main.py` - FastAPI，3 路由（调用 parser）
- `log-viewer/static/index.html` - 单文件前端（原生 JS + fetch）

**实现要点：**
- 路由：`GET /api/tickets`、`GET /api/tickets/{id}`、`GET /`（返回 index.html）
- 路由仅调 parser 纯函数 + 返回 JSON，无业务逻辑
- 前端：两屏（列表 / 详情），原生 JS，按 design doc 的图标/高亮规则渲染
- 注意路由前缀：本地直接 `/`，线上挂 `/logs/` 由 nginx rewrite，前端 fetch 用相对路径

**Tasks:**
1. [ ] `main.py` 3 路由，读 config 的 log_dir
2. [ ] `static/index.html` 列表屏（表格 + 搜索 + 高亮 + 倒序）
3. [ ] 详情屏（时间线 + 图标 + 错误红 + tool_names + 原始 JSON 可展开）
4. [ ] 本地 `uvicorn main:app` 跑通，浏览器手测列表→详情
5. [ ] API 测试（FastAPI TestClient）：两个 API 返回结构正确

**Success Criteria:**
- [ ] 本地浏览器打开能看到工单列表
- [ ] 点工单进详情看到事件时间线，tool_names 可见
- [ ] API 测试通过

### Phase 4: 部署 systemd + nginx 反代（线上可达，暂无认证）

**Goal:** log-viewer 跑在服务器 8081，nginx `/logs/` 反代过去，curl 能通（先不加 Google 认证，
确认链路）。

**Files:**
- `deploy/systemd/zd-log-viewer.service` - 新 unit（宿主 venv 跑 uvicorn :8081，只读挂 logs）
- `deploy/nginx/zd-agent.conf` - 加 `location /logs/` 反代 8081
- `deploy/RUNBOOK.md` - 补 log-viewer 部署/运维段

**Tasks:**
1. [ ] 写 systemd unit（宿主 venv，`uvicorn main:app --port 8081`）
2. [ ] 服务器建 venv 装 log-viewer 依赖
3. [ ] nginx 加 location（先无认证），reload
4. [ ] `curl https://zendesk.smlershou.top/logs/api/tickets` 返回 JSON
5. [ ] RUNBOOK 补段

**Success Criteria:**
- [ ] `systemctl is-active zd-log-viewer` = active
- [ ] curl `/logs/` 返回页面、`/logs/api/tickets` 返回工单列表
- [ ] 主 agent 不受影响（仍 active）

### Phase 5: oauth2-proxy + Google 登录（限 @dify.ai）

**Goal:** `/logs/` 加 Google 登录，只有 @dify.ai 邮箱能进。

**Files:**
- `deploy/systemd/oauth2-proxy.service` - 新 unit
- `deploy/nginx/zd-agent.conf` - 加 `/oauth2/` + `/logs/` 的 auth_request
- `deploy/RUNBOOK.md` - 补 oauth2-proxy 配置 + Google client 创建步骤

**用户需自做（计划里写清步骤，AI 不代做）：**
- Google Cloud Console 建 OAuth 2.0 client（Web）
- redirect URI = `https://zendesk.smlershou.top/oauth2/callback`
- 填 client ID + secret 到配置（secret 用户填）

**Tasks:**
1. [ ] 服务器装 oauth2-proxy 二进制
2. [ ] 写 oauth2-proxy.service（`--provider=google --email-domain=dify.ai --upstream=http://127.0.0.1:8081 --http-address=:4180` + cookie secret）
3. [ ] 生成 cookie secret（提供命令）
4. [ ] nginx 加 `/oauth2/` location + `/logs/` 的 `auth_request /oauth2/auth`，未登录跳 `/oauth2/start`
5. [ ] RUNBOOK 写全 Google client 创建步骤 + 配置
6. [ ] 用户建 client、填凭据
7. [ ] 验收：未登录跳 Google；非 dify.ai 邮箱被拒；dify.ai 邮箱进得去看日志

**Success Criteria:**
- [ ] 浏览器访问 `/logs/` 未登录 → 跳 Google 登录
- [ ] 非 @dify.ai 账号被拒
- [ ] @dify.ai 账号登录后看到工单日志界面
- [ ] 主 agent + log-viewer 均不受影响

## Testing Strategy

- **Phase 1**：主仓 pytest（jsonl sink 单测），不破坏现有 639 测试
- **Phase 2**：log-viewer 独立 pytest（parser 纯函数，真实样本 fixture）
- **Phase 3**：FastAPI TestClient API 测试 + 手动浏览器验证
- **Phase 4-5**：部署后 curl / 浏览器端到端验证（基础设施层，无单测）
- log-viewer 独立 pyproject，测试与主仓隔离

## Risks & Mitigations

| 风险 | 缓解 |
|---|---|
| jsonl sink 写失败拖垮 agent | sink 异常吞掉降级，只 stdout；测试覆盖失败路径 |
| 容器内 jsonl 路径与宿主挂载不一致 | systemd 卷挂载 `/opt/zd-agent/logs` 一致；Phase 1 部署即验证落盘 |
| journald 旧日志 vs 新 jsonl 起点不同 | jsonl 只从 Phase 1 部署后开始有；历史日志仍可 journalctl（不强求迁移） |
| oauth2-proxy 配错导致 /logs/ 锁死或裸奔 | Phase 4/5 分离：先无认证验证链路，再叠认证；每步独立验收 |
| Google client redirect URI 配错 | RUNBOOK 写死精确 URI；验收步骤明确测登录流 |
| log-viewer 占内存影响 2G 小机 | 纯只读、按需扫描、无常驻索引；uvicorn 单 worker |

## 部署节奏

每个 Phase 独立可验收。Phase 1 要重部署 agent（影响线上）；Phase 2-3 纯本地；
Phase 4-5 部署新服务（不动 agent）。每个 Phase 完成 → 验收 → 再下一个。
