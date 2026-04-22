"""Test psql subprocess executor with mocked subprocess."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.integrations.postgres import (
    PsqlResult,
    run_psql,
    TeleportAuthExpired,
)


def _make_mock_proc(*, returncode: int, stdout: bytes, stderr: bytes):
    """Construct a mock process whose communicate() is awaitable."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_run_psql_success():
    csv_out = b"id,name\n1,alice\n2,bob\n"
    mock_proc = _make_mock_proc(returncode=0, stdout=csv_out, stderr=b"")
    with patch(
        "zd_agent.integrations.postgres.asyncio.create_subprocess_exec",
        AsyncMock(return_value=mock_proc),
    ):
        result = await run_psql(
            host="localhost", port=56061, user="read",
            password="pw", database="langgenius",
            sql="SELECT id, name FROM tenants",
            timeout=30, max_rows=1000,
        )
    assert isinstance(result, PsqlResult)
    assert result.row_count == 2
    assert "alice" in result.csv
    assert result.truncated is False


@pytest.mark.asyncio
async def test_run_psql_teleport_expired_connection_refused():
    err = b"psql: error: connection to server at \"localhost\" (127.0.0.1), port 56061 failed: Connection refused"
    mock_proc = _make_mock_proc(returncode=2, stdout=b"", stderr=err)
    with patch(
        "zd_agent.integrations.postgres.asyncio.create_subprocess_exec",
        AsyncMock(return_value=mock_proc),
    ):
        with pytest.raises(TeleportAuthExpired):
            await run_psql(
                host="localhost", port=56061, user="read",
                password="pw", database="langgenius",
                sql="SELECT 1", timeout=30, max_rows=1000,
            )


@pytest.mark.asyncio
async def test_run_psql_other_error_is_runtime_error():
    err = b"psql: ERROR:  relation \"nonexistent_table\" does not exist"
    mock_proc = _make_mock_proc(returncode=1, stdout=b"", stderr=err)
    with patch(
        "zd_agent.integrations.postgres.asyncio.create_subprocess_exec",
        AsyncMock(return_value=mock_proc),
    ):
        with pytest.raises(RuntimeError, match="rc=1"):
            await run_psql(
                host="localhost", port=56061, user="read",
                password="pw", database="langgenius",
                sql="SELECT * FROM nonexistent_table",
                timeout=30, max_rows=1000,
            )


@pytest.mark.asyncio
async def test_run_psql_truncates_to_max_rows():
    # 5 data rows, max_rows=3
    csv_out = b"id\n1\n2\n3\n4\n5\n"
    mock_proc = _make_mock_proc(returncode=0, stdout=csv_out, stderr=b"")
    with patch(
        "zd_agent.integrations.postgres.asyncio.create_subprocess_exec",
        AsyncMock(return_value=mock_proc),
    ):
        result = await run_psql(
            host="localhost", port=56061, user="read",
            password="pw", database="langgenius",
            sql="SELECT id FROM tenants", timeout=30, max_rows=3,
        )
    assert result.truncated is True
    assert result.row_count == 3
