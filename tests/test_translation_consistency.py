"""Guard against select options that lose their labels to a missing translation.

Home Assistant lets a ``SelectSelector`` replace inline labels with a
``translation_key`` resolved from the translation file. If the key is referenced
and the file has no ``selector`` section for it, the dropdown silently shows raw
option values such as ``clay`` instead of readable labels. Nothing else catches
this, and the guard is a no-op while the flow uses inline labels, which is the
point: it starts working the moment someone migrates a selector.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

COMPONENT_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "neverdry_calibrator"


def _select_translation_keys() -> list[str]:
    """Every ``translation_key`` passed to a SelectSelectorConfig in the config flow."""
    tree = ast.parse((COMPONENT_ROOT / "config_flow.py").read_text(encoding="utf-8"))
    keys: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name != "SelectSelectorConfig":
            continue
        for keyword in node.keywords:
            if keyword.arg == "translation_key" and isinstance(keyword.value, ast.Constant):
                keys.append(keyword.value.value)
    return keys


def test_every_select_translation_key_has_labels():
    """A referenced selector key must exist in strings.json with its options."""
    strings = json.loads((COMPONENT_ROOT / "strings.json").read_text(encoding="utf-8"))
    selectors = strings.get("selector", {})
    for key in _select_translation_keys():
        assert key in selectors, f"selector.{key} is referenced but not translated"
        assert selectors[key].get("options"), f"selector.{key} has no option labels"
