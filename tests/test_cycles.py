"""Cycle tracking: what opens a cycle, what closes it, and what makes it evidence."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from helpers import loam
from model import CyclePolicy, CycleTracker, Sample, TrackerState, WaterSource

SOIL = loam()
TAW = SOIL.total_available_water_mm
START = datetime(2026, 6, 1, 8, 0)


def _sample(deficit_mm: float, minutes: int, raw: float = 50.0) -> Sample:
    """A sample at a given deficit, that many minutes into the run."""
    return Sample(
        taken_at=START + timedelta(minutes=minutes),
        raw_percent=raw,
        deficit_mm=deficit_mm,
        reference_moisture=SOIL.moisture_at_deficit(deficit_mm),
        soil_fingerprint=SOIL.fingerprint(),
    )


def _tracker(**policy_overrides) -> CycleTracker:
    """A tracker on the reference soil."""
    return CycleTracker(policy=CyclePolicy(**policy_overrides), total_available_water_mm=TAW)


def test_samples_before_the_first_wet_anchor_are_not_filed():
    """Without a wet anchor there is no way to place a reading on the moisture axis."""
    tracker = _tracker()
    assert tracker.observe(_sample(deficit_mm=20.0, minutes=0)) is None
    assert tracker.cycles == []


def test_a_low_deficit_reading_opens_a_cycle_and_becomes_its_wet_anchor():
    """Field capacity after drainage is the anchor the whole cycle hangs from."""
    tracker = _tracker()
    cycle = tracker.observe(_sample(deficit_mm=1.0, minutes=0, raw=78.0))
    assert cycle is not None
    assert tracker.state is TrackerState.DRYING
    assert cycle.wet_anchor.raw_percent == pytest.approx(78.0)


def test_the_driest_reading_becomes_the_dry_anchor():
    """The dry anchor tracks the deepest deficit the cycle reached."""
    tracker = _tracker()
    tracker.observe(_sample(deficit_mm=1.0, minutes=0))
    tracker.observe(_sample(deficit_mm=9.0, minutes=60, raw=40.0))
    cycle = tracker.observe(_sample(deficit_mm=5.0, minutes=120, raw=55.0))
    assert cycle.dry_anchor.deficit_mm == pytest.approx(9.0)


def test_irrigation_closes_the_cycle_and_the_next_anchor_opens_a_new_one():
    """One irrigation to dry-down span is one cycle, numbered in order."""
    tracker = _tracker()
    tracker.observe(_sample(deficit_mm=1.0, minutes=0))
    tracker.observe(_sample(deficit_mm=20.0, minutes=600))
    tracker.note_irrigation(START + timedelta(minutes=610))
    assert tracker.open_cycle is None
    tracker.observe(_sample(deficit_mm=0.5, minutes=800))
    assert [cycle.index for cycle in tracker.cycles] == [1, 2]


def test_a_cycle_that_never_dried_is_not_evidence():
    """A flat line is not a cycle: without a deficit span the probe learned nothing."""
    tracker = _tracker()
    tracker.observe(_sample(deficit_mm=1.0, minutes=0))
    for minute in range(1, 10):
        tracker.observe(_sample(deficit_mm=1.0 + minute * 0.1, minutes=minute * 60))
    tracker.note_irrigation(START + timedelta(hours=11))
    assert tracker.complete_cycles() == []


def test_a_cycle_with_span_and_samples_counts():
    """The three conditions together: anchors, samples, and a real dry-down."""
    tracker = _tracker()
    tracker.observe(_sample(deficit_mm=0.5, minutes=0))
    for step in range(1, 8):
        tracker.observe(_sample(deficit_mm=step * 3.0, minutes=step * 60))
    tracker.note_irrigation(START + timedelta(hours=9))
    assert len(tracker.complete_cycles()) == 1


def test_an_open_cycle_is_never_counted():
    """Evidence is counted only once the cycle closed: it can still be spoiled."""
    tracker = _tracker()
    tracker.observe(_sample(deficit_mm=0.5, minutes=0))
    for step in range(1, 8):
        tracker.observe(_sample(deficit_mm=step * 3.0, minutes=step * 60))
    assert tracker.complete_cycles() == []


def test_deficit_drop_detection_scales_with_the_reservoir():
    """Irrigation is a fraction of the reservoir, not an absolute number of millimetres."""
    tracker = _tracker(irrigation_drop_fraction=0.20)
    assert tracker.detects_irrigation(previous_deficit_mm=20.0, current_deficit_mm=2.0)
    assert not tracker.detects_irrigation(previous_deficit_mm=20.0, current_deficit_mm=18.0)


def test_tracker_round_trips_through_storage():
    """A restart must not lose the cycles already earned."""
    tracker = _tracker()
    tracker.observe(_sample(deficit_mm=0.5, minutes=0))
    for step in range(1, 8):
        tracker.observe(_sample(deficit_mm=step * 3.0, minutes=step * 60))
    tracker.note_irrigation(START + timedelta(hours=9))
    restored = CycleTracker.from_dict(tracker.to_dict(), tracker.policy, TAW)
    assert len(restored.complete_cycles()) == 1
    assert restored.next_index == tracker.next_index


# ── Which water filled the cycle ─────────────────────────────────


def test_a_cycle_records_the_water_that_opened_it():
    """The label is stamped when the cycle is born, not when the water arrived."""
    tracker = _tracker()
    tracker.note_irrigation(START, WaterSource.RAIN)

    tracker.observe(_sample(deficit_mm=0.5, minutes=240))

    assert tracker.cycles[-1].water_source is WaterSource.RAIN


def test_an_unnamed_witness_never_erases_a_named_one():
    """The deficit collapses after the rain the gauge already reported.

    Without this rule the slowest witness would have the last word, and every
    rain-opened cycle would end up labelled as water of unknown origin.
    """
    tracker = _tracker()
    tracker.note_irrigation(START, WaterSource.RAIN)

    tracker.note_irrigation(START + timedelta(minutes=5), WaterSource.UNKNOWN)

    assert tracker.pending_water_source is WaterSource.RAIN


def test_two_different_waters_before_one_cycle_are_mixed():
    """Irrigating on schedule the morning after a storm fills the profile twice."""
    tracker = _tracker()
    tracker.note_irrigation(START, WaterSource.RAIN)

    tracker.note_irrigation(START + timedelta(hours=8), WaterSource.IRRIGATION)

    assert tracker.pending_water_source is WaterSource.MIXED


def test_the_next_wetting_starts_the_attribution_over():
    """A mixed cycle does not poison the one after it."""
    tracker = _tracker()
    tracker.note_irrigation(START, WaterSource.RAIN)
    tracker.observe(_sample(deficit_mm=0.5, minutes=240))

    tracker.note_irrigation(START + timedelta(days=4), WaterSource.IRRIGATION)

    assert tracker.pending_water_source is WaterSource.IRRIGATION


def test_water_sources_are_counted_over_complete_cycles_only():
    """The count is what tells a user why the rain comparison is still silent."""
    tracker = _tracker()
    tracker.note_irrigation(START, WaterSource.RAIN)
    tracker.observe(_sample(deficit_mm=0.5, minutes=0))
    for step in range(1, 8):
        tracker.observe(_sample(deficit_mm=step * 3.0, minutes=step * 60))
    tracker.note_irrigation(START + timedelta(hours=9), WaterSource.IRRIGATION)

    counts = tracker.complete_cycles_by_source()

    assert counts[str(WaterSource.RAIN)] == 1
    assert counts[str(WaterSource.IRRIGATION)] == 0, "the irrigated cycle has not closed yet"


def test_the_water_source_survives_a_restart():
    """A label lost on restart would silently empty the rain comparison."""
    tracker = _tracker()
    tracker.note_irrigation(START, WaterSource.RAIN)
    tracker.observe(_sample(deficit_mm=0.5, minutes=0))

    restored = CycleTracker.from_dict(tracker.to_dict(), tracker.policy, TAW)

    assert restored.cycles[-1].water_source is WaterSource.RAIN


def test_a_cycle_stored_before_the_gauge_existed_stays_unknown():
    """An old record is not evidence about which water filled it, and must not pretend."""
    tracker = _tracker()
    tracker.observe(_sample(deficit_mm=0.5, minutes=0))
    payload = tracker.to_dict()
    del payload["cycles"][0]["water_source"]

    restored = CycleTracker.from_dict(payload, tracker.policy, TAW)

    assert restored.cycles[0].water_source is WaterSource.UNKNOWN
