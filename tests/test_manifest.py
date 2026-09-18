"""Repository sanity: manifest, services and translations describe the same integration."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "neverdry_calibrator"


@pytest.fixture(name="manifest")
def manifest_fixture() -> dict:
    """The integration manifest."""
    return json.loads((COMPONENT_ROOT / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_declares_the_domain_the_folder_uses(manifest):
    """Home Assistant resolves the integration by folder name; the two must agree."""
    assert manifest["domain"] == COMPONENT_ROOT.name


def test_manifest_has_the_keys_hassfest_requires(manifest):
    """Fail here rather than in the hassfest job, where the message is longer."""
    for key in ("domain", "name", "version", "documentation", "codeowners", "config_flow", "iot_class"):
        assert key in manifest


def test_the_integration_declares_no_python_requirements(manifest):
    """The estimator is standard library only, and that is a design constraint."""
    assert manifest["requirements"] == []


def test_hacs_manifest_matches_the_integration_name(manifest):
    """A mismatch here shows up as a differently named integration in HACS."""
    hacs = json.loads((REPO_ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"] == manifest["name"]


def test_services_yaml_lists_exactly_the_registered_services():
    """A service in one file and not the other is either invisible or undocumented."""
    yaml = pytest.importorskip("yaml")
    services_yaml = (COMPONENT_ROOT / "services.yaml").read_text(encoding="utf-8")
    declared = set(yaml.safe_load(services_yaml))

    registration_source = (COMPONENT_ROOT / "services.py").read_text(encoding="utf-8")
    const_source = (COMPONENT_ROOT / "const.py").read_text(encoding="utf-8")
    constants = {
        node.targets[0].id: node.value.value
        for node in ast.parse(const_source).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and node.targets[0].id.startswith("SERVICE_")
    }
    registered = {value for name, value in constants.items() if name in registration_source}

    assert declared == registered


def test_english_translation_matches_strings():
    """The shipped English file is the strings file; a drift between them is a bug."""
    strings = json.loads((COMPONENT_ROOT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads((COMPONENT_ROOT / "translations" / "en.json").read_text(encoding="utf-8"))
    assert strings == english


def test_every_translation_has_the_same_shape():
    """A missing branch in a translation shows the user a raw key."""

    def shape(node, prefix=""):
        """Set of leaf paths in a translation tree."""
        if not isinstance(node, dict):
            return {prefix}
        paths = set()
        for key, value in node.items():
            paths |= shape(value, f"{prefix}.{key}")
        return paths

    base = json.loads((COMPONENT_ROOT / "strings.json").read_text(encoding="utf-8"))
    for path in (COMPONENT_ROOT / "translations").glob("*.json"):
        translated = json.loads(path.read_text(encoding="utf-8"))
        assert shape(translated) == shape(base), f"{path.name} does not match strings.json"


def test_manifest_keys_are_sorted_the_way_hassfest_wants(manifest):
    """Domain, name, then alphabetical. Hassfest fails the build otherwise."""
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_no_translation_string_contains_a_url():
    """Hassfest refuses URLs inside strings; they belong in description placeholders.

    The guard exists because the natural thing to write, a link in the sentence
    that needs it, passes every local check and fails only in the published
    repository.
    """
    for path in [COMPONENT_ROOT / "strings.json", *(COMPONENT_ROOT / "translations").glob("*.json")]:
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text, f"{path.name} contains a URL"


def _png_size(path: Path) -> tuple[int, int]:
    """Width and height from the PNG header, without pulling in an image library."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def test_brand_assets_exist_at_the_sizes_hacs_expects():
    """Without them HACS falls back to the central brands repository and fails."""
    brand = COMPONENT_ROOT / "brand"
    for name, expected in (("icon.png", 256), ("icon@2x.png", 512), ("logo.png", 256), ("logo@2x.png", 512)):
        asset = brand / name
        assert asset.exists(), f"missing brand asset {name}"
        assert _png_size(asset) == (expected, expected)
