"""Cycle tracking: what opens a cycle, what closes it, and what makes it evidence."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from helpers import loam
from model import CyclePolicy, CycleTracker, Sample, TrackerState

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
