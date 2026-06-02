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
