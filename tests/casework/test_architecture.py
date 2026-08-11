from __future__ import annotations

import ast
from pathlib import Path
import sys


CASEWORK = Path(__file__).parents[2] / "src" / "zd_agent" / "casework"
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


def _module_names(tree: ast.Module) -> tuple[str, ...]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return tuple(names)


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
