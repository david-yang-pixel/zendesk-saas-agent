# @mention 触发 Agent 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持在工单 internal note 里写 `@support-agent` 触发 Agent 处理，Webhook 实时响应 + Scheduler 轮询兜底。

**Architecture:** 新增 `MentionDetector`（纯函数检测评论）和 `MentionTracker`（文件持久化去重）两个组件，Webhook 处理 `ticket.comment_added` 事件，Scheduler 新增独立的 mention 轮询循环。核心引擎零改动。

**Tech Stack:** Python 3.12、aiohttp、pydantic、APScheduler 4.x、unittest

---

## 文件清单

| 文件 | 类型 | 职责 |
|------|------|------|
| `src/zd_agent/mention/__init__.py` | 新增 | 模块导出 |
| `src/zd_agent/mention/detector.py` | 新增 | `is_mention()` 纯函数 |
| `src/zd_agent/mention/tracker.py` | 新增 | `MentionTracker` 持久化去重 |
| `tests/test_mention_detector.py` | 新增 | detector 单元测试 |
| `tests/test_mention_tracker.py` | 新增 | tracker 单元测试 |
| `src/zd_agent/config.py` | 修改 | `ZendeskConfig` 新增两个字段 |
| `config.yaml` | 修改 | 新增 mention 配置项 |
| `src/zd_agent/prompts/strategies/mention.md` | 新增 | mention 策略文件 |
| `src/zd_agent/tools/classify.py` | 修改 | `VALID_TYPES` 加 `mention` |
| `src/zd_agent/entry/webhook.py` | 修改 | 处理 `comment_added` 事件 |
| `tests/test_webhook.py` | 修改 | 新增 mention 相关测试 |
| `src/zd_agent/entry/scheduler.py` | 修改 | 新增 mention 轮询任务 |
| `.gitignore` | 修改 | 追加 `.state/` |

---

### Task 1: MentionDetector

**Files:**
- Create: `src/zd_agent/mention/__init__.py`
- Create: `src/zd_agent/mention/detector.py`
- Create: `tests/test_mention_detector.py`

- [ ] **Step 1: 写 detector 失败测试**

创建 `tests/test_mention_detector.py`：

```python
import unittest

from zd_agent.mention.detector import is_mention


class TestIsMention(unittest.TestCase):
    def test_detects_mention_in_internal_note(self) -> None:
        comment = {"is_public": False, "body": "Hey @support-agent can you look at this?"}
        self.assertTrue(is_mention(comment, "@support-agent"))

    def test_ignores_public_comment(self) -> None:
        comment = {"is_public": True, "body": "Hey @support-agent can you look at this?"}
        self.assertFalse(is_mention(comment, "@support-agent"))

    def test_case_insensitive(self) -> None:
        comment = {"is_public": False, "body": "Hey @Support-Agent please help"}
        self.assertTrue(is_mention(comment, "@support-agent"))

    def test_no_mention(self) -> None:
        comment = {"is_public": False, "body": "This is a regular internal note"}
        self.assertFalse(is_mention(comment, "@support-agent"))

    def test_missing_is_public_treated_as_public(self) -> None:
        comment = {"body": "Hey @support-agent"}
        self.assertFalse(is_mention(comment, "@support-agent"))

    def test_empty_body(self) -> None:
        comment = {"is_public": False, "body": ""}
        self.assertFalse(is_mention(comment, "@support-agent"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

```
uv run python -m unittest tests.test_mention_detector -v
```

Expected: ImportError — `zd_agent.mention.detector` does not exist

- [ ] **Step 3: 实现 detector**

创建 `src/zd_agent/mention/detector.py`：

```python
"""MentionDetector: 检测 Zendesk 评论是否包含 @mention 触发词"""

from __future__ import annotations


def is_mention(comment: dict, trigger: str) -> bool:
    """检测评论是否触发 @mention。

    只处理 is_public=False（internal note）的评论，大小写不敏感。
    """
    if comment.get("is_public", True):
        return False
    body = comment.get("body", "")
    return trigger.lower() in body.lower()
```

创建 `src/zd_agent/mention/__init__.py`（暂时为空，Task 2 完成后补全）：

```python
```

- [ ] **Step 4: 运行测试，确认通过**

```
uv run python -m unittest tests.test_mention_detector -v
```

Expected: 6 tests PASS

- [ ] **Step 5: 提交**

```bash
git add src/zd_agent/mention/__init__.py src/zd_agent/mention/detector.py tests/test_mention_detector.py
git commit -m "feat: 新增 MentionDetector 纯函数"
```

---

### Task 2: MentionTracker

**Files:**
- Create: `src/zd_agent/mention/tracker.py`
- Modify: `src/zd_agent/mention/__init__.py`
- Create: `tests/test_mention_tracker.py`

- [ ] **Step 1: 写 tracker 失败测试**

创建 `tests/test_mention_tracker.py`：

```python
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zd_agent.mention.tracker import MentionTracker


class TestMentionTracker(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = Path(self.tmpdir) / ".state" / "processed_mentions.json"
        self.tracker = MentionTracker(self.state_file)

    def test_new_comment_is_not_processed(self) -> None:
        self.assertFalse(self.tracker.is_processed("comment-123"))

    def test_mark_processed_persists_across_instances(self) -> None:
        self.tracker.mark_processed("comment-123")
        tracker2 = MentionTracker(self.state_file)
        self.assertTrue(tracker2.is_processed("comment-123"))

    def test_is_processed_after_marking(self) -> None:
        self.tracker.mark_processed("comment-456")
        self.assertTrue(self.tracker.is_processed("comment-456"))

    def test_cleanup_removes_old_entries(self) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        self.tracker._data["old-comment"] = old_ts
        self.tracker._save()
        self.tracker.cleanup(days=30)
        self.assertFalse(self.tracker.is_processed("old-comment"))

    def test_cleanup_keeps_recent_entries(self) -> None:
        self.tracker.mark_processed("recent-comment")
        self.tracker.cleanup(days=30)
        self.assertTrue(self.tracker.is_processed("recent-comment"))

    def test_comment_id_coerced_to_string(self) -> None:
        self.tracker.mark_processed(123)
        self.assertTrue(self.tracker.is_processed("123"))
        self.assertTrue(self.tracker.is_processed(123))

    def test_creates_parent_directory_if_missing(self) -> None:
        deep_path = Path(self.tmpdir) / "a" / "b" / "mentions.json"
        tracker = MentionTracker(deep_path)
        tracker.mark_processed("x")
        self.assertTrue(deep_path.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

```
uv run python -m unittest tests.test_mention_tracker -v
```

Expected: ImportError — `zd_agent.mention.tracker` does not exist

- [ ] **Step 3: 实现 MentionTracker**

创建 `src/zd_agent/mention/tracker.py`：

```python
"""MentionTracker: 追踪已处理的 @mention 评论 ID，防止重复派发"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


class MentionTracker:
    def __init__(self, state_file: str | Path = ".state/processed_mentions.json") -> None:
        self._path = Path(state_file)
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def is_processed(self, comment_id: str | int) -> bool:
        return str(comment_id) in self._data

    def mark_processed(self, comment_id: str | int) -> None:
        self._data[str(comment_id)] = datetime.now(timezone.utc).isoformat()
        self._save()

    def cleanup(self, days: int = 30) -> None:
        """删除超过 days 天的记录。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        to_delete = [
            cid for cid, ts in self._data.items()
            if datetime.fromisoformat(ts) < cutoff
        ]
        for cid in to_delete:
            del self._data[cid]
        if to_delete:
            self._save()
```

更新 `src/zd_agent/mention/__init__.py`：

```python
from zd_agent.mention.detector import is_mention
from zd_agent.mention.tracker import MentionTracker

__all__ = ["is_mention", "MentionTracker"]
```

- [ ] **Step 4: 运行测试，确认通过**

```
uv run python -m unittest tests.test_mention_tracker -v
```

Expected: 7 tests PASS

- [ ] **Step 5: 提交**

```bash
git add src/zd_agent/mention/tracker.py src/zd_agent/mention/__init__.py tests/test_mention_tracker.py
git commit -m "feat: 新增 MentionTracker 持久化去重"
```

---

### Task 3: 配置扩展

**Files:**
- Modify: `src/zd_agent/config.py`
- Modify: `config.yaml`

- [ ] **Step 1: 修改 `ZendeskConfig`**

在 `src/zd_agent/config.py` 的 `ZendeskConfig` 类中，在 `poll_query` 字段后新增两个字段：

```python
class ZendeskConfig(BaseModel):
    subdomain: str = ""
    email: str = ""
    api_token: str = ""
    poll_interval_seconds: int = 60
    poll_query: str = "status:open status:new"
    mention_trigger: str = "@support-agent"
    mention_poll_interval_seconds: int = 60
```

- [ ] **Step 2: 修改 `config.yaml`**

在 `zendesk:` 块的末尾追加：

```yaml
zendesk:
  poll_interval_seconds: 60
  poll_query: "status:open status:new"
  mention_trigger: "@support-agent"
  mention_poll_interval_seconds: 60
```

- [ ] **Step 3: 验证配置可加载**

```
uv run python -c "from zd_agent.config import load_config; c = load_config(); print(c.zendesk.mention_trigger, c.zendesk.mention_poll_interval_seconds)"
```

Expected: `@support-agent 60`

- [ ] **Step 4: 提交**

```bash
git add src/zd_agent/config.py config.yaml
git commit -m "feat: config 新增 mention_trigger 和 mention_poll_interval_seconds"
```

---

### Task 4: mention 策略文件 + classify_ticket 扩展

**Files:**
- Create: `src/zd_agent/prompts/strategies/mention.md`
- Modify: `src/zd_agent/tools/classify.py`

- [ ] **Step 1: 创建 mention 策略文件**

创建 `src/zd_agent/prompts/strategies/mention.md`：

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

- [ ] **Step 2: 修改 `classify.py` 的 `VALID_TYPES` 和描述**

在 `src/zd_agent/tools/classify.py` 中：

将：
```python
VALID_TYPES = {"config-deploy", "code-bug", "usage-howto", "general"}
```

改为：
```python
VALID_TYPES = {"config-deploy", "code-bug", "usage-howto", "general", "mention"}
```

将 `parameters` 里的 `description` 改为：
```python
"description": (
    "工单类型：config-deploy（配置/部署/环境）、"
    "code-bug（代码缺陷/回归）、"
    "usage-howto（使用方法/功能咨询）、"
    "mention（@mention 触发，聚焦回答 hint 中的问题）、"
    "general（无法明确归类/复合问题）"
),
```

- [ ] **Step 3: 验证策略文件可加载**

```
uv run python -c "
from zd_agent.tools.classify import _load_strategy
print(_load_strategy('mention')[:30])
"
```

Expected: 输出策略文件开头内容（`## @mention 触发策略`）

- [ ] **Step 4: 提交**

```bash
git add src/zd_agent/prompts/strategies/mention.md src/zd_agent/tools/classify.py
git commit -m "feat: 新增 mention 工单类型和策略文件"
```

---

### Task 5: Webhook 处理 comment_added 事件

**Files:**
- Modify: `src/zd_agent/entry/webhook.py`
- Modify: `tests/test_webhook.py`

- [ ] **Step 1: 写 webhook mention 失败测试**

在 `tests/test_webhook.py` 中，在 `WebhookTest` 类里新增以下辅助方法和测试：

在 `asyncSetUp` 中，把 `self.manager` 和 `self.server` 的构建方式改为接受 tracker：

```python
async def asyncSetUp(self) -> None:
    self.manager = SimpleNamespace(
        dispatch=AsyncMock(return_value=SimpleNamespace(session_id="session-123")),
    )
    self.tracker = SimpleNamespace(
        is_processed=MagicMock(return_value=False),
        mark_processed=MagicMock(),
    )
    self.server = TestServer(create_app(self.manager, tracker=self.tracker))
    self.client = TestClient(self.server)
    await self.client.start_server()
    config_module._config = None
```

在 `asyncSetUp` 前面补充 import（在文件顶部 `from unittest.mock import AsyncMock, patch` 那行加 `MagicMock`）：

```python
from unittest.mock import AsyncMock, MagicMock, patch
```

新增 `_comment_added_payload` 辅助方法：

```python
def _comment_added_payload(
    self,
    *,
    ticket_id: str = "342757",
    comment_id: str = "8659716087550",
    comment_body: str = "Hey @support-agent please help",
    is_public: bool = False,
) -> dict:
    return {
        "account_id": 22129848,
        "detail": {
            "id": ticket_id,
            "tags": [],
        },
        "event": {
            "comment": {
                "id": comment_id,
                "body": comment_body,
                "is_public": is_public,
                "author": {"id": "12345", "name": "Test User", "is_staff": True},
            },
        },
        "id": "test-event-id",
        "type": "zen:event-type:ticket.comment_added",
    }
```

新增 4 个测试方法：

```python
async def test_dispatches_on_mention_in_internal_note(self) -> None:
    with (
        patch.dict(os.environ, {"ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret}, clear=False),
        patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp),
    ):
        config_module._config = None
        response = await self._post_signed_event(self._comment_added_payload())

    self.assertEqual(response.status, 200)
    payload = await response.json()
    self.assertTrue(payload["accepted"])
    self.assertEqual(payload["ticket_id"], 342757)
    self.manager.dispatch.assert_awaited_once()
    call_kwargs = self.manager.dispatch.call_args
    self.assertIn("@support-agent", call_kwargs.kwargs["hint"])

async def test_ignores_mention_in_public_comment(self) -> None:
    with (
        patch.dict(os.environ, {"ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret}, clear=False),
        patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp),
    ):
        config_module._config = None
        response = await self._post_signed_event(
            self._comment_added_payload(is_public=True),
        )

    self.assertEqual(response.status, 200)
    payload = await response.json()
    self.assertFalse(payload["accepted"])
    self.assertEqual(payload["reason"], "no_mention_trigger")
    self.manager.dispatch.assert_not_awaited()

async def test_ignores_already_processed_comment(self) -> None:
    self.tracker.is_processed.return_value = True
    with (
        patch.dict(os.environ, {"ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret}, clear=False),
        patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp),
    ):
        config_module._config = None
        response = await self._post_signed_event(self._comment_added_payload())

    self.assertEqual(response.status, 200)
    payload = await response.json()
    self.assertFalse(payload["accepted"])
    self.assertEqual(payload["reason"], "already_processed")
    self.manager.dispatch.assert_not_awaited()

async def test_ignores_comment_without_mention(self) -> None:
    with (
        patch.dict(os.environ, {"ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret}, clear=False),
        patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp),
    ):
        config_module._config = None
        response = await self._post_signed_event(
            self._comment_added_payload(comment_body="Regular internal note, no mention here"),
        )

    self.assertEqual(response.status, 200)
    payload = await response.json()
    self.assertFalse(payload["accepted"])
    self.assertEqual(payload["reason"], "no_mention_trigger")
    self.manager.dispatch.assert_not_awaited()
```

- [ ] **Step 2: 运行新增测试，确认失败**

```
uv run python -m unittest tests.test_webhook -v
```

Expected: 4 个新测试 FAIL（`create_app` 不接受 `tracker` 参数）

- [ ] **Step 3: 修改 `webhook.py`**

在 `src/zd_agent/entry/webhook.py` 中做以下改动：

在现有 `MANAGER_APP_KEY` 那行后面新增：

```python
TRACKER_APP_KEY = web.AppKey("tracker", object)
COMMENT_ADDED_EVENT_TYPE = "zen:event-type:ticket.comment_added"
```

新增两个辅助函数（放在 `_extract_ticket_id` 后面）：

```python
def _extract_comment(body: dict) -> dict | None:
    """从 comment_added 事件体中提取评论对象。"""
    event = body.get("event")
    if not isinstance(event, dict):
        return None
    comment = event.get("comment")
    if not isinstance(comment, dict):
        return None
    return comment


async def _handle_comment_added(
    request: web.Request,
    body: dict,
    ticket_id: int,
    manager: object,
) -> web.Response:
    """处理 ticket.comment_added 事件：检测 @mention 并派发。"""
    from zd_agent.mention.detector import is_mention

    cfg = get_config()
    tracker = request.app.get(TRACKER_APP_KEY)

    comment = _extract_comment(body)
    if comment is None:
        return _build_ignored_response("missing_comment")

    if not is_mention(comment, cfg.zendesk.mention_trigger):
        return _build_ignored_response("no_mention_trigger")

    comment_id = str(comment.get("id", ""))
    if tracker is not None and tracker.is_processed(comment_id):
        return _build_ignored_response("already_processed")

    if tracker is not None:
        tracker.mark_processed(comment_id)

    hint = f"[来自 @mention 的请求]\n{comment.get('body', '')}"
    session = await manager.dispatch(ticket_id, hint=hint, dry_run=False)

    logger.info(
        "mention_dispatched",
        ticket_id=ticket_id,
        comment_id=comment_id,
        session_id=session.session_id,
    )

    return web.json_response({
        "accepted": True,
        "ticket_id": ticket_id,
        "session_id": session.session_id,
    })
```

在 `handle_zendesk_webhook` 函数中，将：

```python
    event_type = body.get("type")
    if event_type != SUPPORTED_EVENT_TYPE:
        return _build_ignored_response("unsupported_event_type")
```

替换为：

```python
    event_type = body.get("type")

    if event_type == COMMENT_ADDED_EVENT_TYPE:
        return await _handle_comment_added(request, body, ticket_id, manager)

    if event_type != SUPPORTED_EVENT_TYPE:
        return _build_ignored_response("unsupported_event_type")
```

将 `create_app` 函数签名和实现改为：

```python
def create_app(manager: object | None = None, tracker: object | None = None) -> web.Application:
    """创建 aiohttp web 应用"""
    app = web.Application()
    app[MANAGER_APP_KEY] = manager
    app[TRACKER_APP_KEY] = tracker

    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)
    app.router.add_post("/webhook/zendesk", handle_zendesk_webhook)

    return app
```

将 `run_webhook_server` 函数签名改为：

```python
async def run_webhook_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    manager: object | None = None,
    tracker: object | None = None,
) -> None:
    """启动 Webhook HTTP 服务器"""
    app = create_app(manager, tracker)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("webhook_server_started", host=host, port=port)
```

- [ ] **Step 4: 运行全部 webhook 测试，确认通过**

```
uv run python -m unittest tests.test_webhook -v
```

Expected: 全部 PASS（含之前的 5 个 + 新增的 4 个，共 9 个）

- [ ] **Step 5: 提交**

```bash
git add src/zd_agent/entry/webhook.py tests/test_webhook.py
git commit -m "feat: webhook 支持 @mention 触发派发"
```

---

### Task 6: Scheduler mention 轮询

**Files:**
- Modify: `src/zd_agent/entry/scheduler.py`

- [ ] **Step 1: 修改 `scheduler.py`**

在 `src/zd_agent/entry/scheduler.py` 文件顶部，在现有 import 之后新增：

```python
import time
from datetime import datetime, timezone
```

在模块级状态变量区域（`_seen_ticket_ids` 等），新增：

```python
_mention_tracker: MentionTracker | None = None  # type: ignore[name-defined]  # noqa: F821
_last_mention_poll_time: float = 0.0
```

在 `fetch_repos_task` 函数之后，新增 `poll_mention_task` 函数：

```python
async def poll_mention_task() -> None:
    """定时任务：轮询最近更新的工单，检测 @mention 并派发（APScheduler 注册用）"""
    from zd.client import ZendeskClient
    from zd_agent.mention.detector import is_mention

    global _last_mention_poll_time

    if _manager is None or _mention_tracker is None:
        return

    cfg = get_config()
    trigger = cfg.zendesk.mention_trigger

    since = datetime.fromtimestamp(_last_mention_poll_time, tz=timezone.utc)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    query = f"status:open status:new updated>{since_iso}"

    # 更新 poll 时间（在请求前更新，避免遗漏同一秒内的事件）
    _last_mention_poll_time = time.time()

    try:
        client = ZendeskClient()
        data = await asyncio.to_thread(client.search_tickets, query)
        results = data.get("results", [])

        for ticket in results:
            tid = ticket.get("id")
            if not tid:
                continue

            comments_data = await asyncio.to_thread(
                client.get_ticket_comments, tid, 1, 10
            )
            comments = comments_data.get("comments", [])

            # 倒序遍历（最新评论优先）
            for comment in reversed(comments):
                comment_id = str(comment.get("id", ""))
                if not comment_id:
                    continue
                if _mention_tracker.is_processed(comment_id):
                    continue
                if is_mention(comment, trigger):
                    _mention_tracker.mark_processed(comment_id)
                    hint = f"[来自 @mention 的请求]\n{comment.get('body', '')}"
                    await _manager.dispatch(tid, hint=hint, dry_run=_dry_run)
                    logger.info(
                        "mention_poll_dispatched",
                        ticket_id=tid,
                        comment_id=comment_id,
                    )
                    break  # 同一工单每轮只处理一条 mention

    except Exception as e:
        logger.error("mention_poll_error", error=str(e), exc_info=True)
        console.print(f"[red]@mention 轮询出错: {e}[/red]")
```

在 `run_scheduler` 函数中，在 `global _manager, _dry_run` 那行改为：

```python
global _manager, _dry_run, _mention_tracker, _last_mention_poll_time
```

在 `_manager = SessionManager()` 之后新增 tracker 初始化（在 `from zd_agent.session.manager import SessionManager` import 之后）：

```python
    from zd_agent.mention.tracker import MentionTracker

    _manager = SessionManager()
    _dry_run = dry_run
    _mention_tracker = MentionTracker()
    _mention_tracker.cleanup()
    _last_mention_poll_time = time.time()  # 从启动时刻开始，忽略历史 mention
```

在 `run_scheduler` 的 `async with AsyncScheduler() as scheduler:` 块内，在 `zendesk_poll` 调度之后新增 mention 轮询调度：

```python
        mention_interval = cfg.zendesk.mention_poll_interval_seconds
        await scheduler.add_schedule(
            poll_mention_task,
            IntervalTrigger(seconds=mention_interval),
            id="mention_poll",
        )
```

同时更新启动日志，在 `console.print` 的多行字符串里追加：

```python
        f"  @mention 轮询间隔: {mention_interval}s\n"
        f"  @mention 触发词: [cyan]{cfg.zendesk.mention_trigger}[/cyan]\n"
```

- [ ] **Step 2: 验证 scheduler 可正常导入**

```
uv run python -c "from zd_agent.entry.scheduler import poll_mention_task; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: 运行全部测试，确认没有回归**

```
uv run python -m unittest discover -s tests -v
```

Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add src/zd_agent/entry/scheduler.py
git commit -m "feat: scheduler 新增 @mention 轮询任务"
```

---

### Task 7: .gitignore

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: 追加 `.state/` 到 `.gitignore`**

在 `.gitignore` 末尾追加一行：

```
.state/
```

- [ ] **Step 2: 提交**

```bash
git add .gitignore
git commit -m "chore: 忽略 .state/ 运行时状态目录"
```

---

### Task 8: 回归验证

- [ ] **Step 1: 运行全部测试**

```
uv run python -m unittest discover -s tests -v
```

Expected: 全部 PASS

- [ ] **Step 2: 端到端冒烟检查**

手动向 webhook 发一条 `comment_added` 事件（含 `@support-agent` 的 internal note），确认日志输出 `mention_dispatched`：

```bash
curl -s -X POST http://localhost:8080/webhook/zendesk \
  -H "Content-Type: application/json" \
  -d '{"type":"zen:event-type:ticket.comment_added","detail":{"id":"12345","tags":[]},"event":{"comment":{"id":"test-001","body":"@support-agent please help","is_public":false}}}'
```

Expected: `{"accepted": true, "ticket_id": 12345, "session_id": "..."}`
