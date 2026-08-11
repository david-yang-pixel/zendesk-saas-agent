from __future__ import annotations

import ast
from pathlib import Path
import sys


CASEWORK = Path(__file__).parents[2] / "src" / "zd_agent" / "casework"
SOURCE_ROOT = Path(__file__).parents[2] / "src"
CORE_LAYERS = ("domain", "policies", "ports", "application")
MAX_MODULE_LINES = 400
MAX_FUNCTION_LINES = 50

_ALLOWED_PROJECT_PREFIXES = {
    "domain": ("pydantic", "zd_agent.casework.domain"),
    "policies": ("zd_agent.casework.domain",),
    "ports": ("zd_agent.casework.domain",),
    "application": (
        "zd_agent.casework.application",
        "zd_agent.casework.domain",
        "zd_agent.casework.policies",
        "zd_agent.casework.ports",
    ),
}

_PRODUCTION_ROOTS = {
    "zd_agent.runtime.runner",
    "zd_agent.services.agent_management",
    "zd_agent.services.app_bootstrap",
    "zd_agent.services.external_ownership",
    "zd_agent.services.saas_app",
    "zd_agent.services.silence_waker",
    "zd_agent.services.trigger_consumer",
    "zd_agent.services.trigger_message_handler",
    "zd_agent.services.waiting_poller",
}

_FORBIDDEN_V2_DEPENDENCIES = {
    "zd_agent.casework.adapters.shadow",
    "zd_agent.casework.domain.shadow",
    "zd_agent.runtime.casework_shadow",
    "zd_agent.runtime.subagent",
    "zd_agent.runtime.tool_loop",
    "zd_agent.services.ask_notifications",
    "zd_agent.services.escalation",
    "zd_agent.services.silence_expiry",
    "zd_agent.services.trigger_actions",
    "zd_agent.services.verdict_classification",
    "zd_agent.services.verdict_dispatch",
    "zd_agent.tools.registry",
}

_CASEWORK_EFFECT_ROOTS = {"zd_agent.casework.adapters.effects"}
_FORBIDDEN_CASEWORK_PUBLIC_DELIVERY_DEPENDENCIES = {
    "zd_agent.services.public_reply_handoff_effects",
    "zd_agent.tools.zendesk_public_reply",
}


def _module_names(tree: ast.Module) -> tuple[str, ...]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return tuple(names)


def _project_module_path(module: str) -> Path | None:
    relative = Path(*module.split("."))
    module_file = SOURCE_ROOT / relative.with_suffix(".py")
    if module_file.is_file():
        return module_file
    package_file = SOURCE_ROOT / relative / "__init__.py"
    return package_file if package_file.is_file() else None


def _project_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            candidates = (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            base = node.module
            candidates = (
                candidate
                for alias in node.names
                for candidate in (base, f"{base}.{alias.name}")
            )
        else:
            continue
        for candidate in candidates:
            if candidate.startswith("zd_agent.") and _project_module_path(candidate):
                imports.add(candidate)
    return imports


def _reachable_project_modules(roots: set[str]) -> set[str]:
    reachable: set[str] = set()
    pending = list(roots)
    while pending:
        module = pending.pop()
        if module in reachable:
            continue
        path = _project_module_path(module)
        if path is None:
            raise AssertionError(f"production root does not exist: {module}")
        reachable.add(module)
        pending.extend(_project_imports(path) - reachable)
    return reachable


def _source_files(layer: str) -> tuple[Path, ...]:
    return tuple(sorted((CASEWORK / layer).glob("*.py")))


def _allowed_import(module: str, project_prefixes: tuple[str, ...]) -> bool:
    root = module.split(".", maxsplit=1)[0]
    return root in sys.stdlib_module_names or module.startswith(project_prefixes)


def test_core_dependencies_point_inward() -> None:
    violations: list[str] = []
    for layer in CORE_LAYERS:
        project_prefixes = _ALLOWED_PROJECT_PREFIXES[layer]
        for path in _source_files(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module in _module_names(tree):
                if not _allowed_import(module, project_prefixes):
                    violations.append(f"{path.relative_to(CASEWORK)} imports {module}")
    assert violations == []


def test_domain_and_application_do_not_use_any() -> None:
    violations: list[str] = []
    for layer in ("domain", "application"):
        for path in _source_files(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "Any":
                    violations.append(f"{path.relative_to(CASEWORK)}:{node.lineno}")
    assert violations == []


def test_core_modules_and_functions_stay_focused() -> None:
    violations: list[str] = []
    for layer in CORE_LAYERS:
        for path in _source_files(layer):
            source = path.read_text(encoding="utf-8")
            if len(source.splitlines()) > MAX_MODULE_LINES:
                violations.append(f"{path.relative_to(CASEWORK)} exceeds {MAX_MODULE_LINES} lines")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    end = node.end_lineno or node.lineno
                    if end - node.lineno + 1 > MAX_FUNCTION_LINES:
                        violations.append(
                            f"{path.relative_to(CASEWORK)}:{node.lineno} "
                            f"{node.name} exceeds {MAX_FUNCTION_LINES} lines"
                        )
    assert violations == []


def test_v2_production_roots_cannot_reach_legacy_decision_runtime() -> None:
    """Historical outbox repair may remain, but old decision code cannot run."""
    reachable = _reachable_project_modules(_PRODUCTION_ROOTS)
    assert sorted(reachable & _FORBIDDEN_V2_DEPENDENCIES) == []


def test_casework_effects_cannot_reach_legacy_public_delivery() -> None:
    reachable = _reachable_project_modules(_CASEWORK_EFFECT_ROOTS)
    assert sorted(
        reachable & _FORBIDDEN_CASEWORK_PUBLIC_DELIVERY_DEPENDENCIES
    ) == []
