from __future__ import annotations

import json
from pathlib import Path

import pytest
from deploy.release import load_release_manifest, render_environment


def test_release_manifest_renders_only_digest_pinned_images() -> None:
    fixture = Path(__file__).parents[3] / "deploy/release.manifest.example.yaml"

    manifest = load_release_manifest(fixture)
    environment = render_environment(manifest)

    revision = "e9d4958c52253c6ca903adf5727dfcc770f62216"
    assert environment["CASEWORK_RELEASE_REVISION"] == revision
    assert environment["CASEWORK_CONFIG_REVISION"] == revision
    assert environment["CASEWORK_MIGRATION_REVISION"] == "0005_run_telemetry"
    assert set(environment) == {
        "CASEWORK_ADMIN_IMAGE",
        "CASEWORK_CONFIG_REVISION",
        "CASEWORK_HARNESS_IMAGE",
        "CASEWORK_INGRESS_IMAGE",
        "CASEWORK_MIGRATION_REVISION",
        "CASEWORK_RELEASE_REVISION",
        "CASEWORK_SANDBOX_IMAGE",
    }
    for name, value in environment.items():
        if name.endswith("_IMAGE"):
            assert "@sha256:" in value
            assert ":latest" not in value


def test_release_manifest_rejects_mutable_or_missing_image_digest(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "invalid.yaml"
    valid_digest = "sha256:" + ("a" * 64)
    images = {
        name: {
            "repository": f"repo/{name}",
            "tag": "v1",
            "digest": valid_digest,
        }
        for name in ("ingress", "harness", "sandbox", "admin")
    }
    images["ingress"] = {
        "repository": "repo/ingress",
        "tag": "latest",
        "digest": "",
    }
    manifest.write_text(
        json.dumps(
            {
                "apiVersion": "casework.dify.ai/v1alpha1",
                "kind": "ReleaseManifest",
                "spec": {
                    "gitRevision": "abc1234",
                    "migrationRevision": "0001_greenfield_initial",
                    "images": images,
                },
            }
        )
    )

    with pytest.raises(ValueError, match="digest"):
        load_release_manifest(manifest)


def test_release_manifest_requires_full_commit_sha(tmp_path: Path) -> None:
    manifest = tmp_path / "invalid-revision.yaml"
    fixture = Path(__file__).parents[3] / "deploy/release.manifest.example.yaml"
    manifest.write_text(
        fixture.read_text().replace(
            "e9d4958c52253c6ca903adf5727dfcc770f62216", "abc1234"
        )
    )

    with pytest.raises(ValueError, match="full commit SHA"):
        load_release_manifest(manifest)
