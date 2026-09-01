"""Static checks for the Greenfield process boundaries.

The four application images communicate through versioned JSON contracts and
HTTP/queue adapters.  An application must not import another application's
runtime package, and the new tree must never reach into the legacy agent.
"""

from __future__ import annotations

import ast
from pathlib import Path

GREENFIELD_ROOT = Path(__file__).parents[2]
APPLICATION_ROOTS = {
    "casework_ingress": GREENFIELD_ROOT / "apps" / "ingress",
    "casework_harness": GREENFIELD_ROOT / "apps" / "harness",
    "casework_sandbox": GREENFIELD_ROOT / "apps" / "sandbox",
    "casework_admin": GREENFIELD_ROOT / "apps" / "admin-api",
}


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py")) if root.exists() else []


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _module_roots(modules: set[str]) -> set[str]:
    return {module.split(".", 1)[0] for module in modules}


def test_greenfield_does_not_import_legacy_agent() -> None:
    """The greenfield source cannot accidentally reuse ``src/zd_agent``."""

    source_roots = [GREENFIELD_ROOT / "apps", GREENFIELD_ROOT / "packages"]
    for source_root in source_roots:
        for path in _python_files(source_root):
            imported = _imported_modules(path)
            assert not any(
                module == "zd_agent"
                or module.startswith("zd_agent.")
                or module.startswith("src.zd_agent.")
                for module in imported
            )


def test_app_runtimes_do_not_import_each_other() -> None:
    """Cross-image calls go through contracts/adapters, never app imports."""

    app_names = set(APPLICATION_ROOTS)
    for own_package, app_root in APPLICATION_ROOTS.items():
        source_root = app_root / "src"
        for path in _python_files(source_root):
            foreign = _module_roots(_imported_modules(path)) & (
                app_names - {own_package}
            )
            assert not foreign, (
                f"{path.relative_to(GREENFIELD_ROOT)} imports app runtime(s) "
                f"{', '.join(sorted(foreign))}"
            )


def test_all_runtime_apps_have_isolated_source_roots() -> None:
    """The image targets have explicit source roots instead of legacy paths."""

    for package, app_root in APPLICATION_ROOTS.items():
        source_root = app_root / "src" / package
        assert source_root.is_dir(), f"missing isolated source root: {source_root}"
