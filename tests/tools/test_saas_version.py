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
