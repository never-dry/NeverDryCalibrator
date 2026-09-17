"""The placement diagnostic where a user can actually see it.

The domain tests prove the five signatures fire on the right data. These prove
the answer reaches a screen: an entity on the probe's device, a repair in the
Repairs panel carrying the advice, and both of them going away when the probe is
moved. A diagnostic nobody is shown is a diagnostic that does not exist.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from helpers import run_cycles  # noqa: E402
from homeassistant.helpers import issue_registry as ir  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.neverdry_calibrator.const import (  # noqa: E402
    CONF_PROBES,
    DOMAIN,
    ISSUE_PLACEMENT_PREFIX,
)
from custom_components.neverdry_calibrator.model import PlacementSuspicion  # noqa: E402

PLACEMENT_ENTITY = "sensor.ortensia_probe_placement"

#: A probe that publishes the same index whatever the soil does, which is what a
#: probe outside the wetted volume looks like from here.
FLAT_PROBE = lambda soil, deficit, temperature, noise: 50.0 + noise * 0.05  # noqa: E731


async def _setup(hass):
    """One probe on loam, paired with a plain deficit sensor."""
    hass.states.async_set("sensor.probe_ortensia", "55", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.zone_deficit", "6", {"unit_of_measurement": "mm"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NeverDry Calibrator",
        data={
            CONF_PROBES: [
                {
                    "probe_id": "ortensia",
                    "probe_name": "Ortensia",
                    "moisture_entity": "sensor.probe_ortensia",
                    "deficit_entity": "sensor.zone_deficit",
                    "soil_texture": "loam",
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


async def _run(hass, coordinator, **kwargs):
    """Feed the live session a set of cycles and publish what they imply.

    The clock starts two months back and in UTC: the session shares its timeline
    with Home Assistant, which is aware, and the samples have to be old enough
    that the refresh which follows does not fall inside the last cycle.
    """
    start = dt_util.utcnow() - timedelta(days=60)
    run_cycles(coordinator.session, cycles=6, start=start, **kwargs)
    await coordinator.async_refresh()
    await hass.async_block_till_done()


def _issue(hass, entry, suspicion: PlacementSuspicion):
    """The repair of one signature on this probe, if it is currently raised."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_PLACEMENT_PREFIX}_{suspicion}_{entry.entry_id}_ortensia")


async def test_the_probe_says_it_does_not_know_yet(hass):
    """A fresh probe reports insufficient evidence, not silence and not approval.

    Both of the other answers would be wrong on day one: "unknown" reads as a
    broken entity, and anything reassuring would be a claim made from no cycles
    at all.
    """
    await _setup(hass)

    state = hass.states.get(PLACEMENT_ENTITY)

    assert state is not None
    assert state.state == "not_enough_evidence"
    assert state.attributes["suspicions"] == []


async def test_a_probe_in_the_water_reads_plausible(hass):
    """The well-behaved synthetic probe must not be accused of anything."""
    _entry, coordinator = await _setup(hass)

    await _run(hass, coordinator)

    state = hass.states.get(PLACEMENT_ENTITY)
    assert state.state == "plausible"
    assert state.attributes["suspicions"] == []
    assert state.attributes["primary"] is None


async def test_a_probe_outside_the_water_is_named_on_the_entity(hass):
    """Suspicion and the numbers behind it, on the entity the user opens."""
    _entry, coordinator = await _setup(hass)

    await _run(hass, coordinator, index_fn=FLAT_PROBE)

    state = hass.states.get(PLACEMENT_ENTITY)
    assert state.state == "suspect"
    assert "no_response" in state.attributes["suspicions"]
    assert state.attributes["evidence"]["median_raw_span"] < 2.0


async def test_a_probe_outside_the_water_raises_a_repair(hass):
    """The advice has to arrive unprompted, or three weeks are spent first."""
    entry, coordinator = await _setup(hass)

    await _run(hass, coordinator, index_fn=FLAT_PROBE)

    issue = _issue(hass, entry, PlacementSuspicion.NO_RESPONSE)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.translation_placeholders["probe"] == "Ortensia"
    assert issue.translation_placeholders["moisture_entity"] == "sensor.probe_ortensia"


async def test_only_the_worst_suspicion_becomes_a_repair(hass):
    """Five warnings about one probe teach a user to close warnings unread."""
    entry, coordinator = await _setup(hass)

    await _run(hass, coordinator, index_fn=FLAT_PROBE)

    raised = [suspicion for suspicion in PlacementSuspicion if _issue(hass, entry, suspicion) is not None]
    assert raised == [PlacementSuspicion.NO_RESPONSE]


async def test_moving_the_probe_clears_the_repair(hass):
    """A repair that survives its own fix is noise."""
    entry, coordinator = await _setup(hass)
    await _run(hass, coordinator, index_fn=FLAT_PROBE)
    assert _issue(hass, entry, PlacementSuspicion.NO_RESPONSE) is not None

    await coordinator.async_reset_calibration()
    await _run(hass, coordinator)

    assert _issue(hass, entry, PlacementSuspicion.NO_RESPONSE) is None
    assert hass.states.get(PLACEMENT_ENTITY).state == "plausible"


async def test_a_well_placed_probe_never_raises_a_repair(hass):
    """The false positive is the expensive failure here, so it gets its own test."""
    entry, coordinator = await _setup(hass)

    await _run(hass, coordinator)

    assert all(_issue(hass, entry, suspicion) is None for suspicion in PlacementSuspicion)


async def test_the_installation_summary_carries_the_placement(hass):
    """With three probes, the hub is where the odd one out shows up."""
    _entry, coordinator = await _setup(hass)

    await _run(hass, coordinator, index_fn=FLAT_PROBE)

    summary = hass.states.get("sensor.neverdry_calibrator_calibrated_probes")
    assert summary.attributes["probes"]["Ortensia"]["placement"] == "suspect"
