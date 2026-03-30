# 客户回复建议与复核工作流实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 zd-agent 在 Internal Note 中稳定产出“内部分析 + 客户回复建议 + 人工复核指引”三段式结果，并在 `code-bug` 场景下强制完成 GitHub PR 修复状态检查。

**Architecture:** 先通过 prompt 和测试把输出结构与工作流硬约束固化，再增加只读 GitHub PR 工具能力，让模型能真正执行“修复状态检查”。最后补充归档元数据与端到端回归，确保分析结果、客户话术和 reviewer 路径都能被保存和验证。

**Tech Stack:** Python 3.12、aiohttp、pydantic、unittest、Markdown prompts

---

## 文件清单

| 文件 | 类型 | 职责 |
|------|------|------|
| `src/zd_agent/prompts/AGENTS.md` | 修改 | 主工作流新增 Step 5.5 和三段式输出要求 |
| `src/zd_agent/prompts/TOOLS.md` | 修改 | 新增 GitHub PR 工具使用规则与证据约束 |
| `src/zd_agent/prompts/strategies/code-bug.md` | 修改 | 将“修复状态检查”升级为 checklist |
| `src/zd_agent/tools/github_pr.py` | 新增 | GitHub PR 只读工具实现 |
| `src/zd_agent/tools/registry.py` | 修改 | 注册新的 GitHub PR 工具 |
| `src/zd_agent/config.py` | 修改 | 复用 `github_token`，必要时增加 GitHub API base URL/timeout 配置 |
| `src/zd_agent/session/agent_session.py` | 修改 | 增加修复状态或 reviewer 结构化产物字段 |
| `src/zd_agent/runtime/archive_meta.py` | 修改 | 归档 `fix_status`、`customer_reply_available` 等元数据 |
| `src/zd_agent/tools/archive.py` | 修改 | 明确三段式内容归档方式 |
| `tests/test_archive_meta.py` | 修改 | 覆盖新增元数据字段 |
| `tests/test_tool_registry.py` | 新增 | 确认新 GitHub 工具已注册 |
| `tests/test_github_pr_tools.py` | 新增 | GitHub PR 工具单元测试 |
| `tests/test_prompts.py` | 新增 | 校验关键 prompt 约束文本未回退 |

---

### Task 1: 固化三段式输出与修复状态检查 Prompt

**Files:**
- Modify: `src/zd_agent/prompts/AGENTS.md`
- Modify: `src/zd_agent/prompts/TOOLS.md`
- Modify: `src/zd_agent/prompts/strategies/code-bug.md`
- Create: `tests/test_prompts.py`

- [ ] **Step 1: 写 prompt 约束失败测试**

创建 `tests/test_prompts.py`：

```python
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestPrompts(unittest.TestCase):
    def test_agents_prompt_requires_step_5_5(self) -> None:
        content = (ROOT / "src/zd_agent/prompts/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Step 5.5", content)
        self.assertIn("修复状态检查", content)

    def test_agents_prompt_requires_three_sections(self) -> None:
        content = (ROOT / "src/zd_agent/prompts/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("内部分析", content)
        self.assertIn("客户回复建议", content)
        self.assertIn("人工复核指引", content)

    def test_code_bug_prompt_requires_github_pr_check(self) -> None:
        content = (ROOT / "src/zd_agent/prompts/strategies/code-bug.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("GitHub", content)
        self.assertIn("open / merged PR", content)
        self.assertIn("已合并未发布", content)

    def test_tools_prompt_mentions_github_pr_tools(self) -> None:
        content = (ROOT / "src/zd_agent/prompts/TOOLS.md").read_text(encoding="utf-8")
        self.assertIn("github_search_prs", content)
        self.assertIn("github_get_pr", content)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run python -m unittest tests.test_prompts -v`

Expected: FAIL because prompt files do not yet contain the new required text.

- [ ] **Step 3: 修改主工作流 prompt**

更新 `src/zd_agent/prompts/AGENTS.md`：

- 在 Step 4 与 Step 6 之间插入 `Step 5.5 — 修复状态检查`
- 明确该步骤只对 `code-bug` 强制执行
- 要求结论必须落在 `未修复 / 修复中 / 已合并未发布 / 已发布修复 / 不确定`
- 将 Step 6 改成三段式输出，显式要求写出：
  - `## 内部分析`
  - `## 客户回复建议`
  - `## 人工复核指引`
- 在决策规则中增加“即使高置信度，也要给出最短人工复核路径”

- [ ] **Step 4: 修改源码工具与 GitHub 检索说明**

更新 `src/zd_agent/prompts/TOOLS.md`：

- 在“源码工具”章节补充 GitHub PR 工具说明
- 明确 `code-bug` 必须结合本地 diff 与 GitHub PR
- 明确 PR 证据等级要求，避免把“相关讨论”误判为已修复

- [ ] **Step 5: 修改 `code-bug` 策略**

更新 `src/zd_agent/prompts/strategies/code-bug.md`：

- 把现有“检查新版本是否已修复”改成 checklist
- 增加 `GitHub PR` 查询要求
- 增加 reviewer checklist 输出要求
- 增加修复状态分类枚举

- [ ] **Step 6: 运行测试，确认通过**

Run: `uv run python -m unittest tests.test_prompts -v`

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/zd_agent/prompts/AGENTS.md src/zd_agent/prompts/TOOLS.md src/zd_agent/prompts/strategies/code-bug.md tests/test_prompts.py
git commit -m "feat: 固化客户回复与复核工作流提示词"
```

---

### Task 2: 接入 GitHub PR 只读工具

**Files:**
- Create: `src/zd_agent/tools/github_pr.py`
- Modify: `src/zd_agent/tools/registry.py`
- Modify: `src/zd_agent/config.py`
- Create: `tests/test_github_pr_tools.py`
- Create: `tests/test_tool_registry.py`

- [ ] **Step 1: 写 GitHub 工具失败测试**

创建 `tests/test_github_pr_tools.py`：

```python
import unittest
from unittest.mock import AsyncMock, patch

from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.github_pr import GithubGetPr, GithubSearchPrs


class TestGithubSearchPrs(unittest.IsolatedAsyncioTestCase):
    @patch("zd_agent.tools.github_pr._github_request", new_callable=AsyncMock)
    async def test_search_prs_formats_results(self, mock_request: AsyncMock) -> None:
        mock_request.return_value = {
            "items": [
                {
                    "number": 123,
                    "title": "fix: handle null workspace",
                    "state": "open",
                    "html_url": "https://github.com/example/repo/pull/123",
                    "user": {"login": "alice"},
                }
            ]
        }
        tool = GithubSearchPrs()
        result = await tool.execute(
            {"repo": "langgenius/dify", "query": "workspace null", "limit": 5},
            AgentSession(ticket_id=1),
        )
        self.assertTrue(result.success)
        self.assertIn("#123", result.output)
        self.assertIn("open", result.output)


class TestGithubGetPr(unittest.IsolatedAsyncioTestCase):
    @patch("zd_agent.tools.github_pr._github_request", new_callable=AsyncMock)
    async def test_get_pr_formats_summary(self, mock_request: AsyncMock) -> None:
        mock_request.return_value = {
            "number": 456,
            "title": "fix: retry auth flow",
            "state": "closed",
            "merged_at": "2026-03-20T12:00:00Z",
            "html_url": "https://github.com/example/repo/pull/456",
            "body": "Fixes auth retry issue",
            "user": {"login": "bob"},
        }
        tool = GithubGetPr()
        result = await tool.execute(
            {"repo": "langgenius/dify", "pr_number": 456},
            AgentSession(ticket_id=1),
        )
        self.assertTrue(result.success)
        self.assertIn("merged", result.output.lower())
        self.assertIn("auth retry", result.output.lower())
```

创建 `tests/test_tool_registry.py`：

```python
import unittest

from zd_agent.tools.registry import create_tool_registry


class TestToolRegistry(unittest.TestCase):
    def test_registers_github_pr_tools(self) -> None:
        registry = create_tool_registry()
        self.assertIn("github_search_prs", registry)
        self.assertIn("github_get_pr", registry)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run python -m unittest tests.test_github_pr_tools tests.test_tool_registry -v`

Expected: FAIL because `zd_agent.tools.github_pr` and new registry entries do not yet exist.

- [ ] **Step 3: 实现 GitHub PR 工具**

创建 `src/zd_agent/tools/github_pr.py`，包含最小读取能力：

```python
"""GitHub PR 只读工具。"""

from __future__ import annotations

import os
from typing import Any

import aiohttp

from zd_agent.config import EnvSettings
from zd_agent.tools.base import Tool, ToolResult


def _github_headers() -> dict[str, str]:
    env = EnvSettings()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if env.github_token:
        headers["Authorization"] = f"Bearer {env.github_token}"
    return headers


async def _github_request(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers=_github_headers()) as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise RuntimeError(f"GitHub API [{resp.status}]: {data}")
            return data


class GithubSearchPrs(Tool):
    name = "github_search_prs"
    description = "搜索 GitHub pull requests，用于确认某个问题是否已有修复 PR"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "仓库全名，例如 langgenius/dify"},
            "query": {"type": "string", "description": "PR 搜索关键词"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
        "required": ["repo", "query"],
    }
```

同文件继续实现：

- `GithubSearchPrs.execute()`：调用 GitHub Search API，限定 `repo:<repo> is:pr`
- `GithubGetPr.execute()`：读取单个 PR 概要，输出状态、作者、merged_at、链接、摘要
- 如有余力可追加 `GithubGetPrDiff`，但本期非必需
- 所有输出都保持精简，便于 LLM 继续推理
- 若未配置 `github_token` 也允许匿名调用，但错误信息要明确说明可能受 rate limit 影响

- [ ] **Step 4: 注册工具与配置**

更新 `src/zd_agent/tools/registry.py`：

- 导入 `GithubSearchPrs`
- 导入 `GithubGetPr`
- 在源码工具组或独立 GitHub 工具组中注册

更新 `src/zd_agent/config.py`：

- 保持兼容 `github_token`
- 如需要，新增 `zd_github_api_timeout` 环境变量支持；若不加新配置，就统一在工具内写死 30 秒

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run python -m unittest tests.test_github_pr_tools tests.test_tool_registry -v`

Expected: PASS

- [ ] **Step 6: 补充一个真实查询的手工验证说明**

Run:

```bash
uv run python - <<'PY'
import asyncio
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.github_pr import GithubSearchPrs

async def main():
    tool = GithubSearchPrs()
    result = await tool.execute(
        {"repo": "langgenius/dify", "query": "login retry", "limit": 3},
        AgentSession(ticket_id=0),
    )
    print(result.success)
    print(result.output[:500])

asyncio.run(main())
PY
```

Expected: 返回 1-3 条 PR 摘要；若无 token 被限流，则应出现清晰错误说明。

- [ ] **Step 7: 提交**

```bash
git add src/zd_agent/tools/github_pr.py src/zd_agent/tools/registry.py src/zd_agent/config.py tests/test_github_pr_tools.py tests/test_tool_registry.py
git commit -m "feat: 新增 GitHub PR 检索工具"
```

---

### Task 3: 扩展会话与归档元数据

**Files:**
- Modify: `src/zd_agent/session/agent_session.py`
- Modify: `src/zd_agent/runtime/archive_meta.py`
- Modify: `src/zd_agent/tools/archive.py`
- Modify: `tests/test_archive_meta.py`

- [ ] **Step 1: 写归档元数据失败测试**

在 `tests/test_archive_meta.py` 增加：

```python
def test_build_archive_meta_includes_fix_status_and_customer_reply_flags(self) -> None:
    session = AgentSession(ticket_id=1810)
    session.ticket_type = "code-bug"
    session.analysis = "## 内部分析\n..."
    session.reply_draft = "## 内部分析\n...\n## 客户回复建议\n...\n## 人工复核指引\n..."
    session.fix_status = "已合并未发布"
    session.customer_reply_draft = "Hi customer"
    meta = build_archive_meta(session=session, archived_at="2026-03-30T12:00:00Z")
    self.assertEqual(meta["fix_status"], "已合并未发布")
    self.assertTrue(meta["customer_reply_available"])
    self.assertTrue(meta["reviewer_checklist_available"])
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run python -m unittest tests.test_archive_meta -v`

Expected: FAIL because new session/meta fields do not exist yet.

- [ ] **Step 3: 扩展 session 产物字段**

更新 `src/zd_agent/session/agent_session.py`，在“产出物”区域增加：

```python
    customer_reply_draft: str = ""
    reviewer_checklist: str = ""
    fix_status: str = ""
```

说明：

- 这些字段本期主要用于归档与后续可观测性
- 暂时不要求工具层自动解析 `reply_draft` 后回填，但后续如在 tool loop 中提取成功，可直接赋值

- [ ] **Step 4: 更新归档元数据**

更新 `src/zd_agent/runtime/archive_meta.py`：

- 增加 `ticket_type`
- 增加 `fix_status`
- 增加 `customer_reply_available`
- 增加 `reviewer_checklist_available`

建议实现：

```python
        "ticket_type": session.ticket_type or None,
        "fix_status": session.fix_status or None,
        "customer_reply_available": bool(session.customer_reply_draft or "## 客户回复建议" in session.reply_draft),
        "reviewer_checklist_available": bool(session.reviewer_checklist or "## 人工复核指引" in session.reply_draft),
```

- [ ] **Step 5: 更新 archive 工具注释与输出**

更新 `src/zd_agent/tools/archive.py`：

- 保持 `reply.md` 写入完整 Internal Note
- 在注释或输出文案中明确 `reply.md` 现在保存三段式内容
- 不额外拆新文件，保持本期范围收敛

- [ ] **Step 6: 运行测试，确认通过**

Run: `uv run python -m unittest tests.test_archive_meta -v`

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/zd_agent/session/agent_session.py src/zd_agent/runtime/archive_meta.py src/zd_agent/tools/archive.py tests/test_archive_meta.py
git commit -m "feat: 归档修复状态与客户回复元数据"
```

---

### Task 4: 端到端回归与文档更新

**Files:**
- Modify: `README.md`
- Modify: `ai-docs/design/implementation-plan.md` 或新增后续执行记录文档

- [ ] **Step 1: 更新 README 的流程说明**

在 `README.md` 的“标准流程”与归档说明中补充：

- `code-bug` 场景会额外检查 GitHub PR 修复状态
- Internal Note 现在包含三段式内容
- `reply.md` 保存完整 Internal Note

- [ ] **Step 2: 运行最小回归测试集**

Run:

```bash
uv run python -m unittest \
  tests.test_prompts \
  tests.test_github_pr_tools \
  tests.test_tool_registry \
  tests.test_archive_meta -v
```

Expected: PASS

- [ ] **Step 3: 运行现有相关测试避免回归**

Run:

```bash
uv run python -m unittest \
  tests.test_webhook \
  tests.test_official_docs \
  tests.test_source_selector -v
```

Expected: PASS

- [ ] **Step 4: 做一次人工 dry-run 验证**

Run:

```bash
uv run zd-agent run <ticket-id> --dry-run
```

检查：

- `reply.md` 中是否包含三段式结构
- 对 `code-bug` 类工单是否明确写出修复状态
- 是否出现 GitHub PR 检查结果
- “人工复核指引”是否真的给出 repo / ref / files / 关键词 / 判断方法

- [ ] **Step 5: 提交**

```bash
git add README.md ai-docs/design/implementation-plan.md
git commit -m "docs: 更新客户回复与复核工作流说明"
```

---

## 交付顺序建议

建议按以下顺序执行，保证每一阶段都可回归：

1. 先完成 Task 1，确保 prompt 行为边界先被锁死
2. 再完成 Task 2，让“查 PR”从口头要求变成可执行工具
3. 然后完成 Task 3，补齐归档与观察能力
4. 最后完成 Task 4，做回归验证并更新文档

---

## 风险提示

- GitHub Search API 匿名访问容易触发 rate limit，若测试环境没有 `GITHUB_TOKEN`，真实查询验证可能不稳定
- 仅靠 prompt 很难 100% 保证模型稳定拆出三段式内容，因此回归时必须检查真实 dry-run 产物
- 当前计划没有新增“解析 reply_draft 自动回填 session.customer_reply_draft”的实现，这不是 blocker，但后续若要更强结构化统计，可以追加一个小任务

---

## 完成定义

当以下条件全部满足时，可视为本功能完成：

- `code-bug` prompt 明确要求执行修复状态检查
- Agent 可使用 GitHub PR 工具查询 open / merged PR
- Internal Note 稳定输出三段式结构
- `reply.md` 与 `meta.json` 能反映修复状态和 reviewer 指引是否存在
- 至少完成一次真实 `--dry-run` 人工验收
