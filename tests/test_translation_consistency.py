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


def _translation_files() -> list[Path]:
    """Every file a user could be shown a placement message from."""
    return [COMPONENT_ROOT / "strings.json", *sorted((COMPONENT_ROOT / "translations").glob("*.json"))]


def test_every_placement_suspicion_has_its_advice():
    """A signature with no repair text is a diagnosis the user never receives.

    The whole point of the placement diagnostic is that the advice arrives before
    weeks of cycles are spent on a probe in the wrong place. A sixth signature
    added to the domain and not to the strings would fail silently, showing a
    repair with a raw translation key in place of what to do about it.
    """
    import sys

    sys.path.insert(0, str(COMPONENT_ROOT))
    from model import PlacementConfidence, PlacementSuspicion

    for path in _translation_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        issues = data.get("issues", {})
        for suspicion in PlacementSuspicion:
            key = f"placement_{suspicion}"
            assert key in issues, f"{path.name}: issues.{key} is raised in code but not translated"
            assert issues[key].get("title"), f"{path.name}: issues.{key} has no title"
            assert issues[key].get("description"), f"{path.name}: issues.{key} has no description"

        states = data["entity"]["sensor"]["probe_placement"]["state"]
        for confidence in PlacementConfidence:
            assert str(confidence) in states, f"{path.name}: probe_placement.{confidence} has no label"
