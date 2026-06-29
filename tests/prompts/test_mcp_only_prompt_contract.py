from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROMPT_ROOT = ROOT / "src/zd_agent/prompts"


def _prompt_text() -> str:
    parts: list[str] = []
    for path in sorted(PROMPT_ROOT.rglob("*.md")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_prompts_do_not_advertise_removed_local_cli_tools() -> None:
    text = _prompt_text()

    forbidden = [
        "zd_kb_cli_exec",
        "zd ticket",
        "zd attachments",
        "CLI 备选",
        "本地 `sz` CLI",
        "SigNoz Web UI",
        "WebSearch",
        "WebFetch",
    ]
    for phrase in forbidden:
        assert phrase not in text


def test_prompts_declare_remote_mcp_as_external_tool_boundary() -> None:
    text = (PROMPT_ROOT / "TOOLS.md").read_text(encoding="utf-8")

    assert "外部系统工具统一走远程 MCP" in text
    assert "Zendesk / Help Center" in text
    assert "Zendesk MCP" in text
    assert "GitHub MCP" in text
    assert "Docs Tool Service MCP" in text
    assert "Notification Service MCP" in text
    assert "Sandbox Worker MCP" in text
