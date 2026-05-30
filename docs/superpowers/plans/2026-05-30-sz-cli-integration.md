# sz CLI 接入实现计划（版本感知 + SigNoz 日志查询 + 弃用 MCP）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把本地 `sz` CLI 纳入 zd-agent，让 agent 既能知道 cloud.dify.ai 线上实际运行版本，又能用 sz 接管全部 SigNoz 日志查询，并停用已失效的 SigNoz MCP。

**Architecture:** vendored `sz`（纯 Python3 stdlib，打进容器镜像）作为唯一 SigNoz 入口。一个 subprocess 封装层 `sz_cli.run_sz()` 被两类消费者共享：① `saas_version.py` 解析线上版本（SigNoz 主 + saas-deploy 兜底），经动态 prompt 段自动注入 + `saas_deployed_version` 工具深挖；② `signoz_tools.py` 的 4 个结构化工具（errors/traceback/services/query）替换 MCP。registry 停止注册 MCP 工具。

**Tech Stack:** Python 3.12, asyncio subprocess, pytest + pytest-asyncio (asyncio_mode=auto), 现有 `Tool`/`ToolResult` 基类、`PromptSection`/`PromptContext` 框架。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `scripts/sz` | vendored SigNoz CLI（修好 `--json`） | Create（从本地复制 + 改）|
| `scripts/signoz-cli-README.md` | sz 用法文档 | Create（复制）|
| `src/zd_agent/integrations/sz_cli.py` | `run_sz()` subprocess 封装（共享） | Create |
| `src/zd_agent/tools/saas_version.py` | tag→ref 转换 + `resolve_deployed_version()` + `SaasDeployedVersion` 工具 | Create |
| `src/zd_agent/tools/signoz_tools.py` | `signoz_errors`/`_traceback`/`_services`/`_query` 四工具 | Create |
| `src/zd_agent/runtime/runner.py` | `agent_loop` 手工拼 user message 处注入版本块 | Modify |
| `src/zd_agent/tools/registry.py` | 注册新工具 + 停 MCP spawn | Modify |
| `src/zd_agent/tools/subagent_parallel.py` | 示例工具名 signoz_* 更新 | Modify |
| `src/zd_agent/prompts/*.md` | 旧 MCP 工具名 → sz 工具名 + 用法重写 | Modify |
| `tests/tools/test_saas_version.py` | 版本模块单测（含 tag→ref、resolve、工具）| Create |
| `tests/tools/test_version_injection.py` | 版本注入文本块渲染单测 | Create |
| `tests/tools/test_signoz_tools.py` | 日志工具单测 | Create |
| `tests/tools/test_registry_signoz.py` | registry 注册/停 MCP 单测 | Create |
| `tests/integrations/test_sz_cli.py` | run_sz 单测 | Create |
| `tests/test_sz_json_flag.py` | sz `--json` 解析冒烟 | Create |
| `tests/prompts/test_no_stale_signoz_names.py` | prompt 无残留旧工具名 | Create |

**注：** 版本注入走 runner.py 手工拼 user message 的现有路径（仿 customer_profile），**不**改 prompt builder 框架（`section.py`/`defaults.py`）——实读 runner.py 确认 `build_dynamic_user_context` 未被调用。

---

## Task 1: Vendor sz CLI 并修复 `--json`

**Files:**
- Create: `scripts/sz`（从 `~/Documents/工作/saas工单常见问题/signoz-cli/sz` 复制后改）
- Create: `scripts/signoz-cli-README.md`（从同目录 README.md 复制）
- Test: `tests/test_sz_json_flag.py`

**背景：** 本地 sz 把 `--json`/`-q` 只挂在顶层 parser，导致 `sz query "..." --json` 报 `unrecognized arguments`。修法：用一个 `add_help=False` 的父 parser 承载 `--json`/`-q`，只挂到各子命令（不挂顶层），这样 flag 必须跟在子命令后，位置确定、无 argparse 默认覆盖坑。`run_sz` 永远把 `--json` 放在子命令之后，匹配此约定。

- [ ] **Step 1: 复制 sz 与 README 到 scripts/**

```bash
cp "/Users/yangyonggang/Documents/工作/saas工单常见问题/signoz-cli/sz" scripts/sz
cp "/Users/yangyonggang/Documents/工作/saas工单常见问题/signoz-cli/README.md" scripts/signoz-cli-README.md
chmod +x scripts/sz
```

- [ ] **Step 2: 写失败测试（验证 query 子命令接受 --json）**

`tests/test_sz_json_flag.py`：

```python
"""scripts/sz 的 --json flag 在子命令下必须可用（vendored 后修复点）。"""
import importlib.util
from pathlib import Path

import pytest

SZ_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sz"


def _load_sz():
    spec = importlib.util.spec_from_file_location("sz_vendored", SZ_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_query_accepts_json_flag_after_subcommand():
    sz = _load_sz()
    parser = sz.build_parser()
    args = parser.parse_args(["query", "SELECT 1", "--json"])
    assert args.json is True
    assert args.sql == "SELECT 1"


def test_errors_accepts_json_flag():
    sz = _load_sz()
    parser = sz.build_parser()
    args = parser.parse_args(["errors", "--env", "production", "--json"])
    assert args.json is True


def test_services_accepts_json_flag():
    sz = _load_sz()
    parser = sz.build_parser()
    args = parser.parse_args(["services", "--json"])
    assert args.json is True
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_sz_json_flag.py -v`
Expected: FAIL（`unrecognized arguments: --json` 或 SystemExit）

- [ ] **Step 4: 修 scripts/sz 的 build_parser**

在 `scripts/sz` 中，把顶层 parser 的 `--json`/`-q` 改为父 parser 注入到每个子命令。

定位（原 311-318 行附近）：

```python
def build_parser():
    p = argparse.ArgumentParser(
        prog="sz",
        description="SigNoz CLI (zero-dep). Talks directly to SigNoz v4 query_range API.",
    )
    p.add_argument("--json", action="store_true", help="emit raw JSON")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress meta lines")
    sub = p.add_subparsers(dest="cmd", required=True)
```

改为：

```python
def build_parser():
    p = argparse.ArgumentParser(
        prog="sz",
        description="SigNoz CLI (zero-dep). Talks directly to SigNoz v4 query_range API.",
    )
    # 公共 flag 父 parser：--json / -q 挂到每个子命令（放在子命令之后传），
    # 避免顶层-only 导致 `sz query "..." --json` 报 unrecognized arguments。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit raw JSON")
    common.add_argument("-q", "--quiet", action="store_true", help="suppress meta lines")
    sub = p.add_subparsers(dest="cmd", required=True)
```

然后给每个 `sub.add_parser(...)` 加 `parents=[common]`。五处：

```python
    pe = sub.add_parser("errors", parents=[common], help="search ERROR/FATAL logs")
    ...
    pt = sub.add_parser("traceback", parents=[common], help="pull exception.stacktrace from logs")
    ...
    pq = sub.add_parser("query", parents=[common], help="run a raw ClickHouse SQL")
    ...
    ps = sub.add_parser("schema", parents=[common], help="describe a SigNoz ClickHouse table")
    ...
    psv = sub.add_parser("services", parents=[common], help="list active services / deployments")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_sz_json_flag.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add scripts/sz scripts/signoz-cli-README.md tests/test_sz_json_flag.py
git commit -m "feat(sz): vendor sz CLI into scripts/, fix --json on subcommands

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: subprocess 封装 `sz_cli.run_sz()`

**Files:**
- Create: `src/zd_agent/integrations/sz_cli.py`
- Test: `tests/integrations/test_sz_cli.py`

**说明：** `run_sz(*args)` 以 `--json` 调 vendored sz，解析 stdout JSON。任何失败（非零退出/超时/非 JSON）返回 None，记 warning，不抛。sz 路径用常量（容器 `/app/scripts/sz`，env `ZD_SZ_PATH` 覆盖，本地回退仓库相对路径）。SIGNOZ_URL/API_KEY 从 config 透传（缺省则 sz 用内置默认）。

- [ ] **Step 1: 写失败测试**

`tests/integrations/test_sz_cli.py`：

```python
"""run_sz subprocess 封装测试。"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.integrations.sz_cli import run_sz


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_run_sz_parses_json():
    rows = [{"image_tag": "hotfix-1.14.2-fix.4"}]
    fake = _FakeProc(json.dumps(rows).encode(), b"", 0)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake)):
        result = await run_sz("query", "SELECT 1")
    assert result == rows


@pytest.mark.asyncio
async def test_run_sz_nonzero_returns_none():
    fake = _FakeProc(b"", b"boom", 2)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake)):
        result = await run_sz("query", "bad")
    assert result is None


@pytest.mark.asyncio
async def test_run_sz_non_json_returns_none():
    fake = _FakeProc(b"not json at all", b"", 0)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake)):
        result = await run_sz("services")
    assert result is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/integrations/test_sz_cli.py -v`
Expected: FAIL（`ModuleNotFoundError: zd_agent.integrations.sz_cli`）

- [ ] **Step 3: 实现 sz_cli.py**

`src/zd_agent/integrations/sz_cli.py`：

```python
"""subprocess 封装：调 vendored sz CLI（scripts/sz），解析其 --json 输出。

sz 是零依赖 Python3 CLI，直接打 SigNoz v4 query_range API。
本模块被 saas_version.py（版本解析）与 signoz_tools.py（日志查询）共享。
任何失败一律返回 None / 记 warning，绝不抛——调用方负责降级。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from zd_agent.config import get_config
from zd_agent.log import get_logger

logger = get_logger(module="integrations.sz_cli")

# 容器内 sz 路径（Dockerfile COPY scripts ./scripts → /app/scripts/sz）。
# env ZD_SZ_PATH 可覆盖；都没有时回退到仓库相对路径（本地开发）。
_DEFAULT_CONTAINER_PATH = "/app/scripts/sz"
_REPO_RELATIVE = Path(__file__).resolve().parents[3] / "scripts" / "sz"

_DEFAULT_TIMEOUT = 30.0


def _sz_path() -> str:
    env_path = os.environ.get("ZD_SZ_PATH")
    if env_path:
        return env_path
    if os.path.exists(_DEFAULT_CONTAINER_PATH):
        return _DEFAULT_CONTAINER_PATH
    return str(_REPO_RELATIVE)


def _sz_env() -> dict[str, str]:
    """把 config 里的 SigNoz url/key 透传给 sz；缺省则 sz 用其内置默认。"""
    env = dict(os.environ)
    cfg = get_config().signoz
    if cfg.url:
        env["SIGNOZ_URL"] = cfg.url
    if cfg.api_key:
        env["SIGNOZ_API_KEY"] = cfg.api_key
    return env


async def run_sz(
    *args: str, timeout: float = _DEFAULT_TIMEOUT
) -> list[Any] | dict[str, Any] | None:
    """运行 `sz <args...> --json` 并解析 stdout JSON。

    失败（非零退出 / 超时 / 非 JSON / sz 缺失）→ 返回 None。
    """
    sz = _sz_path()
    cmd = [sys.executable, sz, *args, "--json", "-q"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_sz_env(),
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("sz_timeout", args=args, timeout=timeout)
        return None
    except Exception as e:
        logger.warning("sz_spawn_failed", args=args, error=str(e))
        return None

    if proc.returncode != 0:
        logger.warning(
            "sz_nonzero_exit",
            args=args,
            rc=proc.returncode,
            stderr=stderr_b.decode("utf-8", errors="replace")[:300],
        )
        return None

    out = stdout_b.decode("utf-8", errors="replace").strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        logger.warning("sz_non_json", args=args, preview=out[:200])
        return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/integrations/test_sz_cli.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add src/zd_agent/integrations/sz_cli.py tests/integrations/test_sz_cli.py
git commit -m "feat(sz): add run_sz subprocess wrapper for sz CLI

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: tag→ref 转换（纯函数，先 TDD）

**Files:**
- Create: `src/zd_agent/tools/saas_version.py`（先只放纯函数 + dataclass）
- Test: `tests/tools/test_saas_version.py`

**说明：** 镜像 tag 用连字符（docker tag 不允许 `/`），dify 仓对应分支用斜杠。`hotfix-1.14.2-fix.4` → `hotfix/1.14.2-fix.4`；纯版本 tag（`1.14.2`）原样。同时抽 `base_version`（`\d+\.\d+\.\d+`）。

- [ ] **Step 1: 写失败测试**

`tests/tools/test_saas_version.py`：

```python
"""saas_version 模块测试。"""
import pytest

from zd_agent.tools.saas_version import (
    DeployedVersion,
    image_tag_to_git_ref,
    extract_base_version,
)


@pytest.mark.parametrize("tag,expected", [
    ("hotfix-1.14.2-fix.4", "hotfix/1.14.2-fix.4"),
    ("hotfix-1.13.3-fix6", "hotfix/1.13.3-fix6"),
    ("1.14.2", "1.14.2"),
    ("v1.14.2", "v1.14.2"),
    ("", ""),
])
def test_image_tag_to_git_ref(tag, expected):
    assert image_tag_to_git_ref(tag) == expected


@pytest.mark.parametrize("tag,expected", [
    ("hotfix-1.14.2-fix.4", "1.14.2"),
    ("1.14.2", "1.14.2"),
    ("v1.13.0-rc1", "1.13.0"),
    ("garbage", ""),
])
def test_extract_base_version(tag, expected):
    assert extract_base_version(tag) == expected


def test_deployed_version_dataclass():
    dv = DeployedVersion(
        image_tag="hotfix-1.14.2-fix.4",
        git_ref="hotfix/1.14.2-fix.4",
        base_version="1.14.2",
        ref_verified=True,
        source="signoz",
    )
    assert dv.image_tag == "hotfix-1.14.2-fix.4"
    assert dv.source == "signoz"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/tools/test_saas_version.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现纯函数 + dataclass**

`src/zd_agent/tools/saas_version.py`（首版只含这些）：

```python
"""线上部署版本解析：SigNoz 主源（经 sz CLI）+ saas-deploy 兜底。

- resolve_deployed_version()：返回 cloud.dify.ai 生产 dify-api 当前运行版本。
- SaasDeployedVersion：agent 深挖工具。
版本信息是锦上添花，任何失败都返回 None / 明确 error，绝不中断工单处理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_BASE_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class DeployedVersion:
    image_tag: str        # SigNoz container.image.tag，如 hotfix-1.14.2-fix.4
    git_ref: str          # 转换+验证后可 git_show 的 ref，如 hotfix/1.14.2-fix.4
    base_version: str     # 1.14.2
    ref_verified: bool    # git_ref 是否真的在 dify 仓存在
    source: str           # "signoz" | "saas-deploy-fallback"


def image_tag_to_git_ref(tag: str) -> str:
    """镜像 tag（连字符）→ dify 仓 git ref（斜杠）。

    hotfix-1.14.2-fix.4 → hotfix/1.14.2-fix.4
    纯版本 tag（1.14.2 / v1.14.2）原样返回。
    """
    if not tag:
        return ""
    if tag.startswith("hotfix-"):
        return "hotfix/" + tag[len("hotfix-"):]
    return tag


def extract_base_version(tag: str) -> str:
    """从 tag 抽取 x.y.z 基线版本；抽不到返回空串。"""
    m = _BASE_VERSION_RE.search(tag or "")
    return m.group(1) if m else ""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/tools/test_saas_version.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zd_agent/tools/saas_version.py tests/tools/test_saas_version.py
git commit -m "feat(version): saas_version tag->ref conversion + DeployedVersion

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `resolve_deployed_version()`（SigNoz 主 + saas-deploy 兜底 + 缓存）

**Files:**
- Modify: `src/zd_agent/tools/saas_version.py`
- Test: `tests/tools/test_saas_version.py`（追加）

**说明：** SigNoz 主路径经 `run_sz("query", SQL)` 取 dify-api image tag；失败回退 saas-deploy kustomization。ref 转换后用 `git rev-parse --verify` 验证（失败先 auto-fetch dify 仓再验，复用 git_source._auto_fetch_repo）。进程内缓存 5 min。全程不抛。

- [ ] **Step 1: 追加失败测试**

在 `tests/tools/test_saas_version.py` 追加：

```python
import zd_agent.tools.saas_version as sv


@pytest.fixture(autouse=True)
def _clear_cache():
    sv._reset_cache_for_test()
    yield
    sv._reset_cache_for_test()


@pytest.mark.asyncio
async def test_resolve_from_signoz(monkeypatch):
    async def fake_run_sz(*args, **kw):
        return [{"image_tag": "hotfix-1.14.2-fix.4",
                 "service_version": "hotfix-1.14.2-fix.4"}]
    async def fake_verify(ref):
        return True
    monkeypatch.setattr(sv, "run_sz", fake_run_sz)
    monkeypatch.setattr(sv, "_verify_ref", fake_verify)

    dv = await sv.resolve_deployed_version(use_cache=False)
    assert dv is not None
    assert dv.image_tag == "hotfix-1.14.2-fix.4"
    assert dv.git_ref == "hotfix/1.14.2-fix.4"
    assert dv.base_version == "1.14.2"
    assert dv.ref_verified is True
    assert dv.source == "signoz"


@pytest.mark.asyncio
async def test_resolve_falls_back_to_saas_deploy(monkeypatch):
    async def fake_run_sz(*args, **kw):
        return None  # SigNoz 失败
    async def fake_fallback():
        return "hotfix-1.14.2-fix.3"
    async def fake_verify(ref):
        return True
    monkeypatch.setattr(sv, "run_sz", fake_run_sz)
    monkeypatch.setattr(sv, "_read_saas_deploy_tag", fake_fallback)
    monkeypatch.setattr(sv, "_verify_ref", fake_verify)

    dv = await sv.resolve_deployed_version(use_cache=False)
    assert dv is not None
    assert dv.image_tag == "hotfix-1.14.2-fix.3"
    assert dv.source == "saas-deploy-fallback"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_all_fail(monkeypatch):
    async def fake_run_sz(*args, **kw):
        return None
    async def fake_fallback():
        return None
    monkeypatch.setattr(sv, "run_sz", fake_run_sz)
    monkeypatch.setattr(sv, "_read_saas_deploy_tag", fake_fallback)

    dv = await sv.resolve_deployed_version(use_cache=False)
    assert dv is None


@pytest.mark.asyncio
async def test_resolve_uses_cache(monkeypatch):
    calls = {"n": 0}
    async def fake_run_sz(*args, **kw):
        calls["n"] += 1
        return [{"image_tag": "hotfix-1.14.2-fix.4"}]
    async def fake_verify(ref):
        return True
    monkeypatch.setattr(sv, "run_sz", fake_run_sz)
    monkeypatch.setattr(sv, "_verify_ref", fake_verify)

    await sv.resolve_deployed_version(use_cache=True)
    await sv.resolve_deployed_version(use_cache=True)
    assert calls["n"] == 1  # 第二次命中缓存
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/tools/test_saas_version.py -v`
Expected: FAIL（`AttributeError: _reset_cache_for_test` / `resolve_deployed_version`）

- [ ] **Step 3: 实现 resolve + 辅助函数**

在 `saas_version.py` 顶部 import 区加：

```python
import asyncio
import time

from zd_agent.config import get_config
from zd_agent.integrations.sz_cli import run_sz
from zd_agent.log import get_logger
from zd_agent.repos import resolve_repo, fetch_repo

logger = get_logger(module="tools.saas_version")

_TARGET_DEPLOYMENT = "dify-api-oltp"
_KUSTOMIZATION_PATH = "environments/production/dify/api-oltp/kustomization.yaml"
_CACHE_TTL_SECONDS = 300.0
_VERSION_SQL = (
    "SELECT DISTINCT "
    "resources_string['container.image.tag']  AS image_tag, "
    "resources_string['service.version']      AS service_version "
    "FROM signoz_logs.distributed_logs_v2 "
    "WHERE timestamp >= {start_ns} "
    "AND resources_string['k8s.deployment.name'] = '" + _TARGET_DEPLOYMENT + "' "
    "AND resources_string['deployment.environment'] = 'production' "
    "LIMIT 5"
)

# 进程内缓存
_cache: tuple[float, DeployedVersion | None] | None = None


def _reset_cache_for_test() -> None:
    global _cache
    _cache = None
```

在文件追加实现：

```python
async def _verify_ref(ref: str) -> bool:
    """git rev-parse --verify <ref>；失败先 auto-fetch dify 仓再验一次。"""
    if not ref:
        return False
    repo_dir = resolve_repo("dify")
    if repo_dir is None:
        return False

    async def _rev_parse() -> bool:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_dir), "rev-parse", "--verify", "-q", f"{ref}^{{commit}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0

    if await _rev_parse():
        return True
    # auto-fetch dify 仓后重试
    try:
        cfg = get_config()
        for entry in cfg.repos.repositories:
            if entry.name == "dify":
                await asyncio.to_thread(fetch_repo, entry)
                break
    except Exception as e:
        logger.warning("verify_ref_fetch_failed", error=str(e))
    return await _rev_parse()


async def _query_signoz_image_tag() -> str | None:
    """经 sz 查 dify-api-oltp 的 container.image.tag。多 tag 时取第一个。"""
    rows = await run_sz("query", _VERSION_SQL, "--hours", "3")
    if not rows or not isinstance(rows, list):
        return None
    for row in rows:
        tag = (row or {}).get("image_tag")
        if tag:
            return str(tag)
    return None


async def _read_saas_deploy_tag() -> str | None:
    """兜底：从 saas-deploy kustomization 读 newTag（先 fetch 求新鲜）。"""
    repo_dir = resolve_repo("saas-deploy")
    if repo_dir is None:
        return None
    try:
        cfg = get_config()
        for entry in cfg.repos.repositories:
            if entry.name == "saas-deploy":
                await asyncio.to_thread(fetch_repo, entry)
                break
    except Exception as e:
        logger.warning("saas_deploy_fetch_failed", error=str(e))

    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo_dir), "show", f"main:{_KUSTOMIZATION_PATH}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out_b, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    for line in out_b.decode("utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("newTag:"):
            return s.split(":", 1)[1].strip()
    return None


async def resolve_deployed_version(*, use_cache: bool = True) -> DeployedVersion | None:
    """解析 cloud.dify.ai 生产 dify-api 当前运行版本。读不到 → None，绝不抛。"""
    global _cache
    if use_cache and _cache is not None:
        ts, cached = _cache
        if (time.monotonic() - ts) < _CACHE_TTL_SECONDS:
            return cached

    result: DeployedVersion | None = None
    try:
        tag = await _query_signoz_image_tag()
        source = "signoz"
        if not tag:
            tag = await _read_saas_deploy_tag()
            source = "saas-deploy-fallback"
        if tag:
            git_ref = image_tag_to_git_ref(tag)
            verified = await _verify_ref(git_ref)
            result = DeployedVersion(
                image_tag=tag,
                git_ref=git_ref if verified else tag,
                base_version=extract_base_version(tag),
                ref_verified=verified,
                source=source,
            )
    except Exception as e:
        logger.warning("resolve_deployed_version_failed", error=str(e))
        result = None

    _cache = (time.monotonic(), result)
    return result
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/tools/test_saas_version.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add src/zd_agent/tools/saas_version.py tests/tools/test_saas_version.py
git commit -m "feat(version): resolve_deployed_version with signoz primary + saas-deploy fallback + cache

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 深挖工具 `saas_deployed_version`

**Files:**
- Modify: `src/zd_agent/tools/saas_version.py`（追加 Tool 子类）
- Test: `tests/tools/test_saas_version.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
from zd_agent.tools.saas_version import SaasDeployedVersion


@pytest.mark.asyncio
async def test_tool_returns_version(monkeypatch):
    async def fake_resolve(*, use_cache=True):
        return sv.DeployedVersion(
            image_tag="hotfix-1.14.2-fix.4",
            git_ref="hotfix/1.14.2-fix.4",
            base_version="1.14.2",
            ref_verified=True,
            source="signoz",
        )
    monkeypatch.setattr(sv, "resolve_deployed_version", fake_resolve)

    tool = SaasDeployedVersion()
    result = await tool.execute({}, session=None)
    assert result.success
    assert "hotfix-1.14.2-fix.4" in result.output
    assert "hotfix/1.14.2-fix.4" in result.output


@pytest.mark.asyncio
async def test_tool_handles_none(monkeypatch):
    async def fake_resolve(*, use_cache=True):
        return None
    monkeypatch.setattr(sv, "resolve_deployed_version", fake_resolve)

    tool = SaasDeployedVersion()
    result = await tool.execute({}, session=None)
    assert "未能确定" in result.output or result.error
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/tools/test_saas_version.py -k tool -v`
Expected: FAIL（`ImportError: SaasDeployedVersion`）

- [ ] **Step 3: 实现工具类**

在 `saas_version.py` 追加（顶部 import 加 `from typing import Any` 与 `from zd_agent.tools.base import Tool, ToolResult`）：

```python
class SaasDeployedVersion(Tool):
    name = "saas_deployed_version"
    description = (
        "查询 cloud.dify.ai 生产环境 dify-api 当前实际运行的版本（来自 SigNoz "
        "实时遥测，saas-deploy 兜底）。用于判断：某 bug 是否已线上修复、某功能"
        "是否已上线、客户报的问题对应线上哪一份源码。返回可直接喂给 git_show / "
        "git_grep 的源码 ref。"
    )
    parameters = {"type": "object", "properties": {}}
    group = "source_code"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        dv = await resolve_deployed_version()
        if dv is None:
            return ToolResult(
                output="未能确定线上版本（SigNoz 与 saas-deploy 均不可达）。"
                       "可稍后重试，或用 list_repo_tags 看 dify 仓可用版本。"
            )
        ref_note = "✓ 已验证" if dv.ref_verified else "⚠️ 未在 dify 仓验证到该 ref"
        src_note = "SigNoz 实时遥测" if dv.source == "signoz" else "saas-deploy 声明值（SigNoz 不可达，可能非实时）"
        return ToolResult(output=(
            f"## cloud.dify.ai 生产 dify-api 运行版本\n"
            f"- 镜像 tag: `{dv.image_tag}`\n"
            f"- 基线版本: {dv.base_version or '(未知)'}\n"
            f"- 源码 ref: `{dv.git_ref}` （{ref_note}）\n"
            f"  → 用 git_show / git_grep 查这个 ref = 线上实际在跑的代码\n"
            f"- 数据来源: {src_note}"
        ))
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/tools/test_saas_version.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add src/zd_agent/tools/saas_version.py tests/tools/test_saas_version.py
git commit -m "feat(version): add saas_deployed_version deep-dive tool

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 版本自动注入到 user message

**Files:**
- Modify: `src/zd_agent/runtime/runner.py`（在 `agent_loop` 手工拼接 user message 处加版本段）
- Test: `tests/tools/test_version_injection.py`

**关键事实（实读 runner.py 确认）：** runner.py 的 `agent_loop()` **不调用** `build_dynamic_user_context`——它在第 102-148 行**手工**把 ticket-specific 上下文（日期、customer_profile、hint）拼进 `user_content_parts` 列表，再 append 成首条 user message。`build_static_system_prompt()` 只生成静态 system prompt。所以版本注入要**仿照 customer_profile 的手工拼法**加进 `user_content_parts`，不去碰 prompt builder 框架。这样天然满足「版本进 user message 不进 system prompt → 不破坏 prefix cache」。

**注意：** `AgentSession` 已有 `customer_version`（客户自报版本），与「线上部署版本」**不同**，勿复用。

- [ ] **Step 1: 写失败测试（渲染纯函数）**

注入文本由一个纯函数 `render_deployed_version_block(dv)` 生成，便于测试。`tests/tools/test_version_injection.py`：

```python
"""线上版本注入文本块渲染测试。"""
from zd_agent.tools.saas_version import DeployedVersion, render_deployed_version_block


def test_block_verified():
    dv = DeployedVersion(
        image_tag="hotfix-1.14.2-fix.4",
        git_ref="hotfix/1.14.2-fix.4",
        base_version="1.14.2",
        ref_verified=True,
        source="signoz",
    )
    out = render_deployed_version_block(dv)
    assert "线上部署版本" in out
    assert "hotfix-1.14.2-fix.4" in out
    assert "hotfix/1.14.2-fix.4" in out
    assert "✓" in out


def test_block_unverified():
    dv = DeployedVersion(
        image_tag="hotfix-1.14.2-fix.9",
        git_ref="hotfix-1.14.2-fix.9",
        base_version="1.14.2",
        ref_verified=False,
        source="signoz",
    )
    out = render_deployed_version_block(dv)
    assert "⚠️" in out or "未验证" in out


def test_block_fallback_source():
    dv = DeployedVersion(
        image_tag="hotfix-1.14.2-fix.3",
        git_ref="hotfix/1.14.2-fix.3",
        base_version="1.14.2",
        ref_verified=True,
        source="saas-deploy-fallback",
    )
    out = render_deployed_version_block(dv)
    assert "saas-deploy" in out
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/tools/test_version_injection.py -v`
Expected: FAIL（`ImportError: render_deployed_version_block`）

- [ ] **Step 3: 在 saas_version.py 加渲染函数**

在 `saas_version.py` 追加（紧接 `image_tag_to_git_ref` / `extract_base_version` 之后；此函数纯同步、无副作用，工具与 runner 共用）：

```python
def render_deployed_version_block(dv: "DeployedVersion") -> str:
    """把 DeployedVersion 渲染成注入 user message 的 Markdown 块。"""
    src_line = (
        "SigNoz 实时遥测" if dv.source == "signoz"
        else "saas-deploy 声明值（SigNoz 不可达，可能非实时）"
    )
    if dv.ref_verified:
        ref_line = (
            f"- 对应源码 ref：`{dv.git_ref}` ✓"
            "（git_show / git_grep 查这个 = 线上实际在跑的代码）"
        )
    else:
        ref_line = (
            f"- 对应源码 ref：`{dv.git_ref}` ⚠️ 未在 dify 仓验证到，"
            "使用前先 list_repo_tags 核对"
        )
    base = f"（基线 {dv.base_version}）" if dv.base_version else ""
    return (
        f"## 线上部署版本（cloud.dify.ai 生产 · {src_line}）\n"
        f"- dify-api 运行中镜像：`{dv.image_tag}`{base}\n"
        f"{ref_line}\n"
        f"- 需要更多细节用 saas_deployed_version 工具"
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/tools/test_version_injection.py -v`
Expected: PASS

- [ ] **Step 5: runner.py 接线（仿 customer_profile 手工拼法）**

在 `agent_loop()` 的 `user_content = "".join(user_content_parts)` 之前（即第 130 行前、customer_profile 注入之后），加版本注入：

```python
    # 线上部署版本注入（cloud.dify.ai 生产 dify-api 实际运行版本）。
    # best-effort：解析失败返回 None → 整段不注入，不影响工单处理。
    try:
        from zd_agent.tools.saas_version import (
            resolve_deployed_version,
            render_deployed_version_block,
        )
        _dv = await resolve_deployed_version()
        if _dv is not None:
            user_content_parts.append("\n\n" + render_deployed_version_block(_dv))
    except Exception as e:
        logger.warning("deployed_version_inject_failed", error=str(e))
```

- [ ] **Step 6: import 冒烟 + 全 tools 测试**

Run: `uv run python -c "import zd_agent.runtime.runner" && uv run pytest tests/tools/test_version_injection.py tests/tools/test_saas_version.py -v`
Expected: 无 import error + PASS

- [ ] **Step 7: Commit**

```bash
git add src/zd_agent/tools/saas_version.py src/zd_agent/runtime/runner.py tests/tools/test_version_injection.py
git commit -m "feat(version): inject deployed version into ticket user message

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 1: 写失败测试**

`tests/prompts/test_deployed_version_section.py`：

```python
"""deployed_version 动态 prompt 段测试。"""
from zd_agent.runtime.prompt.builder import PromptBuilder
from zd_agent.runtime.prompt.defaults import register_dynamic_sections
from zd_agent.runtime.prompt.section import PromptContext


def _render(deployed_version):
    b = PromptBuilder()
    register_dynamic_sections(b)
    ctx = PromptContext(deployed_version=deployed_version)
    return b.build(ctx)


def test_section_renders_when_version_present():
    out = _render({
        "image_tag": "hotfix-1.14.2-fix.4",
        "git_ref": "hotfix/1.14.2-fix.4",
        "base_version": "1.14.2",
        "ref_verified": True,
        "source": "signoz",
    })
    assert "hotfix-1.14.2-fix.4" in out
    assert "hotfix/1.14.2-fix.4" in out
    assert "线上部署版本" in out


def test_section_absent_when_none():
    out = _render(None)
    assert "线上部署版本" not in out


def test_section_marks_unverified_ref():
    out = _render({
        "image_tag": "hotfix-1.14.2-fix.9",
        "git_ref": "hotfix-1.14.2-fix.9",
        "base_version": "1.14.2",
        "ref_verified": False,
        "source": "signoz",
    })
    assert "未验证" in out or "⚠️" in out
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/prompts/test_deployed_version_section.py -v`
Expected: FAIL（`PromptContext` 无 `deployed_version` 参数 → TypeError）

- [ ] **Step 3: PromptContext 加字段**

`section.py` 在 `customer_profile` 行后加：

```python
    # 线上部署版本（cloud.dify.ai 生产 dify-api 实际运行版本，dispatch 前解析）
    deployed_version: dict[str, Any] | None = None
```

- [ ] **Step 4: defaults.py 注册段**

在 `register_dynamic_sections` 末尾（`customer_profile` 段注册之后）加：

```python
    def _deployed_version_section(ctx: PromptContext) -> str:
        dv = ctx.deployed_version or {}
        image_tag = dv.get("image_tag", "")
        git_ref = dv.get("git_ref", "")
        base = dv.get("base_version", "")
        verified = dv.get("ref_verified", False)
        source = dv.get("source", "")
        src_line = (
            "SigNoz 实时遥测" if source == "signoz"
            else "saas-deploy 声明值（SigNoz 不可达，可能非实时）"
        )
        ref_line = (
            f"- 对应源码 ref：`{git_ref}` ✓（git_show / git_grep 查这个 = 线上实际代码）"
            if verified else
            f"- 对应源码 ref：`{git_ref}` ⚠️ 未在 dify 仓验证到，使用前先 list_repo_tags 核对"
        )
        return (
            f"## 线上部署版本（cloud.dify.ai 生产 · {src_line}）\n"
            f"- dify-api 运行中镜像：`{image_tag}`"
            + (f"（基线 {base}）" if base else "") + "\n"
            f"{ref_line}\n"
            f"- 需要更多细节用 saas_deployed_version 工具"
        )

    builder.register(
        PromptSection(
            name="deployed_version",
            content=_deployed_version_section,
            order=22,
            condition=lambda ctx: bool(ctx.deployed_version),
        )
    )
```

- [ ] **Step 5: 运行段测试确认通过**

Run: `uv run pytest tests/prompts/test_deployed_version_section.py -v`
Expected: PASS

- [ ] **Step 6: runner.py 接线**

`runner.py` 的 `run_tool_loop` 中，`build_dynamic_user_context(...)` 调用前解析版本，并把 dict 传入。改 `dynamic_context = build_dynamic_user_context(...)` 这块：

```python
    from zd_agent.tools.saas_version import resolve_deployed_version
    from dataclasses import asdict
    _dv = await resolve_deployed_version()
    dynamic_context = build_dynamic_user_context(
        current_date=_today_str(),
        ticket_type=session.ticket_type,
        strategy_content=strategy_content,
        customer_language=session.customer_language,
        mode=session.mode,
        previous_record=session.previous_record,
        new_comments_diff=session.new_comments_diff,
        customer_profile=session.customer_profile,
        deployed_version=asdict(_dv) if _dv else None,
    )
```

- [ ] **Step 7: 运行全 prompt 测试 + 确认 runner import 无误**

Run: `uv run pytest tests/prompts/ -v && uv run python -c "import zd_agent.runtime.runner"`
Expected: PASS + 无 import error

- [ ] **Step 8: Commit**

```bash
git add src/zd_agent/runtime/prompt/section.py src/zd_agent/runtime/prompt/defaults.py src/zd_agent/runtime/runner.py tests/prompts/test_deployed_version_section.py
git commit -m "feat(version): inject deployed version as dynamic prompt section

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: SigNoz 日志查询工具（替换 MCP）

**Files:**
- Create: `src/zd_agent/tools/signoz_tools.py`
- Test: `tests/tools/test_signoz_tools.py`

**说明：** 四个工具各包 sz 一个子命令，均 `group="platform_issue"`，`max_output_tokens=8000`（沿用 MCP 限制）。env 默认 production。失败返回明确 error 文本，不抛。

- [ ] **Step 1: 写失败测试**

`tests/tools/test_signoz_tools.py`：

```python
"""SigNoz 日志查询工具（基于 sz CLI）测试。"""
import pytest

import zd_agent.tools.signoz_tools as st
from zd_agent.tools.signoz_tools import (
    SignozErrors, SignozTraceback, SignozServices, SignozQuery,
)


@pytest.mark.asyncio
async def test_errors_returns_rows(monkeypatch):
    async def fake_run_sz(*args, **kw):
        assert args[0] == "errors"
        return [{"ts": "2026-05-30 16:13", "sev": "ERROR",
                 "dep": "dify-api-oltp", "id": "x", "body": "boom"}]
    monkeypatch.setattr(st, "run_sz", fake_run_sz)

    tool = SignozErrors()
    r = await tool.execute({"hours": 1, "limit": 3}, session=None)
    assert r.success
    assert "dify-api-oltp" in r.output
    assert "boom" in r.output


@pytest.mark.asyncio
async def test_errors_passes_filters(monkeypatch):
    seen = {}
    async def fake_run_sz(*args, **kw):
        seen["args"] = args
        return []
    monkeypatch.setattr(st, "run_sz", fake_run_sz)

    tool = SignozErrors()
    await tool.execute({"tenant": "abc", "deployment": "dify-app-worker"}, session=None)
    assert "errors" in seen["args"]
    assert "--tenant" in seen["args"] and "abc" in seen["args"]
    assert "--deployment" in seen["args"] and "dify-app-worker" in seen["args"]
    # env 默认 production
    assert "--env" in seen["args"] and "production" in seen["args"]


@pytest.mark.asyncio
async def test_errors_handles_failure(monkeypatch):
    async def fake_run_sz(*args, **kw):
        return None
    monkeypatch.setattr(st, "run_sz", fake_run_sz)

    tool = SignozErrors()
    r = await tool.execute({}, session=None)
    assert not r.success
    assert r.error


@pytest.mark.asyncio
async def test_query_requires_sql(monkeypatch):
    tool = SignozQuery()
    r = await tool.execute({}, session=None)
    assert not r.success


@pytest.mark.asyncio
async def test_traceback_and_services_smoke(monkeypatch):
    async def fake_run_sz(*args, **kw):
        return []
    monkeypatch.setattr(st, "run_sz", fake_run_sz)
    assert (await SignozTraceback().execute({}, session=None)).success
    assert (await SignozServices().execute({}, session=None)).success
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/tools/test_signoz_tools.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 signoz_tools.py**

`src/zd_agent/tools/signoz_tools.py`：

```python
"""SigNoz 日志查询工具组，经 vendored sz CLI（替换已失效的 SigNoz MCP）。

sz 直接打 SigNoz v4 query_range API，绕过 signoz-mcp-server 对 EE 后端的不兼容。
四个工具对应 sz 子命令：errors / traceback / services / query(raw SQL 兜底)。
"""
from __future__ import annotations

import json
from typing import Any

from zd_agent.integrations.sz_cli import run_sz
from zd_agent.tools.base import Tool, ToolResult

_GROUP = "platform_issue"
_MAX_OUT = 8000


def _rows_to_text(rows: Any) -> str:
    """sz --json 返回的 list[dict] → 紧凑文本。空 → 提示语。"""
    if not rows:
        return "(无匹配行)"
    if not isinstance(rows, list):
        return json.dumps(rows, ensure_ascii=False)[:4000]
    lines = []
    for r in rows:
        if isinstance(r, dict):
            lines.append(" | ".join(f"{k}={v}" for k, v in r.items()))
        else:
            lines.append(str(r))
    return "\n".join(lines)


class SignozErrors(Tool):
    name = "signoz_errors"
    description = (
        "查 cloud.dify.ai 生产 ERROR/FATAL 日志（经 sz CLI）。可按 tenant_id、"
        "body 子串(grep)、k8s deployment 过滤。env 默认 production。"
        "workflow 异步任务跑在 deployment=dify-app-worker，不是 dify-api-oltp。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "tenant": {"type": "string", "description": "tenant_id 精确过滤"},
            "grep": {"type": "string", "description": "body 子串过滤"},
            "deployment": {"type": "string", "description": "k8s.deployment.name，如 dify-api-oltp / dify-app-worker"},
            "env": {"type": "string", "description": "deployment.environment，默认 production"},
            "hours": {"type": "number", "description": "lookback 小时，默认 1"},
            "limit": {"type": "integer", "description": "行上限，默认 30"},
        },
    }
    group = _GROUP
    concurrency_safe = True
    max_output_tokens = _MAX_OUT

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        sz_args = ["errors", "--env", str(args.get("env") or "production")]
        if args.get("tenant"):
            sz_args += ["--tenant", str(args["tenant"])]
        if args.get("grep"):
            sz_args += ["--grep", str(args["grep"])]
        if args.get("deployment"):
            sz_args += ["--deployment", str(args["deployment"])]
        sz_args += ["--hours", str(args.get("hours", 1))]
        sz_args += ["--limit", str(args.get("limit", 30))]
        rows = await run_sz(*sz_args)
        if rows is None:
            return ToolResult(success=False, error="sz errors 查询失败（SigNoz 不可达或 sz 缺失）")
        return ToolResult(output=_rows_to_text(rows))


class SignozTraceback(Tool):
    name = "signoz_traceback"
    description = (
        "拉完整 Python stacktrace（exception.stacktrace 字段），按 log id / "
        "tenant_id / body 子串定位。要看完整堆栈用这个，不要用 signoz_errors（body 只一行）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "精确 log id（最准）"},
            "tenant": {"type": "string"},
            "grep": {"type": "string"},
            "env": {"type": "string", "description": "默认 production"},
            "hours": {"type": "number", "description": "默认 24"},
            "limit": {"type": "integer", "description": "默认 5"},
        },
    }
    group = _GROUP
    concurrency_safe = True
    max_output_tokens = _MAX_OUT

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        sz_args = ["traceback"]
        if args.get("id"):
            sz_args += ["--id", str(args["id"])]
        else:
            sz_args += ["--env", str(args.get("env") or "production")]
            if args.get("tenant"):
                sz_args += ["--tenant", str(args["tenant"])]
            if args.get("grep"):
                sz_args += ["--grep", str(args["grep"])]
        sz_args += ["--hours", str(args.get("hours", 24))]
        sz_args += ["--limit", str(args.get("limit", 5))]
        rows = await run_sz(*sz_args)
        if rows is None:
            return ToolResult(success=False, error="sz traceback 查询失败")
        return ToolResult(output=_rows_to_text(rows))


class SignozServices(Tool):
    name = "signoz_services"
    description = "列 cloud.dify.ai 生产当前活跃 service / k8s deployment（查不到日志时用来核对正确的 deployment 名）。"
    parameters = {
        "type": "object",
        "properties": {
            "env": {"type": "string", "description": "默认 production"},
            "hours": {"type": "number", "description": "默认 1"},
        },
    }
    group = _GROUP
    concurrency_safe = True
    max_output_tokens = _MAX_OUT

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        sz_args = ["services", "--env", str(args.get("env") or "production"),
                   "--hours", str(args.get("hours", 1))]
        rows = await run_sz(*sz_args)
        if rows is None:
            return ToolResult(success=False, error="sz services 查询失败")
        return ToolResult(output=_rows_to_text(rows))


class SignozQuery(Tool):
    name = "signoz_query"
    description = (
        "对 SigNoz ClickHouse 跑任意 SELECT（兜底，前三个工具搞不定时用）。"
        "日志表 signoz_logs.distributed_logs_v2；resource 字段用 resources_string['k8s.deployment.name'] 等；"
        "log 属性用 attributes_string['tenant_id']；时间用 {start_ns}/{end_ns} 占位符（UInt64 纳秒）。"
        "结构 DESCRIBE 用 query 跑 'DESCRIBE signoz_logs.distributed_logs_v2'。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "ClickHouse SELECT，可含 {start_ns}/{end_ns}"},
            "hours": {"type": "number", "description": "{start_ns}/{end_ns} 的窗口小时，默认 1"},
        },
        "required": ["sql"],
    }
    group = _GROUP
    concurrency_safe = True
    max_output_tokens = _MAX_OUT

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        sql = args.get("sql")
        if not sql:
            return ToolResult(success=False, error="sql 参数必填")
        rows = await run_sz("query", str(sql), "--hours", str(args.get("hours", 1)))
        if rows is None:
            return ToolResult(success=False, error="sz query 执行失败（SQL 错误或 SigNoz 不可达）")
        return ToolResult(output=_rows_to_text(rows))
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/tools/test_signoz_tools.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add src/zd_agent/tools/signoz_tools.py tests/tools/test_signoz_tools.py
git commit -m "feat(signoz): sz-backed log query tools (errors/traceback/services/query)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: registry 注册新工具 + 停止 MCP

**Files:**
- Modify: `src/zd_agent/tools/registry.py`
- Test: `tests/tools/test_registry_signoz.py`

- [ ] **Step 1: 写失败测试**

`tests/tools/test_registry_signoz.py`：

```python
"""registry：注册 sz 工具 + 不再注册 MCP signoz 工具。"""
import pytest

from zd_agent.tools.registry import create_tool_registry, augment_registry_async


def test_sz_tools_registered():
    reg = create_tool_registry()
    for name in ["saas_deployed_version", "signoz_errors", "signoz_traceback",
                 "signoz_services", "signoz_query"]:
        assert name in reg, f"{name} 未注册"


@pytest.mark.asyncio
async def test_augment_does_not_spawn_mcp():
    reg = create_tool_registry()
    before = len(reg)
    await augment_registry_async(reg)  # 应为 no-op（MCP 已停用）
    assert len(reg) == before
    # 确认没有 MCP 的旧工具名
    for stale in ["signoz_search_logs", "signoz_aggregate_logs", "signoz_list_services"]:
        assert stale not in reg
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/tools/test_registry_signoz.py -v`
Expected: FAIL（新工具未注册）

- [ ] **Step 3: 改 registry.py**

import 区加：

```python
from zd_agent.tools.saas_version import SaasDeployedVersion
from zd_agent.tools.signoz_tools import (
    SignozErrors,
    SignozTraceback,
    SignozServices,
    SignozQuery,
)
```

在 `create_tool_registry()` 的「源码工具组」末尾（`GithubCodeSearch()` 注册后）加：

```python
    # 线上版本感知（SigNoz 实时遥测 + saas-deploy 兜底）
    registry.register(SaasDeployedVersion())
```

在「Grafana 监控工具组」注册块之后加：

```python
    # SigNoz 日志查询工具组（经 sz CLI，替换已失效的 SigNoz MCP）
    registry.register(SignozErrors())
    registry.register(SignozTraceback())
    registry.register(SignozServices())
    registry.register(SignozQuery())
```

把 `augment_registry_async` 改为 no-op（保留函数签名，不再 spawn MCP）：

```python
async def augment_registry_async(registry: "ToolRegistry") -> None:
    """[已停用] 原用于异步注册 SigNoz MCP 工具。

    SigNoz MCP（signoz-mcp-server）对 SigNoz EE v0.121 后端不兼容（启动即
    closed stdout），生产从未成功加载。已改用 vendored sz CLI（见 signoz_tools.py
    + saas_version.py），SigNoz 工具改为同步注册。本函数保留为 no-op 以兼容
    runner.py 的调用点；signoz_mcp_tools.py 代码保留备用，不删。
    """
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/tools/test_registry_signoz.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zd_agent/tools/registry.py tests/tools/test_registry_signoz.py
git commit -m "feat(registry): register sz-backed signoz tools, disable broken MCP spawn

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: prompt + subagent_parallel 旧工具名改写

**Files:**
- Modify: `src/zd_agent/prompts/TOOLS.md`
- Modify: `src/zd_agent/prompts/AGENTS.md`
- Modify: `src/zd_agent/prompts/references/subagent_guide.md`
- Modify: `src/zd_agent/prompts/strategies/platform_issue.md`
- Modify: `src/zd_agent/prompts/strategies/workflow.md`
- Modify: `src/zd_agent/prompts/strategies/api.md`
- Modify: `src/zd_agent/tools/subagent_parallel.py`
- Test: `tests/prompts/test_no_stale_signoz_names.py`

**说明：** 把所有引用已失效 MCP 工具名（`signoz_search_logs`/`signoz_aggregate_logs`/`signoz_list_services`/`signoz_search_traces`/`signoz_get_field_keys`/`signoz_get_field_values`）的地方改成新工具名（`signoz_errors`/`signoz_traceback`/`signoz_services`/`signoz_query`），并对齐用法。

- [ ] **Step 1: 写守卫测试（先失败）**

`tests/prompts/test_no_stale_signoz_names.py`：

```python
"""保证 prompt / 代码里无残留的已失效 SigNoz MCP 工具名。"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STALE = [
    "signoz_search_logs",
    "signoz_aggregate_logs",
    "signoz_list_services",
    "signoz_search_traces",
    "signoz_get_field_keys",
    "signoz_get_field_values",
]

SCAN = [
    ROOT / "src/zd_agent/prompts",
    ROOT / "src/zd_agent/tools/subagent_parallel.py",
]


def _iter_files():
    for p in SCAN:
        if p.is_file():
            yield p
        else:
            yield from p.rglob("*.md")
            yield from p.rglob("*.py")


@pytest.mark.parametrize("stale", STALE)
def test_no_stale_tool_names(stale):
    hits = []
    for f in _iter_files():
        if "__pycache__" in str(f):
            continue
        text = f.read_text(encoding="utf-8")
        if stale in text:
            hits.append(str(f.relative_to(ROOT)))
    assert not hits, f"残留旧工具名 {stale}: {hits}"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/prompts/test_no_stale_signoz_names.py -v`
Expected: FAIL（多处命中）

- [ ] **Step 3: 改 TOOLS.md**

把 SigNoz 段（约 30-34 行）：

```markdown
### SigNoz
- `signoz_list_services`：列可查询的 service 名
- `signoz_search_logs`：按 `service_name` + `severity`（ERROR/WARN/INFO）+ 可选 `query`
- `signoz_search_traces`：按 `service_name` 查链路
- `signoz_get_field_keys` / `signoz_get_field_values`：发现可用过滤字段
```

改为：

```markdown
### SigNoz（经 sz CLI；旧 MCP 已停用）
- `signoz_errors`：查 ERROR/FATAL 日志，可按 `tenant` / `grep`(body 子串) / `deployment` / `env`(默认 production) / `hours` / `limit` 过滤
- `signoz_traceback`：拉完整 Python stacktrace（按 `id` / `tenant` / `grep`）。要看堆栈用这个，不要用 signoz_errors
- `signoz_services`：列当前活跃 service / k8s deployment（查不到日志时核对 deployment 名）
- `signoz_query`：兜底，跑任意 ClickHouse SELECT（表 `signoz_logs.distributed_logs_v2`，resource 用 `resources_string['k8s.deployment.name']`，时间用 `{start_ns}`/`{end_ns}`）

> 版本感知：`saas_deployed_version` 查 cloud.dify.ai 生产 dify-api 当前运行版本 + 对应源码 ref。
```

- [ ] **Step 4: 改 AGENTS.md**

第 30 行 `- 日志：\`signoz_search_logs\` / \`signoz_aggregate_logs\`` 改为：

```markdown
   - 日志：`signoz_errors` / `signoz_traceback`（堆栈）/ `signoz_query`（raw SQL）
```

- [ ] **Step 5: 改 subagent_guide.md**

第 23 行 `- task='查 SigNoz 错误' → \`tools=['signoz_search_logs', 'signoz_aggregate_logs']\`` 改为：

```markdown
- task='查 SigNoz 错误' → `tools=['signoz_errors', 'signoz_traceback']`
```

- [ ] **Step 6: 改 subagent_parallel.py**

第 56-57 行 `"task='查 SigNoz 错误' → tools=['signoz_search_logs', " "'signoz_aggregate_logs']。**别图省事传 10 个**。"` 改为：

```python
                                "task='查 SigNoz 错误' → tools=['signoz_errors', "
                                "'signoz_traceback']。**别图省事传 10 个**。"
```

第 97 行 `"源码搜，['signoz_search_logs'] for 日志). Don't dump the "` 改为：

```python
                        "源码搜，['signoz_errors'] for 日志). Don't dump the "
```

- [ ] **Step 7: 改 api.md**

第 41 行 `- C. SigNoz 请求日志（5xx 才需要）` 改为：

```markdown
   - C. SigNoz 请求日志（5xx 才需要，用 `signoz_errors --deployment dify-api-oltp`）
```

- [ ] **Step 8: 改 workflow.md**

第 306 行的 SigNoz 引用，把 `dify-app-worker` 过滤说明对齐到 sz 参数。原句保留语义，把"查询规则详见 platform_issue.md 的 SigNoz 章节"改为：

```markdown
   - D. SigNoz worker 日志（若涉及异步执行）。**workflow 异步任务实际跑在 `k8s.deployment.name = dify-app-worker`，不是 `dify-api-oltp`**；用 `signoz_errors --deployment dify-app-worker`（查询规则详见 `strategies/platform_issue.md` 的 SigNoz 章节）
```

（286 行那条无具体工具名，保留不动。）

- [ ] **Step 9: 重写 platform_issue.md 的 SigNoz 章节**

把第 20-91 行整段「🔍 SigNoz 查询规则」替换为基于 sz 工具的新版：

```markdown
## 🔍 SigNoz 查询规则（经 sz CLI；旧 MCP 已停用）

> SigNoz 日志查询现在走 sz 工具（`signoz_errors` / `signoz_traceback` / `signoz_services` / `signoz_query`）。底层是 ClickHouse，字段用 `resources_string['...']`（resource）与 `attributes_string['...']`（log 属性）。

### 1. deployment 名（prod）

| 服务 | k8s.deployment.name |
|---|---|
| Dify API | `dify-api-oltp` |
| 应用消费（workflow / chat / completion 实际跑这里）| `dify-app-worker` |
| 知识库索引 | `dify-dataset-worker` |
| 触发器 | `dify-trigger-worker-*` |
| 邮件 | `dify-mail-worker` |

**典型例子**：客户报"工作流提交后 pending 不动" → `signoz_errors --deployment dify-app-worker`，**不是** `dify-api-oltp`（API 早把请求 enqueue 给 worker 了）。

### 2. 默认只查 production

`signoz_errors` / `signoz_services` 的 `env` 默认 `production`（99% 工单）。内部联调才传 `env=staging`。

### 3. 时间范围显式设

用 `hours` 参数，别依赖默认：客户报"5 分钟前" → `hours=0.25`；"今天上午" → 估算小时数。

### 4. 找不到结果时的 fallback

第一次 0 行 → **不要立即下"无异常"结论**：
1. 用 `signoz_services` 列当前活跃 deployment，核对 deployment 名拼写
2. 改用 `grep`（body 子串）或放宽 `hours` 重查
3. 实在找不到才说"未在 SigNoz 看到对应服务的日志"

### 5. 要堆栈用 traceback，不要用 errors

`signoz_errors` 的 body 只有一行摘要。要完整 Python stacktrace 用 `signoz_traceback`（按 `tenant` / `grep` / 精确 `id`）。

### 6. 镜像版本确认

判断"客户是不是在某个特定 hotfix 上" → 用 `saas_deployed_version` 工具（直接给 dify-api 运行镜像 tag + 源码 ref）。要查历史时间点的版本可 `signoz_query` 跑：
\`\`\`sql
SELECT DISTINCT resources_string['container.image.tag']
FROM signoz_logs.distributed_logs_v2
WHERE timestamp >= {start_ns}
  AND resources_string['k8s.deployment.name'] = 'dify-api-oltp'
\`\`\`

### 7. raw SQL 兜底

复杂聚合用 `signoz_query`：先 `SELECT count(*) ... GROUP BY resources_string['k8s.deployment.name'], severity_text` 看分布，再钻具体日志（先聚合后明细，省 token）。

### 8. SigNoz 整体不可达

若 `signoz_services` 也返回空/失败 → 怀疑 sz 不可用或 ingest 端故障，参考 #2375 路径（agent 修不了，需 SaaS observability 同事介入）。
```

（保留该文件「关键步骤」「回复原则」等其余段落不动，只替换 SigNoz 规则段。注意第 98-109 行若引用 signoz_search_logs/aggregate 也一并改为 signoz_errors/signoz_query。）

- [ ] **Step 10: 运行守卫测试确认通过**

Run: `uv run pytest tests/prompts/test_no_stale_signoz_names.py -v`
Expected: PASS（无残留）

- [ ] **Step 11: Commit**

```bash
git add src/zd_agent/prompts/ src/zd_agent/tools/subagent_parallel.py tests/prompts/test_no_stale_signoz_names.py
git commit -m "docs(prompt): rewrite SigNoz tool refs from MCP names to sz tools

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 全量验证

**Files:** 无新增

- [ ] **Step 0: 清理陈旧 pyc 缓存**

旧的 `test_signoz.py` / `test_signoz_tools.py` 源文件已删除，但 `__pycache__` 里还留着它们的 `.pyc`。新建的 `tests/tools/test_signoz_tools.py` 与旧缓存同名，先清掉避免 pytest 收集到陈旧缓存：

Run: `find tests -name "*.pyc" -path "*signoz*" -delete; find tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo cleaned`
Expected: `cleaned`

- [ ] **Step 1: 跑全测试套件**

Run: `uv run pytest tests/ -q`
Expected: 全绿（含新增测试文件）。

**注（实读确认）：** 无任何测试调用 `augment_registry_async` / `discover_signoz_mcp_tools`（grep 0 命中），故把 `augment_registry_async` 改 no-op 不破坏现有测试。`signoz_mcp_tools.py` / `mcp_subprocess.py` 源码保留备用，但其旧测试 `.py` 已不存在（仅剩 `.pyc` 缓存，Step 0 已清）。若有意外失败先排查 import 链。

- [ ] **Step 2: import 冒烟**

Run:
```bash
uv run python -c "import zd_agent.runtime.runner; import zd_agent.tools.registry; from zd_agent.tools.registry import create_tool_registry; r=create_tool_registry(); print('tools:', len(r)); assert 'signoz_errors' in r and 'saas_deployed_version' in r; print('OK')"
```
Expected: 打印 tools 数 + `OK`

- [ ] **Step 3: 残留旧名全仓扫描**

Run: `grep -rn "signoz_search_logs\|signoz_aggregate_logs\|signoz_list_services\|signoz_get_field" src/zd_agent --include='*.py' --include='*.md' | grep -v __pycache__ | grep -v signoz_mcp_tools.py`
Expected: 无输出（signoz_mcp_tools.py 保留，允许残留）

- [ ] **Step 4: Dockerfile COPY 确认**

Run: `grep -n "COPY scripts" deploy/Dockerfile`
Expected: `COPY scripts ./scripts`（已存在，sz 自动进镜像，无需改）

- [ ] **Step 5: 最终 commit（若有未提交收尾）**

```bash
git add -A
git commit -m "test: full suite green for sz CLI integration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>" || echo "nothing to commit"
```

---

## 部署（实现完成后，单独执行）

1. 确认 `scripts/sz` 已在仓库
2. build + push 镜像：复用现有一键部署（`deploy/RUNBOOK.md`）
3. 服务器 pull + 重启容器
4. 验证：容器内 `docker exec zd-agent /app/.venv/bin/python /app/scripts/sz services --env production --hours 1` 能返回行
5. 处理一张测试工单，确认 internal note 里出现「线上部署版本」段
```
