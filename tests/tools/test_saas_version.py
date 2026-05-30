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
