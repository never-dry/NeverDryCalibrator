"""Discovery of the entities that travel with a probe, through the real registries."""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.neverdry_calibrator.discovery import (  # noqa: E402
    discover_companions,
    snapshot_calibration_values,
)


async def _probe_device(hass, device_registry, entity_registry):
    """Register a Tuya-shaped soil probe: moisture, temperature, battery, three knobs."""
    entry = MockConfigEntry(domain="zha", data={})
    entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("zha", "probe-1")},
        name="Soil probe",
    )
    created = {}
    for domain, unique, name, device_class in (
        ("sensor", "moisture", "Soil moisture", "moisture"),
        ("sensor", "temperature", "Temperature", "temperature"),
        ("sensor", "battery", "Battery", "battery"),
        ("number", "temp_calib", "Temperature calibration", None),
        ("number", "hum_calib", "Humidity calibration", None),
        ("number", "soil_calib", "Soil calibration", None),
    ):
        created[unique] = entity_registry.async_get_or_create(
            domain,
            "zha",
            unique,
            device_id=device.id,
            original_name=name,
            original_device_class=device_class,
        ).entity_id
    return created


async def test_companions_are_found_on_the_probe_device(hass, device_registry, entity_registry):
    """The user names the probe; everything else is read from the device registry."""
    created = await _probe_device(hass, device_registry, entity_registry)

    companions = discover_companions(hass, created["moisture"])

    assert companions.probe_temperature == created["temperature"]
    assert companions.battery == created["battery"]
    assert companions.temperature_calibration == created["temp_calib"]
    assert companions.humidity_calibration == created["hum_calib"]
    assert companions.soil_calibration == created["soil_calib"]


async def test_the_soil_knob_is_preferred_for_write_back(hass, device_registry, entity_registry):
    """Devices exposing both mean different things by them; the soil one is the electrode."""
    created = await _probe_device(hass, device_registry, entity_registry)
    companions = discover_companions(hass, created["moisture"])
    assert companions.moisture_calibration == created["soil_calib"]


async def test_a_probe_with_no_device_degrades_instead_of_failing(hass):
    """A template sensor belongs to no device, and that must not block the pairing."""
    hass.states.async_set("sensor.template_probe", "42")
    companions = discover_companions(hass, "sensor.template_probe")
    assert companions.device_id is None
    assert companions.calibration_entities == ()


async def test_the_knob_snapshot_skips_unavailable_entities(hass, device_registry, entity_registry):
    """An asleep probe must not look like a probe whose calibration was changed."""
    created = await _probe_device(hass, device_registry, entity_registry)
    hass.states.async_set(created["soil_calib"], "3.0")
    hass.states.async_set(created["hum_calib"], "unavailable")

    snapshot = snapshot_calibration_values(hass, (created["soil_calib"], created["hum_calib"]))

    assert snapshot == {created["soil_calib"]: "3.0"}
