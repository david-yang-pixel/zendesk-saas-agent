# Agent 日志系统设计

## 概述

为 zendesk-ai-auto 的 agent 运行记录设计持久化日志系统，数据存入 zendesk-hc-client 的 PostgreSQL，通过 hc-client 前端查看。

## 架构

```
zendesk-ai-auto                            zendesk-hc-client
┌──────────────────┐     HTTP POST        ┌──────────────────────┐
│  tool_loop.py    │ ───────────────────> │ POST /api/agent/logs │
│  消费 agent_loop  │   事件驱动推送        │   写入端点            │
│  事件时同步上报    │                      └──────────┬───────────┘
└──────────────────┘                                  │ ORM 写入
                                                      ▼
                                           ┌──────────────────────┐
                                           │     PostgreSQL       │
                                           │  agent_runs          │
                                           │  agent_rounds        │
                                           │  agent_tool_calls    │
                                           └──────────┬───────────┘
                                                      │ ORM 读取
                                                      ▼
                                           ┌──────────────────────┐
                                           │ GET /api/agent/logs  │
                                           │   查询端点            │──> 前端页面
                                           └──────────────────────┘
```

## 认证方式

日志推送端点使用内部 API Key 认证，不走飞书 OAuth。

- hc-client `.env` 配置 `AGENT_INNER_API_KEY`
- ai-auto `.env` 配置 `HC_CLIENT_URL` 和 `HC_CLIENT_API_KEY`
- 请求头 `X-Agent-Key: <key>`
- 日志端点不加入 `protected` 路由组（不需要 `require_current_user`）

## 数据库设计

三层结构：Run → Round → ToolCall

### agent_runs（概览层）

每次 agent 处理一张工单就是一条记录。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PK | |
| ticket_id | BIGINT NOT NULL | 工单号 |
| session_id | VARCHAR(32) | agent 内部会话 ID |
| status | VARCHAR(20) | running / completed / failed / timeout |
| trigger_type | VARCHAR(20) | webhook / scheduler / cli |
| model | VARCHAR(100) | 主 LLM 模型名 |
| started_at | TIMESTAMPTZ | 开始时间 |
| finished_at | TIMESTAMPTZ | 结束时间 |
| duration_ms | INTEGER | 总耗时 |
| total_rounds | INTEGER | 总轮次 |
| total_input_tokens | INTEGER | 总输入 token |
| total_output_tokens | INTEGER | 总输出 token |
| error_message | TEXT | 错误信息（仅失败时） |
| created_at | TIMESTAMPTZ | 记录创建时间 |
| updated_at | TIMESTAMPTZ | 记录更新时间 |

### agent_rounds（轮次层）

每一轮 LLM 调用一条记录，无论是否触发工具。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PK | |
| run_id | INTEGER FK → agent_runs.id | |
| round | INTEGER | 第几轮 |
| model | VARCHAR(100) | 本轮使用的模型 |
| input_tokens | INTEGER | 本轮输入 token |
| output_tokens | INTEGER | 本轮输出 token |
| duration_ms | INTEGER | LLM 调用耗时 |
| has_tool_calls | BOOLEAN | 本轮是否触发了工具 |
| tool_call_count | INTEGER | 本轮工具调用数量 |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### agent_tool_calls（工具调用层）

每次工具调用一条记录。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PK | |
| run_id | INTEGER FK → agent_runs.id | |
| round_id | INTEGER FK → agent_rounds.id | |
| tool_name | VARCHAR(100) | 工具名称 |
| arguments | JSONB | 调用参数 |
| result_preview | TEXT | 结果预览（截取前 500 字符） |
| success | BOOLEAN | 是否成功 |
| error_message | TEXT | 错误信息 |
| duration_ms | INTEGER | 工具执行耗时 |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### 索引

- `agent_runs`: `ticket_id`, `status`, `created_at`
- `agent_rounds`: `run_id`
- `agent_tool_calls`: `run_id`, `round_id`

## ai-auto 侧改动

### 新增文件

**`src/zd_agent/runtime/log_reporter.py`**

异步 HTTP 日志上报器。职责：

1. 初始化时从配置读取 `HC_CLIENT_URL` 和 `HC_CLIENT_API_KEY`
2. 如果未配置则静默跳过（不影响 agent 运行）
3. 所有上报操作 fire-and-forget，异常只打 warning 不抛出

核心方法：

```python
class LogReporter:
    async def report_run_start(session, trigger_type, model) -> int | None:
        """POST /api/agent/logs/runs → 返回远程 run_id"""

    async def report_round(run_id, event: LoopProgress, model) -> int | None:
        """POST /api/agent/logs/runs/{run_id}/rounds → 返回远程 round_id"""

    async def report_tool_call(run_id, round_id, event: ToolExecuted):
        """POST /api/agent/logs/runs/{run_id}/rounds/{round_id}/tool-calls"""

    async def report_run_finish(run_id, event: LoopFinished):
        """PUT /api/agent/logs/runs/{run_id}"""
```

使用 aiohttp（项目已有依赖），共享 ClientSession 避免反复创建连接。

### 修改文件

**`src/zd_agent/runtime/tool_loop.py`**

在现有 `match event` 分支中，增加对 `LogReporter` 的调用：

- `LoopProgress` → `report_round`，获得 `round_id`
- `ToolExecuted` → `report_tool_call`（使用当前轮的 `round_id`）
- `LoopFinished` → `report_run_finish`

在 `run_tool_loop` 开头调用 `report_run_start` 获得 `run_id`。

插入点选在 `tool_loop.py` 而非 `agent_loop.py`，原因：
- 侵入最小，`agent_loop` 保持纯事件生成器不变
- 与现有 Rich 输出逻辑同位置，容易理解
- `tool_loop` 已经是事件消费层，职责匹配

**`src/zd_agent/config.py`**

`EnvSettings` 新增：

```python
hc_client_url: str = ""          # 如 http://localhost:8000
hc_client_api_key: str = ""      # 对应 hc-client 的 AGENT_INNER_API_KEY
```

## hc-client 侧改动

### 新增文件

| 文件 | 内容 |
|------|------|
| `api/models/agent.py` | ORM 模型：AgentRun, AgentRound, AgentToolCall |
| `api/schemas/agent.py` | Pydantic 请求/响应 schema |
| `api/routers/agent_logs.py` | 写入 + 查询端点 |
| `api/dependencies/agent_key.py` | API Key 认证依赖 |

### models/agent.py

遵循现有 `models/auth.py` 风格：
- SQLAlchemy 2.0 `Mapped` + `mapped_column`
- `DateTime(timezone=True)` + `server_default=func.now()`
- `updated_at` 带 `onupdate=func.now()`
- 外键 `ondelete="CASCADE"`
- `relationship` + `back_populates`

### dependencies/agent_key.py

```python
async def require_agent_key(request: Request, settings = Depends(get_settings)):
    key = request.headers.get("X-Agent-Key")
    if not key or key != settings.agent_inner_api_key:
        raise HTTPException(status_code=401, detail="Invalid agent key")
```

### routers/agent_logs.py

**写入端点**（供 ai-auto 调用，`require_agent_key` 认证）：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agent/logs/runs` | 创建 run |
| PUT | `/agent/logs/runs/{id}` | 更新 run（完成/失败） |
| POST | `/agent/logs/runs/{id}/rounds` | 创建 round |
| POST | `/agent/logs/runs/{id}/rounds/{round_id}/tool-calls` | 创建 tool_call |

**查询端点**（供前端调用，`require_current_user` 认证）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/agent/logs/runs` | 列表（分页、按 ticket_id/status 筛选） |
| GET | `/agent/logs/runs/{id}` | 详情（含 rounds 和 tool_calls） |
| GET | `/agent/logs/stats` | 统计（总处理数、成功率、平均耗时等） |

### main.py 路由挂载

```python
# 日志写入端点：内部 API Key 认证
app.include_router(agent_logs.write_router, prefix="/api", dependencies=[Depends(require_agent_key)])
# 日志查询端点：用户认证
app.include_router(agent_logs.read_router, prefix="/api", dependencies=protected)
```

### config.py

新增配置项：

```python
agent_inner_api_key: str = ""
```

### .env / .env.example

新增：

```
AGENT_INNER_API_KEY=<生成一个随机 key>
```

### Alembic 迁移

新增一个迁移文件 `20260401_0001_add_agent_log_tables.py`，创建三张表。

遵循现有迁移命名风格：`日期_序号_描述`。

## 前端页面（后续）

在 hc-client 前端新增 `/tickets/agent-logs` 页面：

- 列表页：表格展示 agent_runs（工单号、状态徽标、模型、轮次、token 数、耗时、时间）
- 详情页（点击展开或跳转）：展示该次运行的所有轮次和工具调用时间线
- 统计卡片：今日处理数、成功率、平均耗时、总 token 消耗

前端实现可作为独立任务，后端 API 就绪后再做。

## 容错设计

1. **ai-auto 上报失败不影响主流程**：所有 HTTP 调用包在 try-except 中，失败只打 warning
2. **未配置则静默跳过**：`HC_CLIENT_URL` 为空时 `LogReporter` 的所有方法直接 return
3. **网络抖动容忍**：HTTP 请求设 3s 超时，不做重试（日志丢失可接受）
4. **hc-client 服务不可用**：ai-auto 照常工作，只是没有日志写入

## 实现顺序

1. hc-client：数据库模型 + Alembic 迁移 + 跑迁移
2. hc-client：写入端点 + API Key 认证
3. hc-client：查询端点
4. ai-auto：LogReporter + config + tool_loop 集成
5. 联调测试
6. hc-client 前端页面
