from pathlib import Path

from casework_agent_core.skills import SkillLoader


def test_skill_loader_reads_manifest_first_and_loads_content_progressively(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "workspace"
    skill_dir.mkdir()
    (skill_dir / "manifest.json").write_text(
        '{"name":"workspace","version":"1.0.0","description":"Find workspaces",'
        '"dependencies":[],"compatible_tools":["workspace_lookup"]}',
        encoding="utf-8",
    )
    (skill_dir / "content.md").write_text(
        "Use the workspace lookup tool.", encoding="utf-8"
    )

    loader = SkillLoader(tmp_path)
    manifest = loader.manifest("workspace")
    assert manifest.name == "workspace"
    assert loader.loaded_content == ()

    loaded = loader.load("workspace")
    assert loaded.content == "Use the workspace lookup tool."
    assert loader.loaded_content == ("workspace",)
