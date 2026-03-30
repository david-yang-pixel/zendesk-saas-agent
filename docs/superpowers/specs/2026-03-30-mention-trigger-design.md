# @mention 触发 Agent 设计

**创建时间：** 2026-03-30
**状态：** 已确认

---

## 背景

当前 zd-agent 只处理 `ticket.created` + `enterprise` 标签的工单。团队成员无法在已有工单中主动请求 Agent 协助。本设计引入 `@support-agent` mention 机制，让任何人可在工单 internal note 里 @ Agent 来触发处理。

---

## 目标

- 在 internal note 中写 `@support-agent`（可配置）即可触发 Agent
- Webhook 实时响应 + Scheduler 轮询兜底，两路触发
- 同一条评论不被重复处理
- 核心执行引擎（runner、tool_loop、guardrails）零改动

---

## 整体架构

```
config.yaml
└── zendesk.mention_trigger: "@support-agent"
└── zendesk.mention_poll_interval_seconds: 60

新增组件（src/zd_agent/mention/）
├── detector.py     # MentionDetector：纯函数，检测评论是否触发
└── tracker.py      # MentionTracker：读写 .state/processed_mentions.json

触发路径
Webhook (ticket.comment_added)
    → MentionDetector.is_mention(comment, trigger)
    → MentionTracker.is_processed(comment_id)
    → SessionManager.dispatch(ticket_id, hint=comment_body)

Scheduler（轮询）
    → 拉取最近更新的 open 工单
    → 取最新 internal note 评论
    → MentionDetector.is_mention()
    → MentionTracker.is_processed()   ← 去重
    → SessionManager.dispatch(ticket_id, hint=comment_body)

Agent 执行
    → classify_ticket 识别为 mention 类型
    → 加载 strategies/mention.md 策略
    → 跑 ToolLoop，发 Internal Note 回复
```

---

## 组件设计

### MentionDetector（`mention/detector.py`）

纯函数，无状态，可独立测试：

```python
def is_mention(comment: dict, trigger: str) -> bool:
    """
    只处理 is_public=False（internal note）的评论。
    大小写不敏感匹配 trigger 字符串。
    """
    if comment.get("is_public", True):
        return False
    body = comment.get("body", "")
    return trigger.lower() in body.lower()
```

### MentionTracker（`mention/tracker.py`）

持久化已处理的 comment ID，防止重复派发。

**存储文件：** `.state/processed_mentions.json`

```json
{
  "comment_id_1": "2026-03-30T10:00:00Z",
  "comment_id_2": "2026-03-29T08:00:00Z"
}
```

**方法：**

| 方法 | 说明 |
|------|------|
| `is_processed(comment_id) -> bool` | 检查是否已处理 |
| `mark_processed(comment_id)` | 写入并持久化 |
| `cleanup(days=30)` | 删除 N 天前的记录，启动时调用一次 |

`MentionTracker` 在 `SessionManager` 初始化时创建，作为共享单例注入 Webhook 和 Scheduler。

---

## Webhook 改动（`entry/webhook.py`）

新增 `zen:event-type:ticket.comment_added` 事件处理分支：

```python
elif event_type == "zen:event-type:ticket.comment_added":
    comment = body["event"]["comment"]
    if detector.is_mention(comment, settings.mention_trigger):
        comment_id = comment["id"]
        if not tracker.is_processed(comment_id):
            tracker.mark_processed(comment_id)
            hint = f"[来自 @mention 的请求]\n{comment['body']}"
            await manager.dispatch(ticket_id, hint=hint)
```

---

## Scheduler 改动（`entry/scheduler.py`）

新增独立的 @mention 轮询循环，与现有新工单轮询并行运行：

```
每 mention_poll_interval_seconds 秒:
  拉取最近更新的 open 工单列表
  对每张工单取最新的 internal note 评论
  → MentionDetector.is_mention()
  → MentionTracker.is_processed()
  → dispatch(ticket_id, hint=comment_body)
```

两个循环共用同一个 `SessionManager`，并发控制自动生效。

---

## 配置改动

**config.yaml：**

```yaml
zendesk:
  # ... 现有配置 ...
  mention_trigger: "@support-agent"
  mention_poll_interval_seconds: 60
```

**config.py（EnvSettings）：** 新增对应字段，支持环境变量覆盖。

---

## 策略文件（`prompts/strategies/mention.md`）

扩展 `classify_ticket` 工具，新增 `mention` 枚举值：

```markdown
## @mention 触发策略

你是被团队成员通过 @mention 显式请求协助的。

**处理重点：**
- hint 里包含触发 @mention 的评论原文，优先理解并回应其中的具体问题
- 不需要从零开始做完整 SOP，聚焦于回答 @mention 里提出的问题
- 如果 @mention 评论没有明确问题，则按正常 SOP 处理整张工单

**回复要求：**
- Internal Note 开头注明"[回应 @mention]"
- 直接回答问题，不需要重复完整的问题确认环节
```

---

## 涉及改动文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/zd_agent/mention/detector.py` | 新增 | MentionDetector 纯函数 |
| `src/zd_agent/mention/tracker.py` | 新增 | MentionTracker 持久化去重 |
| `src/zd_agent/mention/__init__.py` | 新增 | 模块导出 |
| `src/zd_agent/entry/webhook.py` | 修改 | 处理 comment_added 事件 |
| `src/zd_agent/entry/scheduler.py` | 修改 | 新增 @mention 轮询循环 |
| `src/zd_agent/session/manager.py` | 修改 | 注入 MentionTracker |
| `src/zd_agent/config.py` | 修改 | 新增两个配置字段 |
| `config.yaml` | 修改 | 新增 mention 配置 |
| `src/zd_agent/prompts/strategies/mention.md` | 新增 | mention 策略文件 |
| `src/zd_agent/tools/classify.py` | 修改 | 加 mention 枚举值 |
| `.state/` | 新增目录 | 加入 .gitignore |

**核心引擎零改动：** `runner.py`, `tool_loop.py`, `guardrails.py`, `prompt_loader.py`

---

## 不在本次范围内

- 公开评论（public comment）的 @mention 支持
- 多个 trigger 字符串
- @mention 处理历史的查询界面
