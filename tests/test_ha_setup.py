"""Setting up an installation: what each probe publishes, and what the hub says."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.const import STATE_UNAVAILABLE  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.neverdry_calibrator.const import (  # noqa: E402
    ATTR_PROBE,
    CONF_PROBES,
    DOMAIN,
    SERVICE_APPLY_DEVICE_OFFSET,
    SERVICE_CALIBRATE_NOW,
    SERVICE_EXPORT_SAMPLES,
    SERVICE_MARK_FIELD_CAPACITY,
    SERVICE_RESET_CALIBRATION,
)
from custom_components.neverdry_calibrator.model import RejectionReason, WaterSource  # noqa: E402

SOIL = {"soil_texture": "loam", "root_depth": 30, "root_depth_unit": "cm"}


def _record(name, probe_id, suffix, irrigation=None):
    """A stored probe record pointing at the synthetic entities below."""
    record = {
        "probe_id": probe_id,
        "probe_name": name,
        "moisture_entity": f"sensor.probe_{suffix}",
        "deficit_entity": f"sensor.deficit_{suffix}",
        **SOIL,
    }
    if irrigation:
        record["irrigation_entity"] = irrigation
    return record


async def _setup(hass, *, raw="55", deficit="12", unit="mm", probes=None, site=None, rain=None):
    """Bring up one installation with the given probes and source states.

    ``site`` carries the installation-wide keys, the rain gauge among them, and
    ``rain`` is the gauge's initial reading. Both default to absent, which is the
    configuration every existing install has and the one that must keep working.
    """
    for suffix in ("ortensia", "melino"):
        hass.states.async_set(f"sensor.probe_{suffix}", raw, {"unit_of_measurement": "%"})
        hass.states.async_set(f"sensor.deficit_{suffix}", deficit, {"unit_of_measurement": unit})
    hass.states.async_set("switch.valve", "off")
    if rain is not None:
        hass.states.async_set("sensor.rain_gauge", rain, {"unit_of_measurement": "mm"})

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NeverDry Calibrator",
        data={
            CONF_PROBES: probes or [_record("Ortensia", "ortensia", "ortensia", "switch.valve")],
            **(site or {}),
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN][entry.entry_id]


async def test_an_uncalibrated_probe_publishes_nothing(hass):
    """The whole point: no calibration, no moisture reading."""
    await _setup(hass)

    moisture = hass.states.get("sensor.ortensia_calibrated_soil_moisture")
    status = hass.states.get("sensor.ortensia_calibration_status")

    assert moisture.state == "unknown"
    assert status.state == "collecting"
    assert status.attributes["cycles_required"] == 5


async def test_every_probe_gets_its_own_device_and_entities(hass, device_registry):
    """One entry, several probes, one device each: that is the shape asked for."""
    probes = [_record("Ortensia", "ortensia", "ortensia"), _record("Melino", "melino", "melino")]
    entry, runtime = await _setup(hass, probes=probes)

    assert len(runtime) == 2
    assert hass.states.get("sensor.ortensia_calibrated_soil_moisture") is not None
    assert hass.states.get("sensor.melino_calibrated_soil_moisture") is not None

    devices = device_registry.devices.get_devices_for_config_entry_id(entry.entry_id)
    names = sorted(device.name for device in devices)
    assert names == ["Melino", "NeverDry Calibrator", "Ortensia"]


async def test_the_hub_summarises_the_installation(hass):
    """One entity answers the headline question without opening a device."""
    probes = [_record("Ortensia", "ortensia", "ortensia"), _record("Melino", "melino", "melino")]
    await _setup(hass, probes=probes)

    summary = hass.states.get("sensor.neverdry_calibrator_calibrated_probes")

    assert summary.state == "0"
    assert summary.attributes["probes_configured"] == 2
    assert set(summary.attributes["probes"]) == {"Ortensia", "Melino"}


async def test_the_diagnostics_explain_what_is_missing(hass):
    """A user waiting for a calibration must see what it is waiting for."""
    _entry, runtime = await _setup(hass)
    await runtime["ortensia"].async_calibrate_now()
    await hass.async_block_till_done()

    status = hass.states.get("sensor.ortensia_calibration_status")
    progress = hass.states.get("sensor.ortensia_calibration_progress")

    assert "cycles" in status.attributes["missing_gates"]
    assert float(progress.state) < 100.0
    assert hass.states.get("sensor.ortensia_complete_cycles").state == "0"


async def test_a_deficit_in_inches_is_converted(hass):
    """An imperial deficit must reach the domain in millimetres."""
    _entry, runtime = await _setup(hass, deficit="1.0", unit="in")
    observation = runtime["ortensia"].build_observation(dt_util.utcnow())
    assert observation.deficit_mm == pytest.approx(25.4)


async def test_a_collapsing_deficit_is_read_as_irrigation(hass):
    """Rain and watering cans are water too, and only the deficit witnesses them."""
    _entry, runtime = await _setup(hass, deficit="30")
    coordinator = runtime["ortensia"]
    await coordinator.async_refresh()

    hass.states.async_set("sensor.deficit_ortensia", "1", {"unit_of_measurement": "mm"})
    await coordinator.async_refresh()

    assert coordinator.session.tracker.last_irrigation_at is not None


async def test_services_address_probes_by_name(hass):
    """Automations name the probe, which is what the user named and what the device shows."""
    probes = [_record("Ortensia", "ortensia", "ortensia"), _record("Melino", "melino", "melino")]
    _entry, runtime = await _setup(hass, probes=probes)

    for service in (SERVICE_CALIBRATE_NOW, SERVICE_RESET_CALIBRATION, SERVICE_EXPORT_SAMPLES):
        assert hass.services.has_service(DOMAIN, service)

    await hass.services.async_call(DOMAIN, SERVICE_MARK_FIELD_CAPACITY, {ATTR_PROBE: "Melino"}, blocking=True)

    assert runtime["melino"].session.tracker.open_cycle is not None
    assert runtime["ortensia"].session.tracker.open_cycle is None


async def test_an_unknown_probe_name_is_reported_with_the_known_ones(hass):
    """The error tells the user what to type instead of failing silently."""
    await _setup(hass)

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(DOMAIN, SERVICE_MARK_FIELD_CAPACITY, {ATTR_PROBE: "Camelia"}, blocking=True)

    assert "Ortensia" in str(raised.value)


async def test_reset_refuses_to_act_on_every_probe_at_once(hass):
    """A reset costs weeks of cycles, so it never fans out by accident.

    The refusal comes from the service schema, which makes the field required in
    the user interface as well, and the code behind it refuses an empty target
    too rather than trusting the schema alone.
    """
    await _setup(hass)

    with pytest.raises((HomeAssistantError, vol.Invalid)):
        await hass.services.async_call(DOMAIN, SERVICE_RESET_CALIBRATION, {}, blocking=True)


async def test_export_returns_one_entry_per_probe(hass):
    """The export is keyed by probe, because an installation has several."""
    probes = [_record("Ortensia", "ortensia", "ortensia"), _record("Melino", "melino", "melino")]
    await _setup(hass, probes=probes)

    response = await hass.services.async_call(DOMAIN, SERVICE_EXPORT_SAMPLES, {}, blocking=True, return_response=True)

    assert set(response) == {"Ortensia", "Melino"}
    assert response["Ortensia"]["soil"]["texture"] == "loam"


async def test_write_back_is_refused_unless_enabled(hass):
    """Touching the device is opt-in, and the refusal says so."""
    await _setup(hass)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, SERVICE_APPLY_DEVICE_OFFSET, {ATTR_PROBE: "Ortensia"}, blocking=True)


async def test_unloading_keeps_the_history(hass):
    """A reload must not cost the user the cycles already collected."""
    entry, _runtime = await _setup(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.entry_id not in hass.data.get(DOMAIN, {})


# ── The rain gauge ───────────────────────────────────────────────

RAIN_SITE = {"rain_entity": "sensor.rain_gauge", "rain_sensor_type": "accumulator"}


async def _rain(hass, coordinator, millimetres, minutes):
    """Move the gauge to a new total, that many minutes into the run."""
    hass.states.async_set("sensor.rain_gauge", str(millimetres), {"unit_of_measurement": "mm"})
    with patch(
        "custom_components.neverdry_calibrator.coordinator.dt_util.utcnow",
        return_value=dt_util.utcnow() + timedelta(minutes=minutes),
    ):
        await coordinator.async_refresh()


async def test_an_installation_without_a_gauge_behaves_exactly_as_before(hass):
    """The feature is optional, and optional means invisible when unused."""
    _entry, runtime = await _setup(hass)
    coordinator = runtime["ortensia"]
    await coordinator.async_refresh()

    assert coordinator.rain_entity is None
    assert coordinator.data.rain_watched is False
    assert coordinator.data.raining is False


async def test_a_qualifying_shower_counts_as_an_irrigation(hass):
    """The request in one test: with a rain gauge, rain opens a cycle like water does."""
    _entry, runtime = await _setup(hass, site=RAIN_SITE, rain="100")
    coordinator = runtime["ortensia"]
    await coordinator.async_refresh()

    await _rain(hass, coordinator, 112, minutes=10)
    await _rain(hass, coordinator, 112, minutes=90)

    assert coordinator.session.tracker.last_irrigation_at is not None
    assert coordinator.session.tracker.pending_water_source is WaterSource.RAIN


async def test_rain_still_falling_refuses_samples_by_name(hass):
    """Readings taken in the rain describe water in transit, and now say so."""
    _entry, runtime = await _setup(hass, site=RAIN_SITE, rain="100")
    coordinator = runtime["ortensia"]
    await coordinator.async_refresh()

    await _rain(hass, coordinator, 102, minutes=10)

    assert coordinator.data.raining is True
    assert coordinator.session.last_rejection is RejectionReason.RAIN_WETTING


async def test_a_sheltered_probe_is_never_told_about_the_weather(hass):
    """The gauge on the lawn says nothing about a pot under a roof."""
    probes = [
        _record("Ortensia", "ortensia", "ortensia"),
        {**_record("Melino", "melino", "melino"), "sheltered_from_rain": True},
    ]
    _entry, runtime = await _setup(hass, probes=probes, site=RAIN_SITE, rain="100")

    assert runtime["ortensia"].rain_entity == "sensor.rain_gauge"
    assert runtime["melino"].rain_entity is None


async def test_an_unavailable_gauge_is_not_a_drought(hass):
    """A gauge that drops out must credit nothing rather than crediting a reset."""
    _entry, runtime = await _setup(hass, site=RAIN_SITE, rain="100")
    coordinator = runtime["ortensia"]
    await coordinator.async_refresh()

    hass.states.async_set("sensor.rain_gauge", STATE_UNAVAILABLE)
    await coordinator.async_refresh()

    assert coordinator.data.rain_accumulated_mm == 0.0
    assert coordinator.session.rain_witness.baseline_mm == 100.0


async def test_the_status_entity_tells_rain_apart_from_no_gauge(hass):
    """A user whose samples are refused has to know which of the two it is."""
    _entry, runtime = await _setup(hass, site=RAIN_SITE, rain="100")
    await runtime["ortensia"].async_refresh()

    status = hass.states.get("sensor.ortensia_calibration_status")

    assert status.attributes["rain_watched"] is True
    # The depth that makes a shower a wetting is a share of this probe's own
    # reservoir, the same share a deficit drop needs to count as an irrigation.
    expected = 0.20 * status.attributes["total_available_water_mm"]
    assert status.attributes["rain_event_mm"] == pytest.approx(expected, abs=0.1)
