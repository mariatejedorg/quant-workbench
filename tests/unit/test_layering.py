"""Architecture fitness test: the layering rules of docs/adr/0001 are enforced, not hoped for.

Each rule is checked by parsing every module's imports with :mod:`ast`, so the test
needs no third-party tooling and fails with the exact offending file and import.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PACKAGE = "quant_workbench"
SRC = Path(__file__).resolve().parents[2] / "src" / PACKAGE
STDLIB = set(sys.stdlib_module_names) | {"__future__"}
QT = ("PySide6", "PyQt5", "PyQt6", "shiboken6")

# layer -> internal layers it is allowed to import from (besides itself)
ALLOWED_INTERNAL: dict[str, set[str]] = {
    "domain": set(),
    "application": {"domain"},
    "infrastructure": {"domain", "application"},
    "cli": {"domain", "application", "bootstrap"},
    "ui": {"domain", "application", "bootstrap"},
    "bootstrap": {"domain", "application", "infrastructure"},
}


def _modules(layer: str) -> list[Path]:
    target = SRC / f"{layer}.py" if (SRC / f"{layer}.py").exists() else SRC / layer
    return [target] if target.is_file() else sorted(target.rglob("*.py"))


def _imports(path: Path) -> list[tuple[str, int]]:
    """Return ``(dotted module name, line)`` for every absolute import in ``path``."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.module, node.lineno))
    return found


def _internal_layer(module: str) -> str | None:
    parts = module.split(".")
    if parts[0] != PACKAGE:
        return None
    return parts[1] if len(parts) > 1 else "root"


@pytest.mark.parametrize("layer", sorted(ALLOWED_INTERNAL))
def test_layer_only_imports_allowed_layers(layer: str) -> None:
    violations: list[str] = []
    for path in _modules(layer):
        for module, line in _imports(path):
            target = _internal_layer(module)
            if target is None or target in {layer, "root"}:
                continue
            if target not in ALLOWED_INTERNAL[layer]:
                violations.append(f"{path.relative_to(SRC)}:{line} imports {module}")
    assert not violations, f"layer '{layer}' breaks the dependency rule:\n" + "\n".join(violations)


def test_domain_uses_only_the_standard_library() -> None:
    violations = [
        f"{path.relative_to(SRC)}:{line} imports {module}"
        for path in _modules("domain")
        for module, line in _imports(path)
        if module.split(".")[0] not in STDLIB and _internal_layer(module) != "domain"
    ]
    assert not violations, "domain must stay dependency-free:\n" + "\n".join(violations)


@pytest.mark.parametrize("layer", ["domain", "application", "infrastructure", "cli", "bootstrap"])
def test_qt_is_confined_to_the_ui_layer(layer: str) -> None:
    violations = [
        f"{path.relative_to(SRC)}:{line} imports {module}"
        for path in _modules(layer)
        for module, line in _imports(path)
        if module.split(".")[0] in QT
    ]
    assert not violations, "Qt leaked out of the ui layer:\n" + "\n".join(violations)
