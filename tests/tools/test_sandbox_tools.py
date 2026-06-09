from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zd_agent.tools.sandbox_tools import SandboxList


@pytest.fixture
def sandbox_session(tmp_path: Path):
    session = MagicMock()
    session.sandbox_dir = tmp_path
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
    return session


@pytest.mark.asyncio
async def test_sandbox_list_rejects_invalid_numeric_args(sandbox_session):
    tool = SandboxList()

    result = await tool.execute(
        {"path": ".", "max_depth": "deep", "limit": "many"},
        sandbox_session,
    )

    assert result.success is False
    assert "max_depth" in (result.error or "")
