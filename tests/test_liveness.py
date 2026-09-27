"""The two silent deaths of a cheap probe, and the state that refuses to be fooled.

Every test here pins a decision that a plausible simpler implementation gets
wrong, because both of these mechanisms fail in the direction of looking correct:
a bar derived with the wrong estimator calls a healthy probe dead, and a stall
detector that measures the wrong quantity fires on the first irrigation. The cases
that assert nothing is wrong therefore carry as much weight as the ones that
assert a fault.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from helpers import dead_probe, loam, run_cycles
from model import (
    CADENCE_LEARNING_MIN_S,
    LIVENESS_CEILING_S,
    AdmissionPolicy,
    CalibrationSession,
    CalibrationStatus,
    Observation,
    ProbeCadence,
    ProbeLiveness,
    RejectionReason,
    SensingWitness,
    probe_liveness,
)
from model.samples import evaluate

HOUR = 3600.0


def _at(seconds: float) -> datetime:
    """A clock reading, ``seconds`` after an arbitrary fixed origin."""
    return datetime(2026, 5, 1, 6, 0) + timedelta(seconds=seconds)


# ── The bar: ProbeCadence ────────────────────────────────────────


def test_no_evidence_falls_back_to_the_configured_timeout():
    """A probe with no demonstrated habit is judged exactly as it was before."""
    cadence = ProbeCadence()
    assert cadence.bar_s(0.0, fallback_s=1800.0) == 1800.0


def test_the_bar_is_the_longest_silence_and_not_a_typical_one():
    """The field bug, pinned: fast readings must not outvote the real heartbeat.

    An evening of thirty-second readings and one honest fifty-five-minute gap. Any
    estimator that treats the crowd as the signal puts the bar under a minute and
    declares the probe dead for keeping its own cadence.
    """
    cadence = ProbeCadence()
    for index in range(40):
        cadence.record(float(index * 30), 30.0)
    cadence.record(40 * 30 + 3300.0, 3300.0)
    later = 40 * 30 + 3300.0 + 3 * 24 * HOUR

    assert cadence.observed_max(later) == 3300.0
    assert cadence.bar_s(later, fallback_s=1800.0) == 3300.0 * 2


def test_a_regular_heartbeat_establishes_a_bar_at_all():
    """The commonest device there is, and the one a count-based gate never reaches.

    Every silence of a device with a steady cadence is dominated by the next equal
    one, so the retained sequence holds a single entry however long it has run.
    A gate that counted entries would leave such a probe on the configured fallback
    for ever, which is the whole feature not happening.
    """
    cadence = ProbeCadence()
    for index in range(200):
        cadence.record(float(index * 1800), 1800.0)
    at_s = 200 * 1800.0
    assert len(cadence.to_list()) == 1
    assert cadence.is_established(at_s)
    assert cadence.bar_s(at_s, fallback_s=7200.0) == 3600.0


def test_a_days_watching_is_required_before_the_bar_is_believed():
    """A bar learned from one daylight stretch misses the night, which is the long gap."""
    cadence = ProbeCadence()
    cadence.record(1800.0, 1800.0)
    assert not cadence.is_established(6 * HOUR)
    assert cadence.bar_s(6 * HOUR, fallback_s=7200.0) == 7200.0
    assert cadence.is_established(CADENCE_LEARNING_MIN_S + 1800.0)


def test_one_long_outage_does_not_become_the_bar_on_the_day_it_happened():
    """The second half of the gate: a silence must survive several chances to be beaten."""
    cadence = ProbeCadence()
    cadence.record(10 * HOUR, 10 * HOUR)
    assert not cadence.is_established(30 * HOUR)
    assert cadence.is_established(41 * HOUR)


def test_a_silence_still_running_is_not_evidence_that_it_is_normal():
    """Only ended stretches count, or every silence excuses itself by lasting."""
    cadence = ProbeCadence()
    cadence.record(0.0, 0.0)
    cadence.record(10.0, -5.0)
    assert cadence.observed_max(10.0) is None


def test_the_span_starts_when_the_silence_did_and_not_when_it_ended():
    """The device was being watched while it was quiet, so that time counts."""
    cadence = ProbeCadence()
    cadence.record(at_s=5000.0, quiet_s=4000.0)
    assert cadence.first_seen_s == 1000.0
    assert cadence.watched_s(5000.0) == 4000.0


def test_a_one_off_outage_stops_counting_once_the_window_turns():
    """The window is what keeps a single night of maintenance from widening the bar."""
    cadence = ProbeCadence(window_s=100.0)
    cadence.record(0.0, 5000.0)
    cadence.record(10.0, 30.0)
    cadence.record(20.0, 25.0)
    cadence.record(30.0, 20.0)
    assert cadence.observed_max(30.0) == 5000.0
    assert cadence.observed_max(200.0) is None


def test_the_ceiling_bounds_even_a_demonstrated_habit():
    """A probe may prove any cadence it likes; past the ceiling it cannot do this job."""
    cadence = ProbeCadence()
    cadence.record(5 * HOUR, 5 * HOUR)
    at_s = 30 * HOUR
    assert cadence.is_established(at_s)
    # Twice five hours would be ten; the ceiling brings it back to six.
    assert cadence.bar_s(at_s, fallback_s=1800.0) == LIVENESS_CEILING_S
    # And the fallback is capped by it too, so no configured value can outrun it.
    assert ProbeCadence().bar_s(0.0, fallback_s=48 * HOUR) == LIVENESS_CEILING_S


def test_the_bar_survives_a_restart():
    """The other half of the judgement is rebuilt from the states; this half is not.

    Without persistence a reload leaves an age measured against no bar, which means
    the configured fallback until a fresh day of silences has been watched.
    """
    cadence = ProbeCadence()
    for index, quiet in enumerate((3300.0, 900.0, 600.0)):
        cadence.record(float(index * 4000), quiet)
    at_s = 3 * 24 * HOUR
    restored = ProbeCadence.from_dict(cadence.to_dict())
    assert restored.to_list() == cadence.to_list()
    assert restored.first_seen_s == cadence.first_seen_s
    assert restored.is_established(at_s)
    assert restored.bar_s(at_s, fallback_s=1800.0) == cadence.bar_s(at_s, fallback_s=1800.0)


def test_a_store_written_before_the_span_existed_still_restores_the_silences():
    """The bare list an older store wrote costs one day of fallback, not the evidence."""
    restored = ProbeCadence.from_dict([[0.0, 3300.0], [4000.0, 900.0]])
    assert restored.observed_max(4000.0) == 3300.0
    assert restored.first_seen_s is not None


def test_a_malformed_stored_bar_is_refused_rather_than_trusted():
    """The stored bar is the piece that makes the verdict stricter, so it is validated."""
    assert ProbeCadence.from_list(None).to_list() == []
    assert ProbeCadence.from_list("nonsense").to_list() == []
    assert ProbeCadence.from_list([["a", "b"], [1.0], [10.0, -3.0], [20.0, 50.0]]).to_list() == [[20.0, 50.0]]


def test_a_stored_sequence_that_is_not_decreasing_is_repaired():
    """Replayed through ``record`` rather than assigned, so the invariant is re-established."""
    restored = ProbeCadence.from_list([[0.0, 10.0], [10.0, 900.0], [20.0, 20.0]])
    assert restored.observed_max(30.0) == 900.0
    assert restored.to_list() == [[10.0, 900.0], [20.0, 20.0]]


# ── The verdict: probe_liveness ──────────────────────────────────


def _observation(**kwargs) -> Observation:
    """An observation with everything the admission rules need, minus the overrides."""
    base = {
        "taken_at": _at(0),
        "raw_percent": 50.0,
        "deficit_mm": 10.0,
        "deficit_age_s": 60.0,
    }
    return Observation(**{**base, **kwargs})


def test_neither_sentinel_is_unknown_and_not_a_fault():
    """Absence of evidence must never read as evidence of death."""
    assert probe_liveness(_observation(), AdmissionPolicy()) is ProbeLiveness.UNKNOWN


def test_the_device_answers_before_the_temperature_channel():
    """The regression this whole channel exists for.

    A probe on ground that has stopped moving publishes nothing on its moisture
    entity while the rest of the device carries on. Judged on one channel it is
    dead; judged on the device it is a probe saying the soil has not moved.
    """
    observation = _observation(device_age_s=120.0, probe_temperature_age_s=40 * HOUR)
    assert probe_liveness(observation, AdmissionPolicy()) is ProbeLiveness.ALIVE


def test_the_temperature_channel_answers_when_no_device_could_be_resolved():
    """A registry that will not answer must not turn every probe into a dead one."""
    observation = _observation(device_age_s=None, probe_temperature_age_s=30.0)
    assert probe_liveness(observation, AdmissionPolicy()) is ProbeLiveness.ALIVE
    stale = _observation(device_age_s=None, probe_temperature_age_s=30 * HOUR)
    assert probe_liveness(stale, AdmissionPolicy()) is ProbeLiveness.STALE


def test_the_learned_bar_replaces_the_configured_one():
    """A probe reporting every three hours is alive at three hours, timeout or not."""
    policy = AdmissionPolicy(probe_timeout_s=2 * HOUR)
    observation = _observation(device_age_s=2.5 * HOUR)
    assert probe_liveness(observation, policy) is ProbeLiveness.STALE

    cadence = ProbeCadence()
    cadence.record(_at(-30 * HOUR).timestamp(), 3 * HOUR)
    cadence.record(_at(-2 * HOUR).timestamp(), 3 * HOUR)
    assert cadence.is_established(_at(0).timestamp())
    assert probe_liveness(observation, policy, cadence) is ProbeLiveness.ALIVE


def test_a_chatty_probe_is_judged_by_its_own_short_habit():
    """The bar errs short here, which the sibling project's deliberately does not.

    A probe that has never gone quiet for more than five minutes is not entitled to
    two hours of silence just because the default says so.
    """
    policy = AdmissionPolicy(probe_timeout_s=2 * HOUR)
    cadence = ProbeCadence()
    for index in range(300):
        cadence.record(_at(-30 * HOUR + index * 300).timestamp(), 300.0)
    assert cadence.is_established(_at(0).timestamp())
    assert probe_liveness(_observation(device_age_s=400.0), policy, cadence) is ProbeLiveness.ALIVE
    assert probe_liveness(_observation(device_age_s=1200.0), policy, cadence) is ProbeLiveness.STALE


# ── The second channel: SensingWitness ──────────────────────────


def test_an_index_that_answers_is_never_called_stalled():
    """Half a point of movement is an answer, and it re-anchors the stretch."""
    witness = SensingWitness()
    deficit = 0.0
    for step in range(40):
        deficit += 1.0
        witness.note(50.0 + step * 0.6, deficit, _at(step * 600))
    assert not witness.stalled(39.0)


def test_a_motionless_index_stalls_once_the_soil_has_demonstrably_dried():
    """The signature: the reference travels half the reservoir, the index does not move."""
    witness = SensingWitness()
    assert witness.stalled(39.0) is False
    deficit = 0.0
    for step in range(40):
        deficit += 1.0
        witness.note(50.0, deficit, _at(step * 600))
    assert witness.stalled(39.0)
    assert witness.stalled_travel_mm >= witness.required_travel_mm(39.0)


def test_water_arriving_resets_the_stretch_instead_of_completing_it():
    """The mistake that would make this fire on every irrigation.

    A delivery drops the deficit by most of the reservoir in one step. Accumulating
    the reference's absolute travel would read that as a full excursion and condemn
    any probe that had not refreshed inside the poll interval.
    """
    witness = SensingWitness()
    deficit = 0.0
    for step in range(15):
        deficit += 1.0
        witness.note(50.0, deficit, _at(step * 600))
    assert not witness.stalled(39.0)

    witness.note(50.0, 0.5, _at(20 * 600))
    assert witness.stalled_travel_mm == 0.0
    assert not witness.stalled(39.0)


def test_a_reservoir_of_nothing_can_never_stall():
    """No reservoir, no threshold to clear: a degenerate soil must not accuse the probe."""
    witness = SensingWitness()
    witness.note(50.0, 0.0, _at(0))
    witness.note(50.0, 100.0, _at(600))
    assert not witness.stalled(0.0)


def test_an_incomplete_reading_is_ignored_rather_than_anchored():
    """A missing raw or deficit says nothing about the electrode."""
    witness = SensingWitness()
    witness.note(None, 10.0, _at(0))
    witness.note(50.0, None, _at(600))
    assert witness.raw_at_anchor is None


def test_the_witness_survives_a_restart_and_refuses_half_an_anchor():
    """Travel measured from a deficit with no index recorded against it is not evidence."""
    witness = SensingWitness()
    witness.note(50.0, 0.0, _at(0))
    witness.note(50.0, 12.0, _at(600))
    restored = SensingWitness.from_dict(witness.to_dict())
    assert restored.to_dict() == witness.to_dict()

    assert SensingWitness.from_dict(None).raw_at_anchor is None
    half = witness.to_dict() | {"raw_at_anchor": None}
    assert SensingWitness.from_dict(half).deficit_at_anchor is None
    assert SensingWitness.from_dict(half).stalled_travel_mm == 0.0


# ── Admission: where the new refusals sit in the order ──────────


def test_a_stalled_electrode_outranks_every_physical_check_below_it():
    """None of the physical guards means anything about a number nobody is measuring."""
    admission = evaluate(
        observation=_observation(device_age_s=60.0, irrigation_active=True),
        policy=AdmissionPolicy(),
        soil=loam(),
        cycle_index=0,
        seconds_since_last_sample=None,
        sensing_stalled=True,
    )
    assert admission.reason is RejectionReason.PROBE_SENSING_STALLED


def test_silence_outranks_a_stall_because_a_silent_probe_proves_nothing():
    """A probe that is not talking cannot be shown to have stopped measuring."""
    admission = evaluate(
        observation=_observation(device_age_s=30 * HOUR),
        policy=AdmissionPolicy(),
        soil=loam(),
        cycle_index=0,
        seconds_since_last_sample=None,
        sensing_stalled=True,
    )
    assert admission.reason is RejectionReason.PROBE_STALE


def test_a_dying_battery_stops_the_calibration_with_its_own_reason():
    """The earliest indicator of the failure both channels above are built for."""
    admission = evaluate(
        observation=_observation(device_age_s=60.0, battery_percent=3.0),
        policy=AdmissionPolicy(),
        soil=loam(),
        cycle_index=0,
        seconds_since_last_sample=None,
    )
    assert admission.reason is RejectionReason.BATTERY_CRITICAL


def test_a_reported_zero_battery_is_treated_as_no_information():
    """A device that is talking cannot truthfully be at zero, so zero is a device bug.

    Refusing every sample from one of those would cost the calibration for a fault
    in a channel the calibration does not use.
    """
    admission = evaluate(
        observation=_observation(device_age_s=60.0, battery_percent=0.0),
        policy=AdmissionPolicy(),
        soil=loam(),
        cycle_index=0,
        seconds_since_last_sample=None,
    )
    assert admission.accepted


def test_a_floor_of_zero_turns_the_battery_check_off():
    """For the site whose probes report a battery level they invented."""
    admission = evaluate(
        observation=_observation(device_age_s=60.0, battery_percent=1.0),
        policy=AdmissionPolicy(min_battery_percent=0.0),
        soil=loam(),
        cycle_index=0,
        seconds_since_last_sample=None,
    )
    assert admission.accepted


def test_the_battery_floor_round_trips_through_the_policy():
    """A tuned floor must survive the options round trip, or it silently reverts."""
    policy = AdmissionPolicy(min_battery_percent=12.0)
    assert AdmissionPolicy.from_dict(policy.to_dict()) == policy
    assert AdmissionPolicy.from_dict({}).min_battery_percent == AdmissionPolicy().min_battery_percent


# ── Through the session ─────────────────────────────────────────


def test_the_session_learns_the_cadence_from_the_ages_it_is_handed():
    """The ended silence between two words, derived without the domain owning a clock."""
    session = CalibrationSession(soil=loam())
    for index in range(5):
        session.observe(
            Observation(
                taken_at=_at(index * 1800),
                raw_percent=40.0 + index,
                deficit_mm=float(index),
                deficit_age_s=30.0,
                device_age_s=60.0,
            )
        )
    assert session.last_device_seen_at == _at(4 * 1800 - 60)
    assert session.cadence.observed_max(_at(4 * 1800).timestamp()) == 1800.0
    # A day has not passed, so the learned bar is not in force yet.
    assert not session.cadence.is_established(_at(4 * 1800).timestamp())


def test_a_stalled_probe_is_reported_offline_and_recovers_when_it_answers():
    """The status a consumer reads, and the recovery a repaired probe is owed."""
    session = CalibrationSession(soil=loam())
    run_cycles(session, cycles=6, index_fn=dead_probe)
    assert session.sensing_stalled
    assert session.status is CalibrationStatus.PROBE_OFFLINE

    moved = session.sensing.raw_at_anchor + 5.0
    session.observe(
        Observation(
            taken_at=_at(10 * HOUR),
            raw_percent=moved,
            deficit_mm=5.0,
            deficit_age_s=30.0,
            device_age_s=60.0,
        )
    )
    assert not session.sensing_stalled


def test_the_whole_liveness_state_survives_the_store():
    """Both halves, because a session that restores one of them judges with neither."""
    session = CalibrationSession(soil=loam())
    run_cycles(session, cycles=6, index_fn=dead_probe)
    restored = CalibrationSession.from_dict(session.to_dict())

    assert restored.sensing_stalled == session.sensing_stalled
    assert restored.sensing.to_dict() == session.sensing.to_dict()
    assert restored.cadence.to_dict() == session.cadence.to_dict()
    assert restored.last_device_seen_at == session.last_device_seen_at


def test_a_swapped_source_entity_drops_the_cadence_it_learned():
    """A cadence is a statement about one device, and the new entity may not be on it."""
    from model import InvalidationReason

    session = CalibrationSession(soil=loam())
    for index in range(5):
        session.observe(
            Observation(
                taken_at=_at(index * 1800),
                raw_percent=40.0 + index,
                deficit_mm=float(index),
                deficit_age_s=30.0,
                device_age_s=60.0,
            )
        )
    assert session.cadence.observed_max(_at(4 * 1800).timestamp()) is not None

    session.invalidate(InvalidationReason.SOURCE_CHANGED, _at(5 * 1800))
    assert session.cadence.observed_max(_at(5 * 1800).timestamp()) is None
    assert session.last_device_seen_at is None


def test_a_turned_knob_keeps_the_cadence_and_drops_the_anchor():
    """It changes what the probe says, not how often it says it."""
    from model import InvalidationReason

    session = CalibrationSession(soil=loam())
    run_cycles(session, cycles=6, index_fn=dead_probe)
    learned = session.cadence.to_list()

    session.invalidate(InvalidationReason.DEVICE_CALIBRATION_CHANGED, _at(10 * HOUR))
    assert session.cadence.to_list() == learned
    assert session.sensing.raw_at_anchor is None
    assert not session.sensing_stalled
