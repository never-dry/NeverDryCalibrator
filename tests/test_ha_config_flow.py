"""The config flow: one installation, probes added inside it, and what is refused."""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.const import CONF_NAME  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.neverdry_calibrator.const import (  # noqa: E402
    CONF_DEFICIT_ENTITY,
    CONF_MOISTURE_ENTITY,
    CONF_PROBES,
    CONF_RAIN_ENTITY,
    CONF_ROOT_DEPTH,
    CONF_ROOT_DEPTH_UNIT,
    CONF_SOIL_TEXTURE,
    DOMAIN,
)

FIRST_PROBE = {
    CONF_NAME: "Ortensia",
    CONF_MOISTURE_ENTITY: "sensor.probe_ortensia",
    CONF_DEFICIT_ENTITY: "sensor.deficit_ortensia",
}
SECOND_PROBE = {
    CONF_NAME: "Melino",
    CONF_MOISTURE_ENTITY: "sensor.probe_melino",
    CONF_DEFICIT_ENTITY: "sensor.deficit_melino",
}
SOIL = {CONF_SOIL_TEXTURE: "loam", CONF_ROOT_DEPTH: 30, CONF_ROOT_DEPTH_UNIT: "cm"}


@pytest.fixture(name="sources")
def sources_fixture(hass):
    """Two probes on their 0-100 scale and two deficits in millimetres."""
    for suffix in ("ortensia", "melino"):
        hass.states.async_set(f"sensor.probe_{suffix}", "54", {"unit_of_measurement": "%"})
        hass.states.async_set(f"sensor.deficit_{suffix}", "11.5", {"unit_of_measurement": "mm"})


async def _add_probe(hass, flow_id, probe, add_another=False, rain=None):
    """Walk one probe through the probe, soil, confirm and add-another steps.

    The last probe falls through to the rain step, which is where the flow ends:
    ``rain`` is what gets answered there, and ``None`` means the honest answer of
    a site with no gauge, which must still produce an entry.
    """
    result = await hass.config_entries.flow.async_configure(flow_id, probe)
    assert result["step_id"] == "soil", result
    result = await hass.config_entries.flow.async_configure(flow_id, SOIL)
    assert result["step_id"] == "confirm", result
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["step_id"] == "add_another", result
    result = await hass.config_entries.flow.async_configure(flow_id, {"add_another": add_another})
    if add_another:
        return result
    assert result["step_id"] == "rain", result
    return await hass.config_entries.flow.async_configure(flow_id, rain or {})


async def test_the_flow_creates_one_entry_with_one_probe(hass, sources):
    """The installation is the entry; the probe lives inside it."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "user"

    result = await _add_probe(hass, result["flow_id"], FIRST_PROBE)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NeverDry Calibrator"
    probes = result["data"][CONF_PROBES]
    assert len(probes) == 1
    assert probes[0]["probe_name"] == "Ortensia"
    assert probes[0]["probe_id"] == "ortensia"


async def test_several_probes_can_be_added_in_one_pass(hass, sources):
    """Everything in one place starts here: the first flow can configure the lot."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await _add_probe(hass, result["flow_id"], FIRST_PROBE, add_another=True)
    assert result["step_id"] == "user"
    result = await _add_probe(hass, result["flow_id"], SECOND_PROBE)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [probe["probe_name"] for probe in result["data"][CONF_PROBES]] == ["Ortensia", "Melino"]


async def test_only_one_installation_is_allowed(hass, sources):
    """A second entry would split the installation across two places."""
    MockConfigEntry(domain=DOMAIN, title="NeverDry Calibrator", data={CONF_PROBES: []}).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_a_probe_outside_its_scale_is_refused(hass, sources):
    """The probe contract is 0 to 100; a sensor publishing 4200 is something else."""
    hass.states.async_set("sensor.probe_ortensia", "4200")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], FIRST_PROBE)
    assert result["errors"] == {CONF_MOISTURE_ENTITY: "moisture_out_of_range"}


async def test_a_deficit_in_an_unconvertible_unit_is_refused(hass, sources):
    """A deficit that is not a water depth would calibrate against nothing."""
    hass.states.async_set("sensor.deficit_ortensia", "11.5", {"unit_of_measurement": "kPa"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], FIRST_PROBE)
    assert result["errors"] == {CONF_DEFICIT_ENTITY: "deficit_unit_unknown"}


async def test_a_deficit_in_inches_is_accepted(hass, sources):
    """Imperial installs are not an edge case."""
    hass.states.async_set("sensor.deficit_ortensia", "0.5", {"unit_of_measurement": "in"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], FIRST_PROBE)
    assert result["step_id"] == "soil"


async def test_the_same_sensor_cannot_be_calibrated_twice(hass, sources):
    """Two probes on one sensor would fight over the same history."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await _add_probe(hass, result["flow_id"], FIRST_PROBE, add_another=True)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SECOND_PROBE, CONF_MOISTURE_ENTITY: "sensor.probe_ortensia"}
    )
    assert result["errors"] == {CONF_MOISTURE_ENTITY: "probe_already_configured"}


async def test_an_impossible_soil_is_refused(hass, sources):
    """A wilting point above field capacity describes no ground."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], FIRST_PROBE)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**SOIL, CONF_SOIL_TEXTURE: "custom", "field_capacity": 0.20, "wilting_point": 0.30},
    )
    assert result["errors"] == {"base": "invalid_soil"}


async def _installed(hass, probes):
    """An installation already set up with the given probe records."""
    entry = MockConfigEntry(domain=DOMAIN, title="NeverDry Calibrator", data={CONF_PROBES: probes})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _record(probe, probe_id):
    """A stored probe record, as the config flow would have written it."""
    sources = {key: value for key, value in probe.items() if key != CONF_NAME}
    return {"probe_id": probe_id, "probe_name": probe[CONF_NAME], **sources, **SOIL}


async def test_a_probe_can_be_added_from_the_options(hass, sources):
    """The one place: probes are managed inside the integration, not beside it."""
    entry = await _installed(hass, [_record(FIRST_PROBE, "ortensia")])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "add_probe"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], SECOND_PROBE)
    assert result["step_id"] == "add_probe_soil"
    result = await hass.config_entries.options.async_configure(result["flow_id"], SOIL)
    await hass.async_block_till_done()

    assert [record["probe_name"] for record in entry.data[CONF_PROBES]] == ["Ortensia", "Melino"]
    assert len(hass.data[DOMAIN][entry.entry_id]) == 2


async def test_renaming_a_probe_keeps_its_identity_and_its_history(hass, sources):
    """The identifier survives a rename, and so do weeks of collected cycles."""
    entry = await _installed(hass, [_record(FIRST_PROBE, "ortensia")])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "edit_probe"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"selected_probe": "ortensia"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**FIRST_PROBE, CONF_NAME: "Ortensia grande"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], SOIL)
    await hass.async_block_till_done()

    record = entry.data[CONF_PROBES][0]
    assert record["probe_name"] == "Ortensia grande"
    assert record["probe_id"] == "ortensia"


async def test_removing_a_probe_asks_first(hass, sources):
    """Removal throws away weeks of cycles, so it is confirmed rather than assumed."""
    entry = await _installed(hass, [_record(FIRST_PROBE, "ortensia"), _record(SECOND_PROBE, "melino")])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "remove_probe"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"selected_probe": "melino"})
    assert result["step_id"] == "confirm_removal"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"confirm_removal": True})
    await hass.async_block_till_done()

    assert [record["probe_name"] for record in entry.data[CONF_PROBES]] == ["Ortensia"]


async def test_declining_the_removal_changes_nothing(hass, sources):
    """The confirmation is a real question, not a formality."""
    entry = await _installed(hass, [_record(FIRST_PROBE, "ortensia"), _record(SECOND_PROBE, "melino")])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "remove_probe"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"selected_probe": "melino"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"confirm_removal": False})
    await hass.async_block_till_done()

    assert len(entry.data[CONF_PROBES]) == 2


async def test_the_gates_are_tuned_for_the_whole_installation(hass, sources):
    """One set of thresholds, in one place, applied to every probe."""
    entry = await _installed(hass, [_record(FIRST_PROBE, "ortensia"), _record(SECOND_PROBE, "melino")])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "gates"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "min_cycles": 3,
            "min_samples": 30,
            "min_raw_span": 6,
            "min_r_squared": 0.5,
            "allow_device_writeback": False,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    for coordinator in hass.data[DOMAIN][entry.entry_id].values():
        assert coordinator.session.gates.min_cycles == 3


# ── The rain gauge ───────────────────────────────────────────────


async def test_the_gauge_is_offered_once_and_can_be_skipped(hass, sources):
    """Optional means the setup completes without answering it."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    result = await _add_probe(hass, result["flow_id"], FIRST_PROBE)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_RAIN_ENTITY not in result["data"]


async def test_a_gauge_configured_at_setup_is_stored_on_the_installation(hass, sources):
    """One gauge for the site, beside the probes rather than inside one of them."""
    hass.states.async_set("sensor.rain_gauge", "12", {"unit_of_measurement": "mm"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    result = await _add_probe(
        hass,
        result["flow_id"],
        FIRST_PROBE,
        rain={CONF_RAIN_ENTITY: "sensor.rain_gauge", "rain_sensor_type": "accumulator", "rain_quiet_minutes": 30},
    )

    assert result["data"][CONF_RAIN_ENTITY] == "sensor.rain_gauge"
    assert result["data"]["rain_sensor_type"] == "accumulator"


async def test_a_gauge_can_be_added_to_an_installation_already_running(hass, sources):
    """The path that matters for an entry created before the gauge existed.

    Without it, using a gauge would mean deleting the integration and starting
    over, which throws away the weeks of cycles that are the whole asset here.
    """
    hass.states.async_set("sensor.rain_gauge", "12", {"unit_of_measurement": "mm"})
    entry = await _installed(hass, [_record(FIRST_PROBE, "ortensia")])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rain"})
    assert result["step_id"] == "rain"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_RAIN_ENTITY: "sensor.rain_gauge", "rain_sensor_type": "event", "rain_quiet_minutes": 45},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    coordinator = hass.data[DOMAIN][entry.entry_id]["ortensia"]
    assert coordinator.rain_entity == "sensor.rain_gauge"
    assert coordinator.session.rain_policy.quiet_minutes == 45


async def test_clearing_the_gauge_actually_removes_it(hass, sources):
    """An empty answer has to beat the entity still named in the setup data."""
    hass.states.async_set("sensor.rain_gauge", "12", {"unit_of_measurement": "mm"})
    entry = await _installed(hass, [_record(FIRST_PROBE, "ortensia")])
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_RAIN_ENTITY: "sensor.rain_gauge"})
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rain"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"rain_sensor_type": "event", "rain_quiet_minutes": 30}
    )
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id]["ortensia"].rain_entity is None
