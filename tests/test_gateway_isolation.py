"""The model execution boundary must not depend on dashboard packages."""

import ast
from pathlib import Path


def test_execution_boundaries_do_not_import_dashboard_modules():
    root = Path(__file__).parents[1] / "src" / "balatro_horizons"
    forbidden = ("balatro_horizons.api", "balatro_horizons.workbench")
    for package in ("game", "harness"):
        for path in (root / package).rglob("*.py"):
            tree = ast.parse(path.read_text())
            imports = [
                node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
            ]
            for node in imports:
                if isinstance(node, ast.ImportFrom):
                    modules = (node.module or "",)
                else:
                    modules = tuple(alias.name for alias in node.names)
                assert not any(module.startswith(forbidden) for module in modules), path
