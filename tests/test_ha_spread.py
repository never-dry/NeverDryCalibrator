"""The probe comparison where a user can see it, and the precondition it rests on.

The domain tests prove the arithmetic refuses to compare probes nobody paired.
These prove the entity exists, reaches a state, and carries the same refusal:
a number that appears without anyone declaring which probes share soil would be
the failure this whole feature was written to avoid.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.neverdry_calibrator.const import CONF_PROBES, DOMAIN  # noqa: E402
from custom_components.neverdry_calibrator.probe import ProbeConfig  # noqa: E402

SPREAD_ENTITY = "sensor.neverdry_calibrator_probe_spread"


def _probe(name: str, group: str | None) -> dict:
    """One probe record, optionally declared to share soil with others."""
    record = {
        "probe_id": name.lower(),
        "probe_name": name,
        "moisture_entity": f"sensor.probe_{name.lower()}",
        "deficit_entity": "sensor.zone_deficit",
        "soil_texture": "loam",
        "root_depth": 30,
        "root_depth_unit": "cm",
    }
    if group is not None:
        record["comparison_group"] = group
    return record


async def _setup(hass, probes: list[dict], readings: dict[str, str]):
    """Bring up an entry with the given probes and the states they read."""
    hass.states.async_set("sensor.zone_deficit", "6", {"unit_of_measurement": "mm"})
    for entity_id, value in readings.items():
        hass.states.async_set(entity_id, value, {"unit_of_measurement": "%"})
    entry = MockConfigEntry(domain=DOMAIN, title="NeverDry Calibrator", data={CONF_PROBES: probes})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ── The precondition, at the surface ────────────────────────────


async def test_without_a_declared_group_the_entity_says_unknown(hass):
    """Not zero. Zero is the best possible news and must never be the default."""
    await _setup(
        hass,
        [_probe("Alfa", None), _probe("Beta", None)],
        {"sensor.probe_alfa": "20", "sensor.probe_beta": "80"},
    )
    state = hass.states.get(SPREAD_ENTITY)
    assert state is not None
    assert state.state == "unknown"
    assert state.attributes["groups_declared"] == []
    assert sorted(state.attributes["probes_without_group"]) == ["Alfa", "Beta"]


async def test_two_probes_sharing_soil_are_compared(hass):
    """The case the feature exists for, end to end."""
    await _setup(
        hass,
        [_probe("Alfa", "pot"), _probe("Beta", "pot")],
        {"sensor.probe_alfa": "41", "sensor.probe_beta": "57"},
    )
    state = hass.states.get(SPREAD_ENTITY)
    assert float(state.state) == 16.0
    assert state.attributes["groups_compared"] == ["pot"]
    assert state.attributes["widest_group"] == "pot"


async def test_probes_in_different_groups_are_never_mixed(hass):
    """Two pots are two experiments; the reported number is the worse of them."""
    await _setup(
        hass,
        [_probe("Alfa", "pot"), _probe("Beta", "pot"), _probe("Gamma", "bed"), _probe("Delta", "bed")],
        {
            "sensor.probe_alfa": "40",
            "sensor.probe_beta": "41",
            "sensor.probe_gamma": "30",
            "sensor.probe_delta": "55",
        },
    )
    state = hass.states.get(SPREAD_ENTITY)
    assert float(state.state) == 25.0
    assert state.attributes["widest_group"] == "bed"
    assert sorted(state.attributes["groups_compared"]) == ["bed", "pot"]


async def test_the_attributes_name_who_was_left_out(hass):
    """A group that shrank has to be distinguishable from one that did not."""
    await _setup(
        hass,
        [_probe("Alfa", "pot"), _probe("Beta", "pot"), _probe("Gamma", "pot")],
        {"sensor.probe_alfa": "40", "sensor.probe_beta": "44", "sensor.probe_gamma": "unavailable"},
    )
    detail = hass.states.get(SPREAD_ENTITY).attributes["detail"][0]
    assert detail["considered"] == ["Alfa", "Beta"]
    assert detail["excluded"] == {"Gamma": "no_reading"}


async def test_a_lone_probe_in_a_group_is_not_perfect_agreement(hass):
    """Labelling one probe and forgetting the rest must not publish a zero."""
    await _setup(
        hass, [_probe("Alfa", "pot"), _probe("Beta", None)], {"sensor.probe_alfa": "40", "sensor.probe_beta": "80"}
    )
    state = hass.states.get(SPREAD_ENTITY)
    assert state.state == "unknown"
    assert state.attributes["groups_declared"] == ["pot"]
    assert state.attributes["groups_compared"] == []


# ── The option that carries the declaration ─────────────────────


def test_whitespace_around_a_group_label_does_not_split_a_group():
    """A trailing space typed into a text field must not silently create a second pot."""
    a = ProbeConfig(probe_id="a", name="Alfa", settings={"comparison_group": " pot "})
    b = ProbeConfig(probe_id="b", name="Beta", settings={"comparison_group": "pot"})
    assert a.comparison_group == b.comparison_group == "pot"


def test_an_unset_group_reads_as_empty_rather_than_none():
    """The accessor is what the sensor trusts to decide membership."""
    assert ProbeConfig(probe_id="a", name="Alfa", settings={}).comparison_group == ""
    assert ProbeConfig(probe_id="a", name="Alfa", settings={"comparison_group": None}).comparison_group == ""


def test_the_group_survives_the_config_entry_round_trip():
    """An option that does not survive serialisation reverts to silence on restart."""
    probe = ProbeConfig(probe_id="a", name="Alfa", settings={"comparison_group": "pot"})
    assert ProbeConfig.from_dict(probe.to_dict()).comparison_group == "pot"
