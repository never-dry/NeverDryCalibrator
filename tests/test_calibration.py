"""End to end over synthetic cycles: what is published, when, and on what evidence."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from helpers import RAW_AT_FIELD_CAPACITY, RAW_AT_WILTING_POINT, loam, run_cycles, true_slope
from model import CalibrationSession, CalibrationStatus, Observation, QualityGates, SoilTexture
from model.calibration import InvalidationReason


def _session(**overrides) -> CalibrationSession:
    """A session on the reference soil, with gate overrides for the test at hand."""
    return CalibrationSession(soil=loam(), gates=QualityGates(**overrides))


def test_nothing_is_published_before_the_cycles_are_earned():
    """Four irrigations leave three closed cycles, which is not yet five."""
    session = _session()
    now = run_cycles(session, cycles=4)
    verdict = session.refit(now)
    assert not verdict.passed
    assert "cycles" in verdict.failures
    assert session.status is CalibrationStatus.COLLECTING
    assert session.calibrated_reading(60.0) is None


def test_the_fifth_complete_cycle_publishes_the_calibration():
    """Six irrigations close five cycles, and the default gate asks for five."""
    session = _session()
    now = run_cycles(session, cycles=6)
    verdict = session.refit(now)
    assert verdict.passed, verdict.failures
    assert session.status is CalibrationStatus.CALIBRATED
    assert session.fit is not None
    assert session.fit.cycle_count == 5


def test_the_recovered_line_is_the_line_that_generated_the_data():
    """The calibration must reproduce the synthetic probe it was fitted on."""
    session = _session()
    now = run_cycles(session, cycles=6)
    session.refit(now)
    soil = session.soil
    assert session.fit.slope == pytest.approx(true_slope(soil), rel=0.05)
    for raw in (RAW_AT_WILTING_POINT + 5, 50.0, RAW_AT_FIELD_CAPACITY - 5):
        available = (raw - RAW_AT_WILTING_POINT) / (RAW_AT_FIELD_CAPACITY - RAW_AT_WILTING_POINT)
        expected = soil.wilting_point + available * (soil.field_capacity - soil.wilting_point)
        assert session.calibrated_reading(raw, 20.0).moisture == pytest.approx(expected, abs=0.01)


def test_a_reading_outside_the_fitted_range_is_flagged_as_extrapolated():
    """The fit says over which part of the probe range it was ever tested."""
    session = _session()
    now = run_cycles(session, cycles=6)
    session.refit(now)
    assert session.calibrated_reading(session.fit.raw_max + 25.0, 20.0).extrapolated
    assert not session.calibrated_reading(session.fit.raw_max - 5.0, 20.0).extrapolated


def test_a_provisional_anchor_estimate_exists_but_is_not_the_published_value():
    """The physics-only estimate is available for display, never as a measurement."""
    session = _session()
    now = run_cycles(session, cycles=3)
    session.refit(now)
    assert session.fit is None
    assert session.provisional_fit is not None
    assert session.provisional_fit.source == "anchors"


def test_available_water_is_reported_alongside_the_water_content():
    """The published reading answers both questions the two scales ask."""
    session = _session()
    now = run_cycles(session, cycles=6)
    session.refit(now)
    reading = session.calibrated_reading(RAW_AT_FIELD_CAPACITY, 20.0)
    assert reading.available_fraction == pytest.approx(1.0, abs=0.05)
    assert session.calibrated_reading(RAW_AT_WILTING_POINT, 20.0).available_fraction == pytest.approx(0.0, abs=0.05)


def test_a_probe_wired_backwards_is_refused_rather_than_inverted():
    """A negative slope is an installation fault, and hiding it would keep it hidden."""
    session = _session()
    soil = session.soil
    now = datetime(2026, 4, 1, 6, 0)
    for _cycle in range(7):
        deficit = 0.0
        session.note_irrigation(now)
        last_irrigation = now
        now += timedelta(hours=4)
        for _hour in range(8 * 24):
            deficit += 4.0 / 24.0
            inverted = 100.0 - (
                RAW_AT_WILTING_POINT + (RAW_AT_FIELD_CAPACITY - RAW_AT_WILTING_POINT) * soil.available_fraction(deficit)
            )
            session.observe(
                Observation(
                    taken_at=now,
                    raw_percent=inverted,
                    deficit_mm=deficit,
                    deficit_age_s=60.0,
                    probe_temperature_c=18.0,
                    probe_temperature_age_s=120.0,
                    seconds_since_irrigation=(now - last_irrigation).total_seconds(),
                )
            )
            now += timedelta(hours=1)
    verdict = session.refit(now)
    assert not verdict.passed
    assert "slope_sign" in verdict.failures
    assert session.calibrated_reading(50.0) is None


def test_a_stale_probe_marks_the_session_offline():
    """The temperature sentinel is what notices a flat battery."""
    session = _session()
    now = run_cycles(session, cycles=6)
    session.refit(now)
    session.observe(
        Observation(
            taken_at=now + timedelta(hours=6),
            raw_percent=55.0,
            deficit_mm=10.0,
            deficit_age_s=60.0,
            probe_temperature_c=18.0,
            probe_temperature_age_s=40_000.0,
            seconds_since_irrigation=30_000.0,
        )
    )
    assert session.status is CalibrationStatus.PROBE_OFFLINE


def test_changing_the_soil_keeps_the_measurements_and_drops_the_fit():
    """Raw readings and deficits are facts; the reference moisture was a reading of them."""
    session = _session()
    now = run_cycles(session, cycles=6)
    session.refit(now)
    samples_before = len(session.buffer)

    deeper = loam(root_depth_m=0.60)
    session.apply_soil(deeper, now)

    assert session.fit is None
    assert session.status is CalibrationStatus.INVALIDATED
    assert session.invalidation_reason is InvalidationReason.SOIL_CHANGED
    assert len(session.buffer) == samples_before
    assert session.buffer.last.soil_fingerprint == deeper.fingerprint()


def test_a_device_side_calibration_change_drops_the_samples_too():
    """After the probe is re-calibrated by hand, the old samples describe another instrument."""
    session = _session()
    now = run_cycles(session, cycles=6)
    session.refit(now)
    session.invalidate(InvalidationReason.DEVICE_CALIBRATION_CHANGED, now)
    assert session.fit is None
    assert len(session.buffer) == 0
    assert session.tracker.cycles == []


def test_drift_is_measured_against_the_published_line():
    """A calibration that stops agreeing with the reference says so before it is trusted."""
    session = _session()
    now = run_cycles(session, cycles=6)
    session.refit(now)
    assert session.update_drift() == pytest.approx(0.0, abs=0.01)

    soil = session.soil
    for step in range(12):
        stamp = now + timedelta(hours=step + 1)
        session.observe(
            Observation(
                taken_at=stamp,
                raw_percent=20.0,  # probe stuck at the dry end while the soil is wet
                deficit_mm=1.0,
                deficit_age_s=60.0,
                probe_temperature_c=18.0,
                probe_temperature_age_s=120.0,
                seconds_since_irrigation=30_000.0,
            )
        )
    session.update_drift()
    assert session.drift_rmse > session.gates.max_rmse(soil)
    assert session.status is CalibrationStatus.DRIFTING


def test_marking_field_capacity_by_hand_creates_an_anchor():
    """The escape hatch for a site whose deficit source cannot be trusted yet."""
    session = _session()
    now = datetime(2026, 7, 1, 9, 0)
    recorded = session.mark_field_capacity(
        Observation(taken_at=now, raw_percent=77.0, deficit_mm=None, probe_temperature_c=19.0),
        now,
    )
    assert recorded
    assert session.tracker.open_cycle.wet_anchor.raw_percent == pytest.approx(77.0)
    assert session.tracker.open_cycle.wet_anchor.reference_moisture == pytest.approx(session.soil.field_capacity)


def test_reset_forgets_everything():
    """After a probe is moved, its history describes another patch of soil."""
    session = _session()
    now = run_cycles(session, cycles=6)
    session.refit(now)
    session.reset(now)
    assert session.fit is None
    assert len(session.buffer) == 0
    assert session.status is CalibrationStatus.COLLECTING


def test_the_session_survives_a_restart():
    """Weeks of collected cycles must not be lost to a Home Assistant restart."""
    session = _session()
    now = run_cycles(session, cycles=6)
    session.refit(now)

    restored = CalibrationSession.from_dict(json.loads(json.dumps(session.to_dict())))

    assert restored.status is CalibrationStatus.CALIBRATED
    assert restored.fit.slope == pytest.approx(session.fit.slope)
    assert len(restored.buffer) == len(session.buffer)
    assert restored.calibrated_reading(60.0, 20.0).moisture == pytest.approx(
        session.calibrated_reading(60.0, 20.0).moisture
    )


def test_a_stored_fit_from_another_soil_is_not_restored():
    """A fit is a statement about one reservoir and does not survive a change of it."""
    session = _session()
    now = run_cycles(session, cycles=6)
    session.refit(now)
    payload = session.to_dict()
    payload["soil"]["root_depth_m"] = 0.9

    restored = CalibrationSession.from_dict(payload)

    assert restored.fit is None
    assert restored.status is not CalibrationStatus.CALIBRATED


def test_an_unreadable_store_costs_history_not_the_integration():
    """A corrupted payload restarts collection instead of raising."""
    restored = CalibrationSession.from_dict({"soil": {"texture": str(SoilTexture.LOAM)}, "samples": [{"bad": 1}]})
    assert len(restored.buffer) == 0
    assert restored.status is CalibrationStatus.COLLECTING


def test_a_lowered_cycle_gate_publishes_sooner():
    """The five-cycle rule is a default, not a law: a site may trade evidence for speed."""
    session = _session(min_cycles=2, min_samples=20)
    now = run_cycles(session, cycles=3)
    verdict = session.refit(now)
    assert verdict.passed, verdict.failures
    assert session.fit.cycle_count == 2
