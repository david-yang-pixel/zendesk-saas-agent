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
