"""The thresholds the calibration imposes, made visible before weeks are wasted.

The integration always knew how dry the soil has to get between two irrigations
for a cycle to count. Keeping that number to itself meant a site irrigating too
often would sit at "collecting" forever with no way to find out why. These tests
cover the three parts of the answer: the numbers in millimetres, the entity that
publishes them, and the repair that compares them with the irrigation regime.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.helpers import issue_registry as ir  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.neverdry_calibrator.const import (  # noqa: E402
    CONF_PROBES,
    DOMAIN,
    ISSUE_THRESHOLD_TOO_LOW,
)
from custom_components.neverdry_calibrator.discovery import discover_irrigation_threshold  # noqa: E402

# Clay at 30 cm: the reservoir of the installation these tests were written from.
CLAY_TAW_MM = (0.36 - 0.22) * 0.30 * 1000


async def _zone_device(hass, device_registry, entity_registry, threshold: str | None = "3.0"):
    """Register a water balance zone: a deficit, and the depletion it waters at."""
    source = MockConfigEntry(domain="never_dry", data={})
    source.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("never_dry", "zone-1")},
        name="Giardino Ortensia",
    )
    deficit = entity_registry.async_get_or_create(
        "sensor", "never_dry", "zone1_deficit", device_id=device.id, original_name="Deficit"
    ).entity_id
    hass.states.async_set(deficit, "6", {"unit_of_measurement": "mm"})
    if threshold is not None:
        threshold_entity = entity_registry.async_get_or_create(
            "sensor", "never_dry", "zone1_threshold", device_id=device.id, original_name="Soglia"
        ).entity_id
        hass.states.async_set(threshold_entity, threshold, {"unit_of_measurement": "mm"})
    return deficit


async def _setup(hass, deficit_entity):
    """One probe on clay, paired with the given deficit."""
    hass.states.async_set("sensor.probe_ortensia", "99", {"unit_of_measurement": "%"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NeverDry Calibrator",
        data={
            CONF_PROBES: [
                {
                    "probe_id": "ortensia",
                    "probe_name": "Ortensia",
                    "moisture_entity": "sensor.probe_ortensia",
                    "deficit_entity": deficit_entity,
                    "soil_texture": "clay",
                    "root_depth": 30,
                    "root_depth_unit": "cm",
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN][entry.entry_id]["ortensia"]


async def test_the_threshold_is_found_next_to_the_deficit(hass, device_registry, entity_registry):
    """The user names the deficit; the depletion it waters at travels with it."""
    deficit = await _zone_device(hass, device_registry, entity_registry)

    found = discover_irrigation_threshold(hass, deficit)

    assert found is not None
    assert "threshold" in found


async def test_a_deficit_without_a_device_degrades_quietly(hass):
    """A template deficit publishes no threshold, and that is not a failure."""
    hass.states.async_set("sensor.plain_deficit", "5", {"unit_of_measurement": "mm"})
    assert discover_irrigation_threshold(hass, "sensor.plain_deficit") is None


async def test_the_fractions_are_published_as_millimetres(hass, device_registry, entity_registry):
    """Nobody configures an irrigation system in fractions of available water."""
    deficit = await _zone_device(hass, device_registry, entity_registry)
    _entry, coordinator = await _setup(hass, deficit)

    thresholds = coordinator.derived_thresholds()

    assert thresholds.total_available_water_mm == pytest.approx(CLAY_TAW_MM)
    assert thresholds.irrigation_drop_mm == pytest.approx(0.20 * CLAY_TAW_MM)
    assert thresholds.wet_anchor_deficit_mm == pytest.approx(0.10 * CLAY_TAW_MM)
    assert thresholds.min_cycle_span_mm == pytest.approx(0.30 * CLAY_TAW_MM)


async def test_the_required_depletion_has_its_own_entity(hass, device_registry, entity_registry):
    """It sits on a dashboard next to the irrigation threshold, which is the point."""
    deficit = await _zone_device(hass, device_registry, entity_registry)
    await _setup(hass, deficit)

    state = hass.states.get("sensor.ortensia_required_depletion")

    assert float(state.state) == pytest.approx(0.30 * CLAY_TAW_MM, abs=0.05)
    assert state.attributes["irrigation_threshold_mm"] == pytest.approx(3.0)
    assert state.attributes["regime_can_calibrate"] is False
    assert "Raise the irrigation threshold" in state.attributes["advice"]


async def test_an_irrigation_regime_that_cannot_calibrate_raises_a_repair(hass, device_registry, entity_registry):
    """Three millimetres against a twelve millimetre requirement is never going to work."""
    deficit = await _zone_device(hass, device_registry, entity_registry, threshold="3.0")
    entry, _coordinator = await _setup(hass, deficit)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_THRESHOLD_TOO_LOW}_{entry.entry_id}_ortensia")

    assert issue is not None
    assert issue.translation_placeholders["required"] == "12.6"
    assert issue.translation_placeholders["configured"] == "3.0"
    assert issue.severity is ir.IssueSeverity.WARNING


async def test_a_workable_regime_raises_nothing(hass, device_registry, entity_registry):
    """A site that lets the soil dry is left alone."""
    deficit = await _zone_device(hass, device_registry, entity_registry, threshold="15.0")
    entry, _coordinator = await _setup(hass, deficit)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_THRESHOLD_TOO_LOW}_{entry.entry_id}_ortensia")

    assert issue is None


async def test_raising_the_threshold_clears_the_repair(hass, device_registry, entity_registry):
    """The advice disappears when it has been followed, without a restart."""
    deficit = await _zone_device(hass, device_registry, entity_registry, threshold="3.0")
    entry, coordinator = await _setup(hass, deficit)
    issue_id = f"{ISSUE_THRESHOLD_TOO_LOW}_{entry.entry_id}_ortensia"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    hass.states.async_set("sensor.never_dry_zone1_threshold", "15.0", {"unit_of_measurement": "mm"})
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_an_unknown_threshold_never_looks_like_a_problem(hass, device_registry, entity_registry):
    """Plenty of sites irrigate on a timer; silence is not a wrong configuration."""
    deficit = await _zone_device(hass, device_registry, entity_registry, threshold=None)
    entry, _coordinator = await _setup(hass, deficit)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_THRESHOLD_TOO_LOW}_{entry.entry_id}_ortensia")
    state = hass.states.get("sensor.ortensia_required_depletion")

    assert issue is None
    assert state.attributes["regime_can_calibrate"] is None
