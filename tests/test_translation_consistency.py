"""Guard the two languages against the silent ways a translation goes missing.

The integration speaks English and Italian, and every failure in this area is
silent by design. Home Assistant falls back to English for a key a language does
not carry, shows the raw key when no language carries it, and shows a raw value
such as ``clay`` when a dropdown label is the thing missing. Nothing raises, no
log line appears, and the bug is visible only to whoever reads that form in that
language.

So the source of every user-facing word is checked against the code that shows
it: the keys of ``strings.json`` against each language, the dropdown labels
against the values the flow can offer, the translated errors against the ones the
services raise, the entity names against the platforms that declare them, and
``services.yaml`` against the strings that replaced its English.

The scans parse the modules rather than importing them: this file has to keep
running in the bare environment the domain tests use, without Home Assistant.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

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
    """A referenced selector key must exist, with its options, in every language."""
    for path in _translation_files():
        selectors = _load(path).get("selector", {})
        for key in _select_translation_keys():
            assert key in selectors, f"{path.name}: selector.{key} is referenced but not translated"
            assert selectors[key].get("options"), f"{path.name}: selector.{key} has no option labels"


def _translation_files() -> list[Path]:
    """Every file a user could be shown a word from: the source and each language."""
    return [COMPONENT_ROOT / "strings.json", *sorted((COMPONENT_ROOT / "translations").glob("*.json"))]


def _load(path: Path) -> dict:
    """One translation file, parsed."""
    return json.loads(path.read_text(encoding="utf-8"))


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


def _const_lists() -> dict[str, list[str]]:
    """String constants and lists of them declared in ``const.py``.

    Parsed rather than imported: ``const.py`` imports Home Assistant, and this
    guard has to run in the bare environment the domain tests use. Names inside a
    list are resolved against the literals declared earlier in the same file,
    which is all this file ever does.
    """
    tree = ast.parse((COMPONENT_ROOT / "const.py").read_text(encoding="utf-8"))
    scalars: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        name = targets[0].id if isinstance(targets[0], ast.Name) else None
        if name is None:
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            scalars[name] = node.value.value
        elif isinstance(node.value, ast.List):
            values = []
            for element in node.value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    values.append(element.value)
                elif isinstance(element, ast.Name) and element.id in scalars:
                    values.append(scalars[element.id])
            lists[name] = values
    return lists


def _dropdown_values() -> dict[str, list[str]]:
    """The values each translated dropdown of the config flow can actually show."""
    import sys

    sys.path.insert(0, str(COMPONENT_ROOT))
    from model import SoilTexture

    constants = _const_lists()
    return {
        "rain_sensor_type": constants["RAIN_SENSOR_TYPES"],
        "root_depth_unit": constants["ROOT_DEPTH_UNITS"],
        "soil_texture": [str(texture) for texture in SoilTexture],
    }


def test_every_dropdown_value_has_a_label_in_every_language():
    """A value offered by the flow and missing from the strings shows up raw.

    The dropdowns pass no inline label on purpose, so the label comes entirely
    from the translation. A sixth soil texture added to the enum and not to the
    files would put ``silt`` in the menu next to four readable sentences, in every
    language at once.
    """
    for path in _translation_files():
        selectors = _load(path).get("selector", {})
        for key, values in _dropdown_values().items():
            assert key in selectors, f"{path.name}: selector.{key} is offered but not translated"
            labels = selectors[key].get("options", {})
            missing = [value for value in values if not labels.get(value)]
            assert not missing, f"{path.name}: selector.{key} has no label for {missing}"


def _translation_keys_of(filename: str, call: str) -> list[str]:
    """Every literal ``translation_key`` passed to one kind of call in one module."""
    tree = ast.parse((COMPONENT_ROOT / filename).read_text(encoding="utf-8"))
    keys: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name != call:
            continue
        for keyword in node.keywords:
            if keyword.arg == "translation_key" and isinstance(keyword.value, ast.Constant):
                keys.append(keyword.value.value)
    return keys


def test_every_user_facing_error_is_translated():
    """An error raised at the user is a sentence they read, in their language.

    These reach the interface as a toast, which is the one place the integration
    speaks to somebody who is already stuck. A key raised and never translated
    shows them ``component.neverdry_calibrator.exceptions.unknown_probe.message``
    instead of what to type next.
    """
    raised = {
        key
        for filename in ("services.py", "coordinator.py", "__init__.py")
        for key in _translation_keys_of(filename, "HomeAssistantError")
    }
    assert raised, "no translated error was found: the scan is looking in the wrong place"
    for path in _translation_files():
        exceptions = _load(path).get("exceptions", {})
        for key in sorted(raised):
            assert key in exceptions, f"{path.name}: exceptions.{key} is raised in code but not translated"
            assert exceptions[key].get("message"), f"{path.name}: exceptions.{key} has no message"


def _entity_translation_keys() -> dict[str, set[str]]:
    """Every entity translation key, by the platform whose module declares it."""
    found: dict[str, set[str]] = {}
    for platform in ("sensor", "binary_sensor"):
        tree = ast.parse((COMPONENT_ROOT / f"{platform}.py").read_text(encoding="utf-8"))
        keys: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "translation_key" and isinstance(keyword.value, ast.Constant):
                        keys.add(keyword.value.value)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "_attr_translation_key":
                        keys.add(node.value.value)
        found[platform] = keys
    return found


def test_every_entity_has_its_name_in_every_language():
    """An entity whose key is missing is named after the key in the entity list."""
    for path in _translation_files():
        entities = _load(path).get("entity", {})
        for platform, keys in _entity_translation_keys().items():
            translated = entities.get(platform, {})
            for key in sorted(keys):
                assert key in translated, f"{path.name}: entity.{platform}.{key} is used but not translated"
                assert translated[key].get("name"), f"{path.name}: entity.{platform}.{key} has no name"


def test_services_yaml_leaves_the_words_to_the_translations():
    """Service text belongs to the translations, and to them only.

    ``services.yaml`` may carry a ``name`` and a ``description`` of its own, and
    Home Assistant ignores both once the strings file has the service. Left in
    place they are a second English original that drifts from the translated one
    without a single test going red, which is exactly what this asserts against.
    """
    yaml = pytest.importorskip("yaml")

    services = yaml.safe_load((COMPONENT_ROOT / "services.yaml").read_text(encoding="utf-8"))
    translated = _load(COMPONENT_ROOT / "strings.json")["services"]

    assert set(services) == set(translated), "services.yaml and the strings disagree on which services exist"
    for name, service in services.items():
        assert "name" not in service and "description" not in service, f"{name}: text belongs in the strings file"
        fields = service.get("fields", {})
        assert set(fields) == set(translated[name]["fields"]), f"{name}: the strings describe other fields"
        for field, spec in fields.items():
            assert "name" not in spec and "description" not in spec, f"{name}.{field}: text belongs in the strings"
