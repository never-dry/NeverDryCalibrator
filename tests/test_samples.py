"""Sample admission: what is refused, in which order, and why."""

from __future__ import annotations

from datetime import datetime

import pytest
from helpers import loam
from model import AdmissionPolicy, Observation, ProbeLiveness, RejectionReason, SampleBuffer, probe_liveness
from model.samples import evaluate

NOW = datetime(2026, 5, 1, 12, 0)


def _observation(**overrides) -> Observation:
    """A well-formed observation, with the field under test overridden."""
    defaults = {
        "taken_at": NOW,
        "raw_percent": 55.0,
        "deficit_mm": 12.0,
        "deficit_age_s": 60.0,
        "probe_temperature_c": 18.0,
        "probe_temperature_age_s": 300.0,
        "ambient_temperature_c": 21.0,
        "irrigation_active": False,
        "seconds_since_irrigation": 20_000.0,
    }
    defaults.update(overrides)
    return Observation(**defaults)


def _evaluate(observation: Observation, policy: AdmissionPolicy | None = None, since_last: float | None = None):
    """Run the admission rules against the reference soil."""
    return evaluate(
        observation=observation,
        policy=policy or AdmissionPolicy(),
        soil=loam(),
        cycle_index=1,
        seconds_since_last_sample=since_last,
    )


def test_a_settled_observation_becomes_a_sample():
    """The happy path: a fresh pair on drained, warm soil is admitted."""
    admission = _evaluate(_observation())
    assert admission.accepted
    assert admission.sample is not None
    assert admission.sample.reference_moisture == pytest.approx(loam().moisture_at_deficit(12.0))


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"raw_percent": None}, RejectionReason.PROBE_UNAVAILABLE),
        ({"raw_percent": 140.0}, RejectionReason.RAW_OUT_OF_RANGE),
        ({"deficit_mm": None}, RejectionReason.DEFICIT_UNAVAILABLE),
        ({"deficit_age_s": 9_000.0}, RejectionReason.DEFICIT_STALE),
        ({"probe_temperature_age_s": 30_000.0}, RejectionReason.PROBE_STALE),
        ({"irrigation_active": True}, RejectionReason.IRRIGATION_ACTIVE),
        ({"seconds_since_irrigation": 600.0}, RejectionReason.DRAINAGE_WINDOW),
        ({"probe_temperature_c": -3.0}, RejectionReason.FROZEN_SOIL),
    ],
)
def test_each_failure_mode_has_its_own_named_reason(overrides, reason):
    """Every refusal is named, because a probe that never calibrates must say why."""
    admission = _evaluate(_observation(**overrides))
    assert not admission.accepted
    assert admission.reason is reason


def test_rate_limiting_is_the_last_check():
    """Sampling too soon is reported only once everything else is in order."""
    admission = _evaluate(_observation(), since_last=60.0)
    assert admission.reason is RejectionReason.SAMPLED_TOO_SOON


def test_frost_guard_falls_back_to_ambient_temperature():
    """With no probe temperature, the ambient sensor still guards against frozen soil."""
    admission = _evaluate(
        _observation(probe_temperature_c=None, probe_temperature_age_s=None, ambient_temperature_c=-1.0)
    )
    assert admission.reason is RejectionReason.FROZEN_SOIL


def test_a_probe_without_temperature_is_not_declared_dead():
    """No sentinel means unknown liveness, which must not look like a failure."""
    observation = _observation(probe_temperature_c=None, probe_temperature_age_s=None)
    assert probe_liveness(observation, AdmissionPolicy()) is ProbeLiveness.UNKNOWN
    assert _evaluate(observation).accepted


def test_missing_temperature_can_be_made_mandatory():
    """A site that insists on the sentinel can require it."""
    policy = AdmissionPolicy(require_probe_temperature=True)
    observation = _observation(probe_temperature_c=None, probe_temperature_age_s=None)
    assert _evaluate(observation, policy).reason is RejectionReason.MISSING_PROBE_TEMPERATURE


def test_buffer_is_bounded_and_keeps_the_newest():
    """The buffer drops history rather than growing without bound."""
    buffer = SampleBuffer(capacity=3)
    for index in range(10):
        sample = _evaluate(_observation(raw_percent=float(index))).sample
        buffer.add(sample)
    assert len(buffer) == 3
    assert buffer.last.raw_percent == pytest.approx(9.0)


def test_rederiving_the_buffer_keeps_measurements_and_changes_interpretation():
    """A soil correction re-derives reference moisture, it does not touch the raw data."""
    buffer = SampleBuffer()
    buffer.add(_evaluate(_observation()).sample)
    deeper = loam(root_depth_m=0.60)
    buffer.rederive(deeper)
    rederived = buffer.last
    assert rederived.raw_percent == pytest.approx(55.0)
    assert rederived.deficit_mm == pytest.approx(12.0)
    assert rederived.reference_moisture == pytest.approx(deeper.moisture_at_deficit(12.0))
    assert rederived.soil_fingerprint == deeper.fingerprint()
