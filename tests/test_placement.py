"""The placement diagnostic: six signatures, and the silence around them.

Each test builds the cycles that produce one signature and asserts that exactly
that signature is raised. The cases that assert *nothing* is raised carry as much
weight as the others: a diagnostic that cries wolf on a well-installed probe gets
switched off, and then it protects nobody.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from helpers import RAW_AT_FIELD_CAPACITY, RAW_AT_WILTING_POINT, loam, probe_index, run_cycles
from model import (
    CalibrationSession,
    PlacementConfidence,
    PlacementPolicy,
    PlacementSuspicion,
    Sample,
    SoilProfile,
    WaterSource,
    assess_placement,
)
from model.cycles import DryDownCycle

START = datetime(2026, 4, 1, 6, 0)


def _sample(soil: SoilProfile, raw: float, deficit_mm: float, cycle_index: int, at: datetime) -> Sample:
    """One admitted pair, with the reference derived exactly as the session derives it."""
    return Sample(
        taken_at=at,
        raw_percent=raw,
        deficit_mm=deficit_mm,
        reference_moisture=soil.moisture_at_deficit(deficit_mm),
        soil_fingerprint=soil.fingerprint(),
        cycle_index=cycle_index,
        probe_temperature_c=20.0,
    )


def _cycle(
    soil: SoilProfile,
    index: int,
    *,
    wet_raw: float,
    dry_raw: float,
    wet_deficit: float = 1.0,
    dry_deficit: float = 35.0,
    sample_count: int = 20,
) -> DryDownCycle:
    """A closed cycle with the two anchors and the extremes a real one would have."""
    opened = START + timedelta(days=10 * index)
    wet = _sample(soil, wet_raw, wet_deficit, index, opened)
    dry = _sample(soil, dry_raw, dry_deficit, index, opened + timedelta(days=8))
    return DryDownCycle(
        index=index,
        opened_at=opened,
        wet_anchor=wet,
        dry_anchor=dry,
        sample_count=sample_count,
        min_deficit_mm=wet_deficit,
        max_deficit_mm=dry_deficit,
        min_raw_percent=min(wet_raw, dry_raw),
        max_raw_percent=max(wet_raw, dry_raw),
        closed_at=opened + timedelta(days=9),
        closure=None,
    )


def _healthy(soil: SoilProfile, count: int = 5) -> list[DryDownCycle]:
    """Cycles from a probe that is where it should be: wide, consistent, steady."""
    return [
        _cycle(soil, index, wet_raw=RAW_AT_FIELD_CAPACITY - index * 0.3, dry_raw=RAW_AT_WILTING_POINT + index * 0.2)
        for index in range(1, count + 1)
    ]


def _quiet_samples(soil: SoilProfile, cycles: list[DryDownCycle], step: float = 0.2) -> list[Sample]:
    """Samples whose index moves as little as the soil does between two readings."""
    samples: list[Sample] = []
    for cycle in cycles:
        at = cycle.opened_at
        for position in range(12):
            deficit = 2.0 + position * 0.05
            samples.append(_sample(soil, 60.0 + position * step, deficit, cycle.index, at))
            at += timedelta(minutes=10)
    return samples


# ── Silence ──────────────────────────────────────────────────────


def test_below_the_minimum_cycles_it_says_nothing():
    """Two cycles are not enough to accuse an installation of anything."""
    soil = loam()
    verdict = assess_placement(_healthy(soil, count=2), [], soil)
    assert verdict.confidence is PlacementConfidence.NOT_ENOUGH_EVIDENCE
    assert verdict.suspicions == ()
    assert verdict.primary is None


def test_no_cycles_at_all_is_not_an_accusation():
    """A fresh installation must not open with a warning about itself."""
    soil = loam()
    verdict = assess_placement([], [], soil)
    assert verdict.confidence is PlacementConfidence.NOT_ENOUGH_EVIDENCE
    assert verdict.evidence["cycles_considered"] == 0.0


def test_a_well_placed_probe_raises_nothing():
    """The case that matters most: a good installation stays quiet."""
    soil = loam()
    cycles = _healthy(soil)
    verdict = assess_placement(cycles, _quiet_samples(soil, cycles), soil)
    assert verdict.confidence is PlacementConfidence.PLAUSIBLE
    assert verdict.suspicions == ()


# ── The five signatures ──────────────────────────────────────────


def test_a_probe_that_never_moves_is_not_in_the_water():
    """The reservoir emptied and the index travelled one point."""
    soil = loam()
    cycles = [_cycle(soil, index, wet_raw=50.5, dry_raw=49.8) for index in range(1, 6)]
    verdict = assess_placement(cycles, [], soil)
    assert verdict.confidence is PlacementConfidence.SUSPECT
    assert PlacementSuspicion.NO_RESPONSE in verdict.suspicions
    # A probe that is not in the water is not *also* a coarse probe: one problem,
    # one message, and the fixes are different.
    assert PlacementSuspicion.COARSE_RESPONSE not in verdict.suspicions


def test_a_probe_too_coarse_to_meet_the_error_budget_is_flagged():
    """Six index points over the whole reservoir costs more than two points of VWC."""
    soil = loam()
    cycles = [_cycle(soil, index, wet_raw=53.0, dry_raw=47.0) for index in range(1, 6)]
    verdict = assess_placement(cycles, [], soil)
    assert verdict.suspicions == (PlacementSuspicion.COARSE_RESPONSE,)
    assert verdict.evidence["points_per_reservoir"] < 10.0


def test_one_cycle_that_disagrees_with_the_others_is_flagged():
    """The irrigation that missed the probe is the signature worth catching."""
    soil = loam()
    cycles = _healthy(soil)
    # Third cycle: the water reached the probe only partly, so the same deficit
    # travel produced a quarter of the index travel.
    cycles[2] = _cycle(soil, 3, wet_raw=RAW_AT_WILTING_POINT + 14.0, dry_raw=RAW_AT_WILTING_POINT)
    verdict = assess_placement(cycles, [], soil)
    assert PlacementSuspicion.UNSTABLE_BETWEEN_CYCLES in verdict.suspicions
    assert verdict.evidence["slope_spread"] > verdict.evidence["slope_spread_limit"]


def test_a_wet_anchor_that_slides_one_way_is_drift():
    """Field capacity reading lower every cycle is the soil letting go of the shaft."""
    soil = loam()
    cycles = [
        _cycle(soil, index, wet_raw=RAW_AT_FIELD_CAPACITY - index * 4.0, dry_raw=RAW_AT_WILTING_POINT)
        for index in range(1, 6)
    ]
    verdict = assess_placement(cycles, [], soil)
    assert PlacementSuspicion.WET_ANCHOR_DRIFT in verdict.suspicions


def test_a_wet_anchor_that_wobbles_is_not_drift():
    """The same excursion without a direction is noise, and digging it up would be wrong."""
    soil = loam()
    offsets = [0.0, -9.0, 0.0, -9.0, -9.0]
    cycles = [
        _cycle(soil, index, wet_raw=RAW_AT_FIELD_CAPACITY + offset, dry_raw=RAW_AT_WILTING_POINT)
        for index, offset in enumerate(offsets, start=1)
    ]
    verdict = assess_placement(cycles, [], soil)
    assert PlacementSuspicion.WET_ANCHOR_DRIFT not in verdict.suspicions


def test_an_index_that_jumps_while_the_soil_stands_still_is_poor_contact():
    """Soil does not change by four points in ten minutes. An air gap does."""
    soil = loam()
    cycles = _healthy(soil)
    generator = random.Random(3)
    samples: list[Sample] = []
    for cycle in cycles:
        at = cycle.opened_at
        for _position in range(12):
            samples.append(_sample(soil, 60.0 + generator.uniform(-4.0, 4.0), 2.0, cycle.index, at))
            at += timedelta(minutes=10)
    verdict = assess_placement(cycles, samples, soil)
    assert PlacementSuspicion.POOR_CONTACT in verdict.suspicions
    assert verdict.evidence["still_raw_step"] > verdict.evidence["still_raw_step_limit"]


def test_too_few_standing_still_pairs_says_nothing_about_contact():
    """Three quiet readings are not a measurement of anything."""
    soil = loam()
    cycles = _healthy(soil)
    samples = [
        _sample(soil, 60.0 + offset * 9.0, 2.0, cycles[0].index, cycles[0].opened_at + timedelta(minutes=10 * offset))
        for offset in range(3)
    ]
    verdict = assess_placement(cycles, samples, soil)
    assert PlacementSuspicion.POOR_CONTACT not in verdict.suspicions
    assert verdict.evidence["still_pairs"] == 2.0


def test_a_step_across_a_cycle_boundary_is_not_a_jump():
    """The gap between two cycles contains an irrigation: the index is meant to move."""
    soil = loam()
    cycles = _healthy(soil)
    samples = [
        _sample(soil, 30.0, 2.0, cycles[0].index, cycles[0].opened_at),
        _sample(soil, 75.0, 2.0, cycles[1].index, cycles[1].opened_at),
    ]
    verdict = assess_placement(cycles, samples, soil)
    assert verdict.evidence["still_pairs"] == 0.0


# ── Reporting ────────────────────────────────────────────────────


def test_every_suspicion_is_reported_not_just_the_first():
    """One diagnosis at a time makes a user wait twice for one problem."""
    soil = loam()
    cycles = [_cycle(soil, index, wet_raw=56.0 - index * 0.8, dry_raw=47.0) for index in range(1, 6)]
    verdict = assess_placement(cycles, [], soil)
    assert PlacementSuspicion.COARSE_RESPONSE in verdict.suspicions
    assert PlacementSuspicion.WET_ANCHOR_DRIFT in verdict.suspicions


def test_the_primary_suspicion_is_the_worst_one():
    """Only one of them is worth interrupting the user for."""
    soil = loam()
    cycles = [_cycle(soil, index, wet_raw=50.5 - index * 0.1, dry_raw=49.8) for index in range(1, 6)]
    verdict = assess_placement(cycles, [], soil)
    assert verdict.primary is PlacementSuspicion.NO_RESPONSE


def test_the_verdict_serializes_for_the_attributes():
    """What the entity publishes must survive the trip to a state attribute."""
    soil = loam()
    cycles = _healthy(soil)
    payload = assess_placement(cycles, _quiet_samples(soil, cycles), soil).to_dict()
    assert payload["confidence"] == "plausible"
    assert payload["suspicions"] == []
    assert payload["primary"] is None
    assert "points_per_reservoir" in payload["evidence"]


def test_the_policy_survives_the_store():
    """Thresholds round-trip through the config entry like every other policy."""
    policy = PlacementPolicy(min_cycles=4, max_slope_spread=0.8)
    assert PlacementPolicy.from_dict(policy.to_dict()) == policy
    assert PlacementPolicy.from_dict({}) == PlacementPolicy()


# ── Through the session ──────────────────────────────────────────


def test_the_synthetic_probe_is_judged_plausible():
    """End to end: the well-behaved generator must not trip any signature."""
    session = CalibrationSession(soil=loam())
    run_cycles(session, cycles=6)
    verdict = session.assess_placement()
    assert verdict.confidence is PlacementConfidence.PLAUSIBLE, verdict.evidence


def test_a_probe_outside_the_wetted_volume_is_caught_end_to_end():
    """The same run, with a probe that reports the same index whatever the soil does."""
    session = CalibrationSession(soil=loam())
    run_cycles(session, cycles=6, index_fn=lambda soil, deficit, temperature, noise: 50.0 + noise * 0.05)
    verdict = session.assess_placement()
    assert verdict.confidence is PlacementConfidence.SUSPECT
    assert verdict.primary is PlacementSuspicion.NO_RESPONSE


def test_placement_never_touches_the_calibration():
    """The diagnostic describes; only the gates decide. Asserted, not assumed."""
    session = CalibrationSession(soil=loam())
    run_cycles(session, cycles=6, index_fn=lambda soil, deficit, temperature, noise: 50.0 + noise * 0.05)
    before = session.status
    session.assess_placement()
    assert session.status is before


def test_a_temperature_only_probe_still_reads_as_placed():
    """A sanity check that the healthy generator is not passing by accident."""
    session = CalibrationSession(soil=loam())
    run_cycles(
        session,
        cycles=6,
        index_fn=lambda soil, deficit, temperature, noise: probe_index(soil, deficit, temperature, noise),
    )
    assert session.assess_placement().suspicions == ()


# ── Outside the wetted bulb ──────────────────────────────────────


def _sourced(cycles: list[DryDownCycle], sources: list[WaterSource]) -> list[DryDownCycle]:
    """Label a run of cycles with the water that opened each of them."""
    for cycle, source in zip(cycles, sources, strict=True):
        cycle.water_source = source
    return cycles


def _bulb_cycles(soil: SoilProfile, rain_wet: float, irrigation_wet: float) -> list[DryDownCycle]:
    """Four cycles, two of each water, alternating as a real season would.

    Alternating rather than grouped on purpose: two rainy weeks followed by two
    dry ones would confound the comparison with anything that drifts over time,
    and the interleaved layout is the one a user actually lives through.
    """
    cycles = [
        _cycle(soil, 1, wet_raw=rain_wet, dry_raw=RAW_AT_WILTING_POINT),
        _cycle(soil, 2, wet_raw=irrigation_wet, dry_raw=RAW_AT_WILTING_POINT),
        _cycle(soil, 3, wet_raw=rain_wet, dry_raw=RAW_AT_WILTING_POINT),
        _cycle(soil, 4, wet_raw=irrigation_wet, dry_raw=RAW_AT_WILTING_POINT),
    ]
    return _sourced(
        cycles,
        [WaterSource.RAIN, WaterSource.IRRIGATION, WaterSource.RAIN, WaterSource.IRRIGATION],
    )


def test_a_probe_the_dripper_never_reaches_is_named():
    """Full after rain, half full after watering: the probe is outside the bulb.

    Both anchors were taken at the same deficit, because a cycle only opens once
    the water balance says the profile is nearly full. The water is identical;
    only the probe's answer differs.
    """
    soil = loam()
    cycles = _bulb_cycles(soil, rain_wet=RAW_AT_FIELD_CAPACITY, irrigation_wet=RAW_AT_FIELD_CAPACITY - 30.0)

    verdict = assess_placement(cycles, [], soil)

    assert PlacementSuspicion.OUTSIDE_WETTED_BULB in verdict.suspicions
    assert verdict.evidence["wet_anchor_gap"] == 30.0


def test_the_bulb_finding_outranks_everything_but_a_silent_probe():
    """It is the most actionable diagnosis available: it says where to dig."""
    soil = loam()
    cycles = _bulb_cycles(soil, rain_wet=RAW_AT_FIELD_CAPACITY, irrigation_wet=RAW_AT_FIELD_CAPACITY - 30.0)

    verdict = assess_placement(cycles, [], soil)

    assert verdict.primary is PlacementSuspicion.OUTSIDE_WETTED_BULB


def test_a_probe_inside_the_bulb_is_not_accused():
    """Rain and irrigation fill the same soil, so the two anchors agree."""
    soil = loam()
    cycles = _bulb_cycles(soil, rain_wet=RAW_AT_FIELD_CAPACITY, irrigation_wet=RAW_AT_FIELD_CAPACITY - 1.0)

    verdict = assess_placement(cycles, [], soil)

    assert PlacementSuspicion.OUTSIDE_WETTED_BULB not in verdict.suspicions


def test_a_dripper_wetter_than_the_rain_is_not_a_placement_fault():
    """The comparison is one-sided: more water from the dripper is just more water."""
    soil = loam()
    cycles = _bulb_cycles(soil, rain_wet=RAW_AT_FIELD_CAPACITY - 30.0, irrigation_wet=RAW_AT_FIELD_CAPACITY)

    verdict = assess_placement(cycles, [], soil)

    assert PlacementSuspicion.OUTSIDE_WETTED_BULB not in verdict.suspicions


def test_without_both_kinds_of_water_the_comparison_stays_silent():
    """A rainless month leaves nothing to compare, and silence is the honest output."""
    soil = loam()
    cycles = _sourced(
        _healthy(soil, count=4),
        [WaterSource.IRRIGATION] * 4,
    )

    verdict = assess_placement(cycles, [], soil)

    assert PlacementSuspicion.OUTSIDE_WETTED_BULB not in verdict.suspicions
    assert verdict.evidence["rain_cycles"] == 0.0
    assert verdict.evidence["irrigation_cycles"] == 4.0


def test_one_cycle_of_each_is_an_anecdote_and_not_evidence():
    """Two of each is the floor: a single pair cannot separate a difference from noise."""
    soil = loam()
    cycles = _sourced(
        [
            _cycle(soil, 1, wet_raw=RAW_AT_FIELD_CAPACITY, dry_raw=RAW_AT_WILTING_POINT),
            _cycle(soil, 2, wet_raw=RAW_AT_FIELD_CAPACITY - 30.0, dry_raw=RAW_AT_WILTING_POINT),
            _cycle(soil, 3, wet_raw=RAW_AT_FIELD_CAPACITY - 30.0, dry_raw=RAW_AT_WILTING_POINT),
        ],
        [WaterSource.RAIN, WaterSource.IRRIGATION, WaterSource.UNKNOWN],
    )

    verdict = assess_placement(cycles, [], soil)

    assert PlacementSuspicion.OUTSIDE_WETTED_BULB not in verdict.suspicions


def test_cycles_of_unknown_or_mixed_water_are_left_out_of_the_comparison():
    """Attributing them by guesswork would break the one property that makes it work."""
    soil = loam()
    cycles = _sourced(
        _healthy(soil, count=4),
        [WaterSource.UNKNOWN, WaterSource.MIXED, WaterSource.RAIN, WaterSource.IRRIGATION],
    )

    verdict = assess_placement(cycles, [], soil)

    assert verdict.evidence["rain_cycles"] == 1.0
    assert verdict.evidence["irrigation_cycles"] == 1.0
