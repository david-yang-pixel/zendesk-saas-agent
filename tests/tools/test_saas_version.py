"""saas_version 模块测试。"""
import pytest

from zd_agent.tools.saas_version import (
    DeployedVersion,
    _VERSION_SQL,
    _select_primary_image_tag,
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


def test_version_sql_ranks_tags_by_count_then_latest():
    assert "GROUP BY image_tag" in _VERSION_SQL
    assert "count() AS log_count" in _VERSION_SQL
    assert "max(timestamp) AS latest_ts" in _VERSION_SQL
    assert "ORDER BY log_count DESC, latest_ts DESC" in _VERSION_SQL


def test_select_primary_image_tag_prefers_count_then_latest():
    rows = [
        {"image_tag": "hotfix-1.14.2-fix.3", "log_count": 200, "latest_ts": 300},
        {"image_tag": "hotfix-1.14.2-fix.4", "log_count": 500, "latest_ts": 100},
        {"image_tag": "hotfix-1.14.2-fix.5", "log_count": 500, "latest_ts": 400},
    ]

    assert _select_primary_image_tag(rows) == "hotfix-1.14.2-fix.5"


def test_select_primary_image_tag_ignores_empty_tags_and_keeps_legacy_rows():
    rows = [
        {"image_tag": "", "log_count": 999, "latest_ts": 999},
        {"image_tag": "hotfix-1.14.2-fix.4"},
    ]

    assert _select_primary_image_tag(rows) == "hotfix-1.14.2-fix.4"


import zd_agent.tools.saas_version as sv


@pytest.fixture(autouse=True)
def _clear_cache():
    sv._reset_cache_for_test()
    yield
    sv._reset_cache_for_test()


@pytest.mark.asyncio
async def test_resolve_from_signoz(monkeypatch):
    async def fake_run_signoz_sz(*args, **kw):
        return [
            {
                "image_tag": "hotfix-1.14.2-fix.3",
                "service_version": "hotfix-1.14.2-fix.3",
                "log_count": 100,
                "latest_ts": 300,
            },
            {
                "image_tag": "hotfix-1.14.2-fix.4",
                "service_version": "hotfix-1.14.2-fix.4",
                "log_count": 200,
                "latest_ts": 200,
            },
        ]
    async def fake_verify(ref):
        return True
    monkeypatch.setattr(sv, "run_signoz_sz", fake_run_signoz_sz)
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
    async def fake_run_signoz_sz(*args, **kw):
        return None  # SigNoz 失败
    async def fake_fallback():
        return "hotfix-1.14.2-fix.3"
    async def fake_verify(ref):
        return True
    monkeypatch.setattr(sv, "run_signoz_sz", fake_run_signoz_sz)
    monkeypatch.setattr(sv, "_read_saas_deploy_tag", fake_fallback)
    monkeypatch.setattr(sv, "_verify_ref", fake_verify)

    dv = await sv.resolve_deployed_version(use_cache=False)
    assert dv is not None
    assert dv.image_tag == "hotfix-1.14.2-fix.3"
    assert dv.source == "saas-deploy-fallback"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_all_fail(monkeypatch):
    async def fake_run_signoz_sz(*args, **kw):
        return None
    async def fake_fallback():
        return None
    monkeypatch.setattr(sv, "run_signoz_sz", fake_run_signoz_sz)
    monkeypatch.setattr(sv, "_read_saas_deploy_tag", fake_fallback)

    dv = await sv.resolve_deployed_version(use_cache=False)
    assert dv is None


@pytest.mark.asyncio
async def test_resolve_uses_cache(monkeypatch):
    calls = {"n": 0}
    async def fake_run_signoz_sz(*args, **kw):
        calls["n"] += 1
        return [{"image_tag": "hotfix-1.14.2-fix.4"}]
    async def fake_verify(ref):
        return True
    monkeypatch.setattr(sv, "run_signoz_sz", fake_run_signoz_sz)
    monkeypatch.setattr(sv, "_verify_ref", fake_verify)

    await sv.resolve_deployed_version(use_cache=True)
    await sv.resolve_deployed_version(use_cache=True)
    assert calls["n"] == 1  # 第二次命中缓存


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
async def test_tool_uses_source_mcp_when_configured(monkeypatch):
    async def fake_call_source_tool(name, arguments):
        assert name == "source_runtime_ref"
        assert arguments == {"repo": "dify"}
        return {
            "success": True,
            "data": {
                "repo": "dify",
                "deployment": "dify-api-oltp",
                "image_tag": "hotfix-1.14.2-fix.4",
                "base_version": "1.14.2",
                "ref": "hotfix/1.14.2-fix.4",
                "commit": "abc123def456",
                "source": "signoz",
            },
        }

    monkeypatch.setattr(sv, "source_mcp_enabled", lambda: True)
    monkeypatch.setattr(sv, "call_source_tool", fake_call_source_tool)

    tool = SaasDeployedVersion()
    result = await tool.execute({}, session=None)

    assert result.success
    assert "hotfix-1.14.2-fix.4" in result.output
    assert "hotfix/1.14.2-fix.4" in result.output
    assert "Source MCP" in result.output


@pytest.mark.asyncio
async def test_tool_handles_none(monkeypatch):
    async def fake_resolve(*, use_cache=True):
        return None
    monkeypatch.setattr(sv, "resolve_deployed_version", fake_resolve)

    tool = SaasDeployedVersion()
    result = await tool.execute({}, session=None)
    assert "未能确定" in result.output or result.error
