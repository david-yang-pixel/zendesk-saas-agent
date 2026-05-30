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
