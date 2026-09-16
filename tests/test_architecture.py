"""Architectural guards: the domain stays pure, and every unit stays documented.

These are gates rather than tests of behaviour. The first one is what makes the
claim in ``model/__init__.py`` true rather than aspirational, and it fails the
moment somebody reaches for a Home Assistant helper inside the calibration
mathematics. The second enforces the docstring rule the project runs on.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

COMPONENT_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "neverdry_calibrator"
MODEL_ROOT = COMPONENT_ROOT / "model"

#: Modules the pure domain is allowed to import, beyond its own package.
ALLOWED_DOMAIN_IMPORTS = {
    "__future__",
    "abc",
    "collections",
    "collections.abc",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "math",
    "statistics",
    "typing",
}


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names a file imports, excluding relative imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module)
    return roots


@pytest.mark.parametrize("path", sorted(MODEL_ROOT.glob("*.py")), ids=lambda path: path.name)
def test_the_domain_never_imports_home_assistant(path: Path):
    """The calibration mathematics must run, and be testable, without Home Assistant."""
    for module in _imported_roots(path):
        assert not module.startswith("homeassistant"), f"{path.name} imports {module}"
        assert module in ALLOWED_DOMAIN_IMPORTS, f"{path.name} imports unexpected module {module}"


@pytest.mark.parametrize(
    "path",
    sorted(COMPONENT_ROOT.rglob("*.py")),
    ids=lambda path: str(path.relative_to(COMPONENT_ROOT)),
)
def test_every_module_class_and_function_has_a_docstring(path: Path):
    """A unit without a docstring is a blocker in this codebase, not a nit."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert ast.get_docstring(tree), f"{path.name} has no module docstring"
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            assert ast.get_docstring(node), f"{path.name}:{node.lineno} {node.name} has no docstring"


def test_the_soil_reservoir_has_exactly_one_home():
    """The deficit to moisture conversion must not be copied outside ``soil.py``.

    Two copies of this formula is how a deficit gets compared against a moisture
    defined on a different reservoir, which is the bug the whole soil profile
    object exists to make impossible.
    """
    offenders = []
    for path in COMPONENT_ROOT.rglob("*.py"):
        if path.name == "soil.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "field_capacity - " in text and "root_depth" in text:
            offenders.append(str(path.relative_to(COMPONENT_ROOT)))
    assert not offenders, f"deficit/moisture conversion duplicated in {offenders}"
