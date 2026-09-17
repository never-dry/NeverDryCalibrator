"""The rain gauge: crediting millimetres, and deciding when a shower is a wetting.

Two families of test, and the second is the one that earns its keep. Crediting is
arithmetic with traps in it: a tipping bucket that reports the same depth twice,
a daily total that resets at midnight, a restart that restores a state already
counted. Wetting is judgement: how much rain counts, and when it is over.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from helpers import loam
from model import (
    AdmissionPolicy,
    CalibrationSession,
    Observation,
    RainPolicy,
    RainSensorKind,
    RainWitness,
    RejectionReason,
    WaterSource,
)
from model.samples import evaluate

START = datetime(2026, 9, 17, 8, 0)


def _witness(kind: RainSensorKind = RainSensorKind.ACCUMULATOR, taw: float = 40.0) -> RainWitness:
    """A witness on a forty millimetre reservoir: eight millimetres make a wetting."""
    return RainWitness(policy=RainPolicy(), total_available_water_mm=taw, kind=kind)


# ── Crediting: accumulator ───────────────────────────────────────


def test_the_first_reading_after_a_restart_credits_nothing():
    """A restored counter holds rain that fell before this process existed."""
    witness = _witness()

    update = witness.observe(137.4, START)

    assert update.credited_mm == 0.0
    assert witness.baseline_mm == 137.4


def test_an_accumulator_credits_only_what_it_gained():
    """The value is a total; the rain is the difference between two totals."""
    witness = _witness()
    witness.observe(100.0, START)

    update = witness.observe(103.5, START + timedelta(minutes=10))

    assert update.credited_mm == 3.5
    assert update.accumulated_mm == 3.5


def test_a_counter_that_falls_is_a_reset_and_not_negative_rain():
    """Midnight on a daily total, or a rolling window ageing out. Never a drought."""
    witness = _witness()
    witness.observe(12.0, START)
    witness.observe(0.0, START + timedelta(hours=1))

    update = witness.observe(2.0, START + timedelta(hours=2))

    assert update.credited_mm == 2.0, "rain after the reset is credited from the new baseline"


# ── Crediting: tipping bucket ────────────────────────────────────


def test_two_identical_tips_are_two_events():
    """The trap the marker exists for: the value repeats, the rain does not."""
    witness = _witness(RainSensorKind.EVENT)
    witness.observe(0.2, START, marker="t0")

    first = witness.observe(2.0, START + timedelta(minutes=5), marker="t1")
    second = witness.observe(2.0, START + timedelta(minutes=10), marker="t2")

    assert (first.credited_mm, second.credited_mm) == (2.0, 2.0)
    assert second.accumulated_mm == 4.0


def test_polling_an_untouched_bucket_credits_nothing():
    """Five minutes later, nothing tipped: the same state is the same event."""
    witness = _witness(RainSensorKind.EVENT)
    witness.observe(0.2, START, marker="t0")
    witness.observe(2.0, START + timedelta(minutes=5), marker="t1")

    update = witness.observe(2.0, START + timedelta(minutes=10), marker="t1")

    assert update.credited_mm == 0.0


def test_without_a_marker_only_a_changed_value_counts():
    """The conservative half of an unidentifiable reading: under-count, never invent."""
    witness = _witness(RainSensorKind.EVENT)
    witness.observe(0.2, START)

    repeated = witness.observe(0.2, START + timedelta(minutes=5))
    changed = witness.observe(1.0, START + timedelta(minutes=10))

    assert (repeated.credited_mm, changed.credited_mm) == (0.0, 1.0)


# ── Wetting: how much, and when it is over ───────────────────────


def test_a_shower_under_the_threshold_is_never_a_wetting():
    """Four millimetres on forty wet the surface. They do not refill the reservoir."""
    witness = _witness()
    witness.observe(0.0, START)
    witness.observe(4.0, START + timedelta(minutes=10))

    update = witness.observe(4.0, START + timedelta(hours=2))

    assert update.wetting_ended_at is None
    assert update.raining is False, "the event is over, it simply never qualified"


def test_a_qualifying_event_closes_at_the_last_drop_not_at_the_poll():
    """The drainage window has to start when the water stopped arriving."""
    witness = _witness()
    witness.observe(0.0, START)
    witness.observe(5.0, START + timedelta(minutes=10))
    last_drop = START + timedelta(minutes=20)
    witness.observe(9.0, last_drop)

    update = witness.observe(9.0, START + timedelta(hours=3))

    assert update.wetting_ended_at == last_drop
    assert update.wetting_depth_mm == 9.0


def test_rain_still_falling_is_not_yet_an_event():
    """A storm is one wetting, not one per poll, however long it lasts."""
    witness = _witness()
    witness.observe(0.0, START)
    witness.observe(9.0, START + timedelta(minutes=10))

    update = witness.observe(14.0, START + timedelta(minutes=20))

    assert update.raining is True
    assert update.wetting_ended_at is None
    assert witness.qualified is True, "it will be a wetting; it is not over yet"


def test_a_long_drizzle_adds_up_to_a_wetting():
    """No single reading qualifies, and eight millimetres of drizzle still refill."""
    witness = _witness()
    witness.observe(0.0, START)
    at = START
    for step in range(8):
        at = START + timedelta(minutes=10 * (step + 1))
        witness.observe(1.0 * (step + 1), at)

    update = witness.observe(8.0, at + timedelta(hours=1))

    assert update.wetting_ended_at == at
    assert update.wetting_depth_mm == 8.0


def test_the_threshold_follows_the_reservoir():
    """Eight millimetres refill a pot of sand and barely wet a metre of clay."""
    small = _witness(taw=20.0)
    large = _witness(taw=120.0)

    assert small.event_depth_mm == 4.0
    assert large.event_depth_mm == 24.0


def test_a_reservoir_of_nothing_never_qualifies():
    """Guard against a misconfigured soil turning every drop into a cycle."""
    witness = _witness(taw=0.0)
    witness.observe(0.0, START)
    witness.observe(50.0, START + timedelta(minutes=10))

    update = witness.observe(50.0, START + timedelta(hours=2))

    assert update.wetting_ended_at is None


# ── The session: rain as water delivered ─────────────────────────


def _session() -> CalibrationSession:
    """A session on loam, with the witness it builds for itself."""
    return CalibrationSession(soil=loam())


def test_the_session_treats_a_qualifying_event_as_water_delivered():
    """The promise the feature was asked for: a rain gauge counts as an irrigation."""
    session = _session()
    session.note_rain(0.0, START)
    session.note_rain(session.rain_witness.event_depth_mm + 1.0, START + timedelta(minutes=10))

    update = session.note_rain(session.rain_witness.event_depth_mm + 1.0, START + timedelta(hours=2))

    assert update.is_wetting
    assert session.tracker.pending_water_source is WaterSource.RAIN
    assert session.tracker.last_irrigation_at == START + timedelta(minutes=10)


def test_a_probe_with_no_gauge_reports_no_rain():
    """Never fed, the witness answers the only honest thing: nothing was seen."""
    session = _session()

    raining, since = session.rain_state(START)

    assert raining is False
    assert since is None


def test_rain_survives_a_restart_mid_shower():
    """The accumulation persists; the baseline deliberately does not."""
    session = _session()
    session.note_rain(0.0, START)
    session.note_rain(4.0, START + timedelta(minutes=10))

    restored = CalibrationSession.from_dict(session.to_dict())

    assert restored.rain_witness.accumulated_mm == 4.0
    assert restored.rain_witness.baseline_mm is None, "a restored gauge state is rebased, not credited"


# ── Admission: rain that did not qualify still contaminates ──────


def _observation(**overrides) -> Observation:
    """A reading that would be admitted, unless an override spoils it."""
    fields = {
        "taken_at": START,
        "raw_percent": 55.0,
        "deficit_mm": 20.0,
        "deficit_age_s": 60.0,
        "probe_temperature_c": 19.0,
        "probe_temperature_age_s": 60.0,
    }
    fields.update(overrides)
    return Observation(**fields)


def _admit(observation: Observation):
    """Run the admission rules alone, without the cycle attribution behind them.

    Deliberately not through the session: a reading at twenty millimetres of
    deficit is refused there for a different and correct reason, having no wet
    anchor behind it, and that would hide which rule is under test.
    """
    return evaluate(
        observation=observation,
        policy=AdmissionPolicy(),
        soil=loam(),
        cycle_index=1,
        seconds_since_last_sample=None,
    )


def test_a_sample_taken_while_it_rains_is_refused_by_name():
    """The failure this feature exists to stop, and it used to be silent."""
    admission = _admit(_observation(rain_active=True))

    assert admission.accepted is False
    assert admission.reason is RejectionReason.RAIN_WETTING


def test_the_refusal_lasts_as_long_as_water_takes_to_redistribute():
    """Light rain leaves the profile in transit for the same window any water does."""
    window = AdmissionPolicy().drainage_seconds

    inside = _admit(_observation(seconds_since_rain=window - 60.0))
    outside = _admit(_observation(seconds_since_rain=window + 60.0))

    assert inside.reason is RejectionReason.RAIN_WETTING
    assert outside.accepted is True


def test_a_reading_with_no_weather_behind_it_is_untouched():
    """No rain state on the observation, no rain-shaped refusal."""
    assert _admit(_observation()).accepted is True
