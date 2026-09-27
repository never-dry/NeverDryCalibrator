"""The two silent deaths, where a user can actually see them.

The domain tests prove the state machines are right. These prove the wiring around
them is: that the sentinel really reads the whole device and not one channel, that
a stalled electrode raises its own repair rather than the placement advice, and
that both go away when the probe is fixed. A judgement nobody is shown is a
judgement that does not exist.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from helpers import dead_probe  # noqa: E402
from homeassistant.helpers import issue_registry as ir  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.neverdry_calibrator.const import (  # noqa: E402
    CONF_MIN_BATTERY_PERCENT,
    CONF_PROBES,
    DOMAIN,
    ISSUE_PLACEMENT_PREFIX,
    ISSUE_SENSING_STALLED,
)
from custom_components.neverdry_calibrator.model import PlacementSuspicion, ProbeLiveness  # noqa: E402

ONLINE_ENTITY = "binary_sensor.ortensia_probe_online"


async def _probe_device(hass, device_registry, entity_registry):
    """A Zigbee-shaped probe: moisture, temperature and battery on one device."""
    source = MockConfigEntry(domain="zha", data={})
    source.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("zha", "probe-ortensia")},
        name="Soil probe",
    )
    created = {}
    for domain, unique, name, device_class in (
        ("sensor", "moisture", "Soil moisture", "moisture"),
        ("sensor", "temperature", "Temperature", "temperature"),
        ("sensor", "battery", "Battery", "battery"),
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


async def _setup(hass, moisture_entity: str, options: dict | None = None):
    """One probe on loam, reading the given moisture entity."""
    hass.states.async_set("sensor.zone_deficit", "6", {"unit_of_measurement": "mm"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NeverDry Calibrator",
        data={
            CONF_PROBES: [
                {
                    "probe_id": "ortensia",
                    "probe_name": "Ortensia",
                    "moisture_entity": moisture_entity,
                    "deficit_entity": "sensor.zone_deficit",
                    "soil_texture": "loam",
                    "root_depth": 30,
                    "root_depth_unit": "cm",
                }
            ]
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN][entry.entry_id]["ortensia"]


# ── The sentinel reads the device ───────────────────────────────


async def test_every_entity_of_the_probe_device_is_enrolled_as_a_witness(hass, device_registry, entity_registry):
    """The registry walk has to reach the coordinator, or the sentinel is one channel."""
    created = await _probe_device(hass, device_registry, entity_registry)
    hass.states.async_set(created["moisture"], "55", {"unit_of_measurement": "%"})
    _entry, coordinator = await _setup(hass, created["moisture"])

    assert set(coordinator.companions.device_entities) == set(created.values())


async def test_a_still_reading_from_a_talking_device_is_not_a_dead_probe(
    hass, device_registry, entity_registry, freezer
):
    """The regression this whole channel exists for.

    Home Assistant writes a sensor's state when its value changes, so a probe on
    ground that has stopped moving publishes nothing on its moisture entity while
    its temperature carries on. Judged on the moisture entity alone the probe is
    declared dead for reporting the same number twice; judged on the device it is a
    probe stating that the soil has not moved.
    """
    created = await _probe_device(hass, device_registry, entity_registry)
    hass.states.async_set(created["moisture"], "55", {"unit_of_measurement": "%"})

    # Ten hours pass with nothing from the moisture channel, then the device speaks
    # on its temperature. Ten hours is past every bar and past the ceiling, so a
    # check reading the moisture entity alone has no way to call this alive.
    freezer.tick(timedelta(hours=10))
    hass.states.async_set(created["temperature"], "18.5", {"unit_of_measurement": "°C"})
    hass.states.async_set(created["battery"], "82", {"unit_of_measurement": "%"})

    _entry, coordinator = await _setup(hass, created["moisture"])
    await coordinator.async_refresh()

    assert coordinator.data.device_age_s < 60
    assert coordinator.data.liveness is ProbeLiveness.ALIVE
    assert hass.states.get(ONLINE_ENTITY).state == "on"


async def test_a_device_gone_quiet_on_every_channel_is_reported_offline(
    hass, device_registry, entity_registry, freezer
):
    """The other direction, and the one the check is nominally for."""
    created = await _probe_device(hass, device_registry, entity_registry)
    for entity_id, value in ((created["moisture"], "55"), (created["temperature"], "18.5")):
        hass.states.async_set(entity_id, value)

    freezer.tick(timedelta(hours=10))
    _entry, coordinator = await _setup(hass, created["moisture"])
    await coordinator.async_refresh()

    assert coordinator.data.liveness is ProbeLiveness.STALE
    assert hass.states.get(ONLINE_ENTITY).state == "off"


async def test_the_silence_and_the_bar_are_published_together(hass, device_registry, entity_registry):
    """An age without its bar invites the reader to compare it with a number they invented."""
    created = await _probe_device(hass, device_registry, entity_registry)
    hass.states.async_set(created["moisture"], "55", {"unit_of_measurement": "%"})
    _entry, coordinator = await _setup(hass, created["moisture"])
    await coordinator.async_refresh()

    attributes = hass.states.get(ONLINE_ENTITY).attributes
    assert attributes["device_silence_s"] is not None
    assert attributes["silence_allowed_s"] is not None
    assert set(attributes["device_entities"]) == set(created.values())


async def test_a_probe_on_no_device_still_gets_the_channel_it_had(hass):
    """A template sensor belongs to no device, and must not lose its sentinel for it."""
    hass.states.async_set("sensor.template_probe", "55", {"unit_of_measurement": "%"})
    _entry, coordinator = await _setup(hass, "sensor.template_probe")
    await coordinator.async_refresh()

    assert coordinator.companions.device_entities == ("sensor.template_probe",)
    assert coordinator.data.liveness is ProbeLiveness.ALIVE


# ── The stalled electrode ───────────────────────────────────────


async def _run_dead(hass, coordinator):
    """Feed the live session cycles from a probe whose electrode has stopped.

    The two published states have to agree with the story the cycles tell, because
    the refresh that follows feeds one more real observation. An index that moved
    there would re-anchor the witness, and so would a deficit that fell: a fall is
    water arriving, and whatever the index did across a delivery says nothing. Both
    would clear the very finding under test.
    """
    from helpers import run_cycles

    start = dt_util.utcnow() - timedelta(days=60)
    run_cycles(coordinator.session, cycles=6, start=start, index_fn=dead_probe)
    hass.states.async_set("sensor.zone_deficit", "37", {"unit_of_measurement": "mm"})
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_a_stalled_electrode_raises_its_own_repair(hass):
    """Not the placement advice: that one ends in "move the probe", which is wrong here."""
    hass.states.async_set("sensor.probe_ortensia", "50", {"unit_of_measurement": "%"})
    entry, coordinator = await _setup(hass, "sensor.probe_ortensia")
    await _run_dead(hass, coordinator)

    assert coordinator.session.sensing_stalled
    registry = ir.async_get(hass)
    stalled = registry.async_get_issue(DOMAIN, f"{ISSUE_SENSING_STALLED}_{entry.entry_id}_ortensia")
    assert stalled is not None
    placement = registry.async_get_issue(
        DOMAIN,
        f"{ISSUE_PLACEMENT_PREFIX}_{PlacementSuspicion.NO_RESPONSE}_{entry.entry_id}_ortensia",
    )
    assert placement is None


async def test_the_repair_carries_the_numbers_behind_the_verdict(hass):
    """An accusation needs its evidence: not moved, yes, but across how much drying."""
    hass.states.async_set("sensor.probe_ortensia", "50", {"unit_of_measurement": "%"})
    entry, coordinator = await _setup(hass, "sensor.probe_ortensia")
    await _run_dead(hass, coordinator)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_SENSING_STALLED}_{entry.entry_id}_ortensia")
    placeholders = issue.translation_placeholders
    assert float(placeholders["travel"]) >= float(placeholders["required"])
    assert placeholders["probe"] == "Ortensia"


async def test_repairing_the_probe_clears_the_repair(hass):
    """A finding that never goes away is a finding the user learns to close unread."""
    hass.states.async_set("sensor.probe_ortensia", "50", {"unit_of_measurement": "%"})
    entry, coordinator = await _setup(hass, "sensor.probe_ortensia")
    await _run_dead(hass, coordinator)
    issue_id = f"{ISSUE_SENSING_STALLED}_{entry.entry_id}_ortensia"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    # The index answers again while the soil is still where it was: a repaired
    # electrode, not a delivery.
    hass.states.async_set("sensor.probe_ortensia", "62", {"unit_of_measurement": "%"})
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.session.sensing_stalled
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_healthy_probe_is_never_accused_of_stalling(hass):
    """The case that decides whether this channel can be left switched on."""
    from helpers import run_cycles

    hass.states.async_set("sensor.probe_ortensia", "55", {"unit_of_measurement": "%"})
    entry, coordinator = await _setup(hass, "sensor.probe_ortensia")
    run_cycles(coordinator.session, cycles=6, start=dt_util.utcnow() - timedelta(days=60))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.session.sensing_stalled
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_SENSING_STALLED}_{entry.entry_id}_ortensia") is None


# ── The battery floor ───────────────────────────────────────────


async def test_the_battery_floor_reaches_the_policy(hass, device_registry, entity_registry):
    """A tuned option that never arrives is worse than no option at all."""
    created = await _probe_device(hass, device_registry, entity_registry)
    hass.states.async_set(created["moisture"], "55", {"unit_of_measurement": "%"})
    _entry, coordinator = await _setup(hass, created["moisture"], options={CONF_MIN_BATTERY_PERCENT: 15})

    assert coordinator.session.admission_policy.min_battery_percent == 15


async def test_a_dying_battery_stops_the_samples_and_says_so(hass, device_registry, entity_registry):
    """Refused with its own reason, which is what makes a stalled calibration explainable."""
    from custom_components.neverdry_calibrator.model import RejectionReason

    created = await _probe_device(hass, device_registry, entity_registry)
    hass.states.async_set(created["moisture"], "55", {"unit_of_measurement": "%"})
    hass.states.async_set(created["battery"], "3", {"unit_of_measurement": "%"})
    _entry, coordinator = await _setup(hass, created["moisture"])
    await coordinator.async_refresh()

    assert coordinator.data.battery_percent == 3
    assert coordinator.data.last_rejection is RejectionReason.BATTERY_CRITICAL
