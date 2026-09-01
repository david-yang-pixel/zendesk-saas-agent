"""Validate immutable four-image manifests and render deployment inputs.

This tool deliberately never invokes Docker, Compose, a registry, or Zendesk.
Operators can inspect its deterministic output before a separate deployment
step consumes it.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

IMAGE_NAMES = ("ingress", "harness", "sandbox", "admin")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ReleaseImage:
    repository: str
    tag: str
    digest: str

    @property
    def immutable_reference(self) -> str:
        return f"{self.repository}@{self.digest}"


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    git_revision: str
    migration_revision: str
    images: dict[str, ReleaseImage]


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"release manifest {field} must be an object")
    return cast(dict[str, Any], value)


def _required_string(mapping: dict[str, Any], name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"release manifest requires {name}")
    return value.strip()


def load_release_manifest(path: Path) -> ReleaseManifest:
    raw = _mapping(yaml.safe_load(path.read_text()), field="root")
    if raw.get("apiVersion") != "casework.dify.ai/v1alpha1":
        raise ValueError("unsupported release manifest apiVersion")
    if raw.get("kind") != "ReleaseManifest":
        raise ValueError("unsupported release manifest kind")
    spec = _mapping(raw.get("spec"), field="spec")
    images_raw = _mapping(spec.get("images"), field="spec.images")
    if set(images_raw) != set(IMAGE_NAMES):
        raise ValueError("release manifest must contain exactly four images")

    images: dict[str, ReleaseImage] = {}
    for name in IMAGE_NAMES:
        image = _mapping(images_raw[name], field=f"spec.images.{name}")
        repository = _required_string(image, "repository")
        tag = _required_string(image, "tag")
        digest = _required_string(image, "digest")
        if tag.casefold() == "latest":
            raise ValueError(f"release image {name} uses mutable latest tag")
        if not _DIGEST.fullmatch(digest):
            raise ValueError(f"release image {name} requires a sha256 digest")
        if "@" in repository:
            raise ValueError(f"release image {name} repository contains a digest")
        images[name] = ReleaseImage(
            repository=repository,
            tag=tag,
            digest=digest,
        )

    return ReleaseManifest(
        git_revision=_required_string(spec, "gitRevision"),
        migration_revision=_required_string(spec, "migrationRevision"),
        images=images,
    )


def render_environment(manifest: ReleaseManifest) -> dict[str, str]:
    environment = {
        f"CASEWORK_{name.upper()}_IMAGE": image.immutable_reference
        for name, image in manifest.images.items()
    }
    environment["CASEWORK_RELEASE_REVISION"] = manifest.git_revision
    environment["CASEWORK_MIGRATION_REVISION"] = manifest.migration_revision
    return dict(sorted(environment.items()))


def render_dotenv(manifest: ReleaseManifest) -> str:
    return "\n".join(
        f"{name}={value}" for name, value in render_environment(manifest).items()
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render digest-pinned Casework release inputs"
    )
    parser.add_argument(
        "mode",
        choices=("release", "rollback"),
        help="rollback renders the explicitly supplied previous manifest",
    )
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(render_dotenv(load_release_manifest(args.manifest)), end="")


if __name__ == "__main__":
    main()
