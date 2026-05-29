# 工单日志 Web 查看系统 — 设计文档

日期：2026-05-29
状态：设计已确认，待写实现计划

## 目标

给 zd-agent 做一个 Web 界面，运维人（yonggang）能在浏览器上**按工单查看每个工单的处理日志**：
列出所有处理过的工单，点进去看该工单从触发判定 → 分类 → 每轮工具调用 → 查询 →
最终回复的**事件级时间线**。

## 需求（已确认）

| 维度 | 决定 |
|---|---|
| 核心视图 | 工单列表 + 点进详情 |
| 详情粒度 | 事件级时间线（每条结构化日志都列） |
| 数据源 | agent 落一份 jsonl 文件，Web 读文件（不再实时调 journalctl） |
| 解析方式 | agent 输出 JSON 日志（仅文件 sink 用 JSON，stdout 保持带色控制台格式不变） |
| 技术栈 | log-viewer = Python + FastAPI，独立进程 |
| 服务隔离 | 独立 systemd，与主 agent 解耦（互不影响） |
| 保留期 | agent 另写 jsonl 持久化（按天分文件），不受 journald 轮转限制 |
| 访问控制 | 公网 `/logs/` + **Google 登录（oauth2-proxy），限 @dify.ai 域名** |

## 架构总览

```
┌─────────────────┐   写         ┌──────────────────────────┐
│  zd-agent       │ ─────────→   │ /opt/zd-agent/logs/        │
│ (主服务, 8080)  │  JSON 行      │   events-YYYY-MM-DD.jsonl  │  ← 按天分文件
└─────────────────┘              └──────────────────────────┘
                                            │ 读(只读)
                                            ▼
浏览器 ──→ nginx /logs/ ──auth_request──→ oauth2-proxy (:4180)
              │                              │ 未登录 → 跳 Google 登录
              │                              │ 已登录且 @dify.ai → 放行
              ▼ (认证通过)
         log-viewer (FastAPI, :8081)  ← 纯只读，零认证代码
```

四块新增组件，互相解耦：

1. **agent jsonl sink**（改 `src/zd_agent/log.py`）：现有 stdout ConsoleRenderer 保持不变，
   **额外**追加一个文件 sink，把每条日志以 JSON 格式写到
   `/opt/zd-agent/logs/events-<date>.jsonl`。只加文件输出，裸看 journalctl 不受影响。
2. **log-viewer 服务**（新目录 `log-viewer/`，FastAPI）：只读 jsonl 文件，按 `ticket_id`
   分组聚合，提供列表/详情 API + 单文件 HTML 前端。独立进程，崩了不影响 agent。
3. **oauth2-proxy**（独立二进制 + systemd）：Google OAuth 全流程，`--email-domain=dify.ai`
   限制只有 dify.ai 邮箱能进。log-viewer 应用本身不含任何认证代码。
4. **部署**：新 systemd unit（log-viewer + oauth2-proxy）+ nginx 加 `location /logs/`。

## 数据模型

### jsonl 每行格式（structlog JSON renderer 输出）

```json
{"event":"agent_done","ticket_id":2709,"rounds":7,"total_tokens":370297,"reply_sent":true,"status":"completed","timestamp":"2026-05-29T04:14:44Z","level":"info","module":"runner"}
{"event":"round_tool_calls","ticket_id":2709,"round":3,"tool_call_count":2,"tool_names":["git_grep","sql_query"],"timestamp":"...","level":"info"}
```

- 几乎每条业务日志已带 `ticket_id`（现有日志即如此），按工单分组天然成立
- 无 `ticket_id` 的行（启动、poller 心跳）归"系统"桶，列表页不显示

### 关键 event 类型（详情时间线据此渲染）

| event | 含义 | 图标 |
|---|---|---|
| `ticket_classified` | 分类 | 📋 |
| `trigger_verdict` | 触发判定 | 🎯 |
| `round_tool_calls` | 每轮工具调用（含 tool_names） | 🔧 |
| `inspector_exec_timeout` / `*_failed` / `*_error` | 失败/超时 | ⚠️ |
| `public_reply_sent` / `internal_reply_*` | 发回复 | 💬 |
| `compaction_triggered` | 上下文压缩 | 🗜️ |
| `agent_done` | 收尾 | ✅ |

## 后端 API（log-viewer）

| 端点 | 作用 | 返回 |
|---|---|---|
| `GET /logs/api/tickets` | 工单列表 | 每个 ticket_id 一行：最新状态、verdict、总轮数、token、耗时、reply_sent、最后时间 |
| `GET /logs/api/tickets/{id}` | 单工单全事件 | 该 ticket_id 所有日志行，按时间升序 |
| `GET /logs/` | HTML 页面 | 单文件 HTML（原生 JS + fetch） |

读取策略：**按需扫描**最近 N 天 jsonl 文件、内存过滤。量级小（一天几十单、几千行），
无需数据库/索引。坏行（非 JSON）跳过容错。

## 界面（两屏）

**列表屏**（默认，按时间倒序）：
表格列 = 工单号 / 状态 / verdict / 轮数 / token / 耗时 / 最后时间。
失败、高 token 行用颜色高亮。顶部有工单号搜索框。点行 → 详情屏。

**详情屏**（事件级时间线）：
顶部摘要（工单号 + 状态 + 轮数 + token + 耗时）。下面逐条事件：
时间 + 图标 + 关键字段。错误/超时红色；工具调用显示 tool_names（直接能看出批量与否）。
每行可点开看完整原始 JSON。

## 文件落点

```
zendesk-agent/
  src/zd_agent/log.py                       # 改：加 jsonl 文件 sink（仅文件 JSON，stdout 不变）
  log-viewer/                               # 新目录，独立服务
    main.py                                 # FastAPI app（3 路由）
    parser.py                               # jsonl 读取 + 按 ticket 分组聚合
    static/index.html                       # 单文件前端（原生 JS）
    pyproject.toml                          # 独立依赖：fastapi + uvicorn
    tests/test_parser.py                    # 解析/聚合单测
  deploy/systemd/zd-log-viewer.service      # 新 systemd unit
  deploy/systemd/oauth2-proxy.service       # 新 systemd unit
  deploy/nginx/zd-agent.conf                # 改：加 location /logs/ + /oauth2/
```

## 安全

- Google 登录（oauth2-proxy）+ `--email-domain=dify.ai` 限制只有 dify.ai 邮箱能进
- 默认任何 Google 账号能登录 → **必须**配域名限制（红线）
- log-viewer 纯只读 jsonl，无写/删接口、无命令执行 → 注入面为零
- 日志含客户 PII（邮箱/wid）→ Google 域限制 + HTTPS（nginx 已有证书）
- oauth2-proxy 需 cookie secret（随机生成）+ Google OAuth client（用户本人在 Google
  Cloud Console 创建，secret 由用户填，不由 AI 经手）

## 用户需自行完成的步骤

1. Google Cloud Console 建 OAuth 2.0 client（Web 类型）
2. Authorized redirect URI：`https://zendesk.smlershou.top/oauth2/callback`
3. 拿到 client ID + secret，填进 oauth2-proxy 配置（secret 用户自己填）
4. 生成 cookie secret（提供命令）

## 测试

- `parser.py` 单测：jsonl 解析、按 ticket 分组、列表聚合、坏行容错（跳过非 JSON）
- 用真实日志样本做 fixture
- log-viewer 独立 pyproject，测试不混进主仓 639 个测试

## 分阶段交付

1. **阶段 1**：agent 加 jsonl sink + 重部署，确认日志正常落盘（不碰 web）
2. **阶段 2**：写 log-viewer + 本地测，跑通解析
3. **阶段 3**：部署 log-viewer systemd + nginx 反代（先不加认证，本地 curl 验证）
4. **阶段 4**：装 oauth2-proxy + 建 Google client + nginx 加 auth_request（用户设凭据，验收登录）

## 不做（YAGNI）

- 不做数据库/全文索引（量小，文件扫描够）
- 不做日志写入/编辑/删除接口（纯只读）
- 不做实时推送/WebSocket（刷新即可）
- 不做多用户权限分级（dify.ai 域内一视同仁）
- log-viewer 不进 Docker（纯只读小服务，宿主 venv 跑更简单）
```
