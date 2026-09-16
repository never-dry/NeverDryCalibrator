"""The calibration aggregate: what the probe is worth, and whether we may say so.

This module holds the object the rest of the integration talks to. It owns the
sample buffer, the cycle tracker, the quality gates and the current fit, and it
is the only place allowed to decide that a probe is calibrated.

The rule the whole design turns on is that a calibration is either **earned or
absent**. A fit that has not passed its gates is not published as a slightly
worse number, it is not published at all: the calibrated entity stays unknown
and the diagnostic entity says exactly which gate is missing. A soil moisture
reading that looks authoritative and is not is worse than no reading, because an
irrigation controller downstream cannot tell the difference.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .cycles import CycleClosure, CyclePolicy, CycleTracker
from .estimator import LineFit, TemperatureAwareFit, fit_with_temperature, residual_rmse, two_point_line
from .samples import (
    Admission,
    AdmissionPolicy,
    Observation,
    ProbeLiveness,
    RejectionReason,
    Sample,
    SampleBuffer,
    evaluate,
    probe_liveness,
)
from .soil import SoilProfile

#: Reference temperature used when no sample carries one [degrees Celsius].
FALLBACK_REFERENCE_TEMPERATURE_C: float = 20.0


class CalibrationStatus(StrEnum):
    """What the calibration currently is, from the point of view of a consumer."""

    COLLECTING = "collecting"
    CALIBRATED = "calibrated"
    DRIFTING = "drifting"
    PROBE_OFFLINE = "probe_offline"
    INVALIDATED = "invalidated"


class InvalidationReason(StrEnum):
    """Why a previously valid calibration was dropped."""

    SOIL_CHANGED = "soil_changed"
    SOURCE_CHANGED = "source_changed"
    DEVICE_CALIBRATION_CHANGED = "device_calibration_changed"
    MANUAL_RESET = "manual_reset"


@dataclass(frozen=True, slots=True)
class QualityGates:
    """The conditions a fit must meet before it may be published.

    Every threshold answers one failure mode seen with cheap probes:

    * ``min_cycles`` stops a calibration built on a single wetting event, which
      is the most common way to get a confident and wrong line. Five by default.
    * ``min_samples`` and ``min_raw_span`` stop a line placed through a cluster:
      a probe that only ever moved three points has not been asked a question.
    * ``min_r_squared`` catches the probe that is simply not monotone in this
      soil, usually because it sits in a gravel pocket or above the root zone.
    * ``max_rmse_fraction`` bounds the residual as a share of the available water
      span, which is the only scale on which a moisture error is meaningful.
    """

    min_cycles: int = 5
    min_samples: int = 40
    min_raw_span: float = 8.0
    min_r_squared: float = 0.6
    max_rmse_fraction: float = 0.25
    drift_rmse_multiplier: float = 2.0
    drift_window: int = 24
    extrapolation_margin: float = 10.0

    def to_dict(self) -> dict[str, float | int]:
        """Serialize for the config entry options."""
        return {
            "min_cycles": self.min_cycles,
            "min_samples": self.min_samples,
            "min_raw_span": self.min_raw_span,
            "min_r_squared": self.min_r_squared,
            "max_rmse_fraction": self.max_rmse_fraction,
            "drift_rmse_multiplier": self.drift_rmse_multiplier,
            "drift_window": self.drift_window,
            "extrapolation_margin": self.extrapolation_margin,
        }

    @classmethod
    def from_dict(cls, data: dict) -> QualityGates:
        """Rebuild gates from options, falling back to the default of each field."""
        blank = cls()
        return cls(
            min_cycles=int(data.get("min_cycles", blank.min_cycles)),
            min_samples=int(data.get("min_samples", blank.min_samples)),
            min_raw_span=float(data.get("min_raw_span", blank.min_raw_span)),
            min_r_squared=float(data.get("min_r_squared", blank.min_r_squared)),
            max_rmse_fraction=float(data.get("max_rmse_fraction", blank.max_rmse_fraction)),
            drift_rmse_multiplier=float(data.get("drift_rmse_multiplier", blank.drift_rmse_multiplier)),
            drift_window=int(data.get("drift_window", blank.drift_window)),
            extrapolation_margin=float(data.get("extrapolation_margin", blank.extrapolation_margin)),
        )

    def max_rmse(self, soil: SoilProfile) -> float:
        """Largest residual a fit may have [m3/m3], as a share of the available span."""
        return self.max_rmse_fraction * (soil.field_capacity - soil.wilting_point)


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """Whether the gates passed, which ones did not, and how far along each is.

    ``progress`` is what the user actually watches while a new probe calibrates:
    three cycles out of five, thirty-one samples out of forty. It is published by
    the diagnostic entity so that waiting is informed rather than blind.
    """

    passed: bool
    failures: tuple[str, ...]
    progress: dict[str, float]


@dataclass(frozen=True, slots=True)
class CalibrationFit:
    """A published calibration: the line, its provenance and its quality.

    ``soil_fingerprint`` and ``raw_min`` / ``raw_max`` are part of the fit and
    not of its metadata: the first says which reservoir the line was fitted
    against, the second pair says over which part of the probe range the line was
    ever tested. Both are needed to answer the only question that matters at read
    time, which is whether this line may be applied to *this* reading.
    """

    slope: float
    intercept: float
    temperature_coefficient: float
    reference_temperature_c: float
    r_squared: float
    rmse: float
    sample_count: int
    cycle_count: int
    raw_min: float
    raw_max: float
    deficit_span_mm: float
    soil_fingerprint: str
    fitted_at: datetime
    source: str = "regression"

    @property
    def raw_span(self) -> float:
        """Probe range the fit was built on [percent points]."""
        return self.raw_max - self.raw_min

    def estimate(self, raw_percent: float, temperature_c: float | None = None) -> float:
        """Water content implied by a raw reading [m3/m3], before any clamping."""
        value = self.slope * raw_percent + self.intercept
        if temperature_c is not None and self.temperature_coefficient:
            value += self.temperature_coefficient * (temperature_c - self.reference_temperature_c)
        return value

    def is_extrapolated(self, raw_percent: float, margin: float) -> bool:
        """Whether a reading falls outside the range the line was fitted on, plus a margin."""
        return raw_percent < self.raw_min - margin or raw_percent > self.raw_max + margin

    def to_dict(self) -> dict:
        """Serialize for the sample store."""
        return {
            "slope": self.slope,
            "intercept": self.intercept,
            "temperature_coefficient": self.temperature_coefficient,
            "reference_temperature_c": self.reference_temperature_c,
            "r_squared": self.r_squared,
            "rmse": self.rmse,
            "sample_count": self.sample_count,
            "cycle_count": self.cycle_count,
            "raw_min": self.raw_min,
            "raw_max": self.raw_max,
            "deficit_span_mm": self.deficit_span_mm,
            "soil_fingerprint": self.soil_fingerprint,
            "fitted_at": self.fitted_at.isoformat(),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CalibrationFit:
        """Rebuild a fit from the store."""
        return cls(
            slope=float(data["slope"]),
            intercept=float(data["intercept"]),
            temperature_coefficient=float(data.get("temperature_coefficient", 0.0)),
            reference_temperature_c=float(data.get("reference_temperature_c", FALLBACK_REFERENCE_TEMPERATURE_C)),
            r_squared=float(data.get("r_squared", 0.0)),
            rmse=float(data.get("rmse", 0.0)),
            sample_count=int(data.get("sample_count", 0)),
            cycle_count=int(data.get("cycle_count", 0)),
            raw_min=float(data.get("raw_min", 0.0)),
            raw_max=float(data.get("raw_max", 100.0)),
            deficit_span_mm=float(data.get("deficit_span_mm", 0.0)),
            soil_fingerprint=str(data.get("soil_fingerprint", "")),
            fitted_at=datetime.fromisoformat(data["fitted_at"]),
            source=str(data.get("source", "regression")),
        )


@dataclass(frozen=True, slots=True)
class CalibratedReading:
    """What a consumer gets back when asking the calibration about a raw value."""

    moisture: float
    available_fraction: float
    deficit_mm: float
    extrapolated: bool
    temperature_corrected: bool


@dataclass(slots=True)
class CalibrationSession:
    """Aggregate root: one probe, one deficit source, one soil, one calibration.

    Invariants it keeps, and which no other object may break:

    1. Every sample in the buffer was derived through the *current* soil profile;
       a soil change re-derives the history or the history is dropped.
    2. Only samples belonging to complete cycles reach the estimator.
    3. A fit is stored only when the gates passed and the slope is positive: a
       probe whose index falls as the soil wets is wired or sited wrongly, and
       inverting it silently would hide a real installation fault.
    4. ``status`` is derived, never assigned from outside.
    """

    soil: SoilProfile
    admission_policy: AdmissionPolicy = field(default_factory=AdmissionPolicy)
    cycle_policy: CyclePolicy = field(default_factory=CyclePolicy)
    gates: QualityGates = field(default_factory=QualityGates)
    buffer: SampleBuffer = field(default_factory=SampleBuffer)
    tracker: CycleTracker | None = None
    fit: CalibrationFit | None = None
    provisional_fit: CalibrationFit | None = None
    status: CalibrationStatus = CalibrationStatus.COLLECTING
    last_rejection: RejectionReason | None = None
    rejection_counts: dict[str, int] = field(default_factory=dict)
    last_sample_at: datetime | None = None
    last_fit_at: datetime | None = None
    invalidation_reason: InvalidationReason | None = None
    drift_rmse: float | None = None

    def __post_init__(self) -> None:
        """Attach a tracker sized on the current reservoir when none was supplied."""
        if self.tracker is None:
            self.tracker = CycleTracker(
                policy=self.cycle_policy,
                total_available_water_mm=self.soil.total_available_water_mm,
            )

    # ── Ingestion ────────────────────────────────────────────────

    def observe(self, observation: Observation) -> Admission:
        """Offer an observation to the calibration and report what became of it.

        Three things happen in order, and the order is the contract: admission
        rules decide whether the pair is usable at all, the cycle tracker decides
        which cycle it belongs to, and only then is it buffered. A sample that no
        cycle claims is discarded rather than filed under a placeholder, because
        a sample with no wet anchor behind it cannot be placed on the moisture
        axis at all.
        """
        assert self.tracker is not None
        seconds_since_last = None
        if self.last_sample_at is not None:
            seconds_since_last = (observation.taken_at - self.last_sample_at).total_seconds()

        admission = evaluate(
            observation=observation,
            policy=self.admission_policy,
            soil=self.soil,
            cycle_index=self.tracker.current_index,
            seconds_since_last_sample=seconds_since_last,
        )
        if not admission.accepted or admission.sample is None:
            self._record_rejection(admission.reason)
            if probe_liveness(observation, self.admission_policy) is ProbeLiveness.STALE:
                self.status = CalibrationStatus.PROBE_OFFLINE
            return admission

        cycle = self.tracker.observe(admission.sample)
        if cycle is None:
            self._record_rejection(RejectionReason.AWAITING_WET_ANCHOR)
            return Admission.refuse(RejectionReason.AWAITING_WET_ANCHOR)

        stamped = Sample(
            taken_at=admission.sample.taken_at,
            raw_percent=admission.sample.raw_percent,
            deficit_mm=admission.sample.deficit_mm,
            reference_moisture=admission.sample.reference_moisture,
            soil_fingerprint=admission.sample.soil_fingerprint,
            cycle_index=cycle.index,
            probe_temperature_c=admission.sample.probe_temperature_c,
            ambient_temperature_c=admission.sample.ambient_temperature_c,
        )
        self.buffer.add(stamped)
        self.last_sample_at = stamped.taken_at
        self.last_rejection = None
        if self.status is CalibrationStatus.PROBE_OFFLINE:
            self.status = CalibrationStatus.CALIBRATED if self.fit else CalibrationStatus.COLLECTING
        return Admission.accept(stamped)

    def note_irrigation(self, at: datetime) -> None:
        """Tell the tracker that water reached the soil, from whatever witness saw it."""
        assert self.tracker is not None
        self.tracker.note_irrigation(at)

    def deficit_drop_is_irrigation(self, previous_deficit_mm: float, current_deficit_mm: float) -> bool:
        """Whether a fall in the deficit is large enough to be read as water delivered."""
        assert self.tracker is not None
        return self.tracker.detects_irrigation(previous_deficit_mm, current_deficit_mm)

    def _record_rejection(self, reason: RejectionReason | None) -> None:
        """Count a rejection and remember the last one for the diagnostic entity."""
        if reason is None:
            return
        self.last_rejection = reason
        self.rejection_counts[str(reason)] = self.rejection_counts.get(str(reason), 0) + 1

    # ── Fitting ──────────────────────────────────────────────────

    def _fit_samples(self) -> list[Sample]:
        """Samples the estimator is allowed to see: those inside complete cycles."""
        assert self.tracker is not None
        return self.buffer.for_cycles(self.tracker.complete_cycle_indices())

    def _reference_temperature(self, samples: list[Sample]) -> float:
        """Centre of the temperature term: the median temperature actually observed."""
        temperatures = [s.probe_temperature_c for s in samples if s.probe_temperature_c is not None]
        if not temperatures:
            temperatures = [s.ambient_temperature_c for s in samples if s.ambient_temperature_c is not None]
        if not temperatures:
            return FALLBACK_REFERENCE_TEMPERATURE_C
        return statistics.median(temperatures)

    def evaluate_gates(self, candidate: TemperatureAwareFit | None, samples: list[Sample]) -> GateVerdict:
        """Check a candidate fit against every gate, reporting all failures at once.

        Reporting every failure rather than the first is deliberate: a user who
        fixes the sample count only to be told next week that the span is too
        narrow has been made to wait twice for one diagnosis.
        """
        assert self.tracker is not None
        cycle_count = len(self.tracker.complete_cycles())
        raw_values = [sample.raw_percent for sample in samples]
        raw_span = (max(raw_values) - min(raw_values)) if raw_values else 0.0

        failures: list[str] = []
        if cycle_count < self.gates.min_cycles:
            failures.append("cycles")
        if len(samples) < self.gates.min_samples:
            failures.append("samples")
        if raw_span < self.gates.min_raw_span:
            failures.append("raw_span")
        if candidate is None:
            failures.append("no_fit")
        else:
            if candidate.line.slope <= 0.0:
                failures.append("slope_sign")
            if candidate.line.r_squared < self.gates.min_r_squared:
                failures.append("r_squared")
            if candidate.line.rmse > self.gates.max_rmse(self.soil):
                failures.append("residual")

        progress = {
            "cycles": float(cycle_count),
            "cycles_required": float(self.gates.min_cycles),
            "samples": float(len(samples)),
            "samples_required": float(self.gates.min_samples),
            "raw_span": round(raw_span, 2),
            "raw_span_required": float(self.gates.min_raw_span),
            "r_squared": round(candidate.line.r_squared, 4) if candidate else 0.0,
            "rmse": round(candidate.line.rmse, 5) if candidate else 0.0,
        }
        return GateVerdict(passed=not failures, failures=tuple(failures), progress=progress)

    def refit(self, now: datetime) -> GateVerdict:
        """Recompute the calibration from the complete cycles and publish it if it passes.

        Always returns a verdict, whether or not a fit was published: the verdict
        is the message to the user. On failure the previously published fit is
        kept rather than dropped, because yesterday's earned calibration is
        better evidence than today's failed attempt.
        """
        samples = self._fit_samples()
        self.provisional_fit = self._anchor_fit(now)

        if len(samples) < 2:
            verdict = self.evaluate_gates(None, samples)
            self._settle_status(verdict)
            return verdict

        reference_temperature = self._reference_temperature(samples)
        candidate = fit_with_temperature(
            [(sample.raw_percent, sample.reference_moisture, sample.probe_temperature_c) for sample in samples],
            reference_temperature_c=reference_temperature,
        )
        verdict = self.evaluate_gates(candidate, samples)
        if verdict.passed and candidate is not None:
            self.fit = self._build_fit(candidate, samples, now, source="regression")
            self.last_fit_at = now
            self.drift_rmse = None
        self._settle_status(verdict)
        return verdict

    def _build_fit(
        self,
        candidate: TemperatureAwareFit,
        samples: list[Sample],
        now: datetime,
        source: str,
    ) -> CalibrationFit:
        """Package an accepted estimate together with the evidence behind it."""
        assert self.tracker is not None
        raw_values = [sample.raw_percent for sample in samples]
        deficits = [sample.deficit_mm for sample in samples]
        return CalibrationFit(
            slope=candidate.line.slope,
            intercept=candidate.line.intercept,
            temperature_coefficient=candidate.temperature_coefficient,
            reference_temperature_c=candidate.reference_temperature_c,
            r_squared=candidate.line.r_squared,
            rmse=candidate.line.rmse,
            sample_count=len(samples),
            cycle_count=len(self.tracker.complete_cycles()),
            raw_min=min(raw_values),
            raw_max=max(raw_values),
            deficit_span_mm=max(deficits) - min(deficits),
            soil_fingerprint=self.soil.fingerprint(),
            fitted_at=now,
            source=source,
        )

    def _anchor_fit(self, now: datetime) -> CalibrationFit | None:
        """The line through the averaged wet and dry anchors, used while gates are unmet.

        This is the physics-only estimate: field capacity after drainage at one
        end, the driest observed state at the other. It is never published as the
        calibrated value, only as a provisional attribute, because two points
        cannot tell a good probe from a stuck one.
        """
        assert self.tracker is not None
        cycles = [cycle for cycle in self.tracker.cycles if cycle.wet_anchor and cycle.dry_anchor]
        if not cycles:
            return None
        wet_raw = statistics.median([cycle.wet_anchor.raw_percent for cycle in cycles])
        wet_moisture = statistics.median([cycle.wet_anchor.reference_moisture for cycle in cycles])
        dry_raw = statistics.median([cycle.dry_anchor.raw_percent for cycle in cycles])
        dry_moisture = statistics.median([cycle.dry_anchor.reference_moisture for cycle in cycles])
        line = two_point_line((wet_raw, wet_moisture), (dry_raw, dry_moisture))
        if line is None or line.slope <= 0.0:
            return None
        candidate = TemperatureAwareFit(
            line=line,
            temperature_coefficient=0.0,
            reference_temperature_c=FALLBACK_REFERENCE_TEMPERATURE_C,
        )
        anchors = [sample for cycle in cycles for sample in (cycle.wet_anchor, cycle.dry_anchor) if sample is not None]
        return self._build_fit(candidate, anchors, now, source="anchors")

    def _settle_status(self, verdict: GateVerdict) -> None:
        """Derive the published status from the fit, the verdict and the drift."""
        if self.status is CalibrationStatus.PROBE_OFFLINE:
            return
        if self.fit is None:
            self.status = CalibrationStatus.COLLECTING
            return
        if self.drift_rmse is not None and self.drift_rmse > self._drift_threshold():
            self.status = CalibrationStatus.DRIFTING
            return
        self.status = CalibrationStatus.CALIBRATED if verdict.passed or self.fit else CalibrationStatus.COLLECTING

    def _drift_threshold(self) -> float:
        """Residual above which the published fit is treated as drifting [m3/m3]."""
        return self.gates.max_rmse(self.soil) * self.gates.drift_rmse_multiplier

    def update_drift(self) -> float | None:
        """Measure the recent residual of the published fit and flag drift.

        Probes age: the electrode corrodes, roots grow past the sensing volume,
        the soil settles. The calibration does not become wrong all at once, it
        starts missing in one direction, and the cheapest witness is the residual
        of the most recent samples against the line that is currently published.
        """
        if self.fit is None:
            self.drift_rmse = None
            return None
        recent = self.buffer.tail(self.gates.drift_window)
        if len(recent) < 4:
            return self.drift_rmse
        reference = self.fit.reference_temperature_c
        points = [
            (
                sample.raw_percent,
                sample.reference_moisture
                - self.fit.temperature_coefficient * ((sample.probe_temperature_c or reference) - reference),
            )
            for sample in recent
        ]
        self.drift_rmse = residual_rmse(points, self.fit.slope, self.fit.intercept)
        if self.drift_rmse > self._drift_threshold():
            self.status = CalibrationStatus.DRIFTING
        elif self.status is CalibrationStatus.DRIFTING:
            self.status = CalibrationStatus.CALIBRATED
        return self.drift_rmse

    # ── Reading ──────────────────────────────────────────────────

    def calibrated_reading(
        self,
        raw_percent: float,
        temperature_c: float | None = None,
    ) -> CalibratedReading | None:
        """Translate a raw probe index into soil water, or ``None`` if not calibrated.

        Returning ``None`` rather than a best guess is the load-bearing decision
        of this integration: an uncalibrated probe has nothing to say about the
        soil, and saying it anyway is what the cheap probe already does.
        """
        if self.fit is None or self.status is CalibrationStatus.INVALIDATED:
            return None
        estimated = self.fit.estimate(raw_percent, temperature_c)
        clamped = min(self.soil.saturation, max(0.0, estimated))
        return CalibratedReading(
            moisture=clamped,
            available_fraction=self.soil.available_fraction_at_moisture(clamped),
            deficit_mm=self.soil.deficit_at_moisture(clamped),
            extrapolated=self.fit.is_extrapolated(raw_percent, self.gates.extrapolation_margin),
            temperature_corrected=temperature_c is not None and bool(self.fit.temperature_coefficient),
        )

    # ── Lifecycle ────────────────────────────────────────────────

    def apply_soil(self, soil: SoilProfile, now: datetime) -> None:
        """Adopt a corrected reservoir: re-derive the history, drop the fit.

        The raw readings and the deficits are measurements and survive; the
        reference moistures were an interpretation and are recomputed. The fit
        was a statement about the old reservoir and cannot survive, so it is
        invalidated and has to be earned again on the same data.
        """
        assert self.tracker is not None
        if soil.fingerprint() == self.soil.fingerprint():
            return
        self.soil = soil
        self.buffer.rederive(soil)
        self.tracker.total_available_water_mm = soil.total_available_water_mm
        for cycle in self.tracker.cycles:
            if cycle.wet_anchor:
                cycle.wet_anchor = cycle.wet_anchor.with_soil(soil)
            if cycle.dry_anchor:
                cycle.dry_anchor = cycle.dry_anchor.with_soil(soil)
        self.invalidate(InvalidationReason.SOIL_CHANGED, now)

    def invalidate(self, reason: InvalidationReason, now: datetime) -> None:
        """Drop the published fit, keeping the evidence when it is still valid.

        A device-side calibration change or a swapped source entity breaks the
        relationship between index and soil, so the *samples* die with the fit.
        A soil correction only changes the interpretation, so the samples stay.
        """
        assert self.tracker is not None
        self.fit = None
        self.drift_rmse = None
        self.invalidation_reason = reason
        self.status = CalibrationStatus.INVALIDATED
        if reason in (InvalidationReason.SOURCE_CHANGED, InvalidationReason.DEVICE_CALIBRATION_CHANGED):
            self.buffer.clear()
            self.tracker.reset()
            self.last_sample_at = None
        elif reason is InvalidationReason.SOIL_CHANGED:
            self.tracker.close_all(now, CycleClosure.SOIL_CHANGED)

    def reset(self, now: datetime) -> None:
        """Forget everything: samples, cycles and fit. The probe starts over."""
        assert self.tracker is not None
        self.buffer.clear()
        self.tracker.reset()
        self.fit = None
        self.provisional_fit = None
        self.drift_rmse = None
        self.last_sample_at = None
        self.last_rejection = None
        self.rejection_counts = {}
        self.invalidation_reason = InvalidationReason.MANUAL_RESET
        self.status = CalibrationStatus.COLLECTING
        self.last_fit_at = None

    def mark_field_capacity(self, observation: Observation, now: datetime) -> bool:
        """Declare by hand that the soil is at field capacity right now.

        The escape hatch for a site whose deficit source is unreliable or brand
        new: the user waters, waits for drainage and says so. It is recorded as a
        wet anchor exactly like an automatic one, which keeps a single notion of
        anchor in the model.
        """
        assert self.tracker is not None
        if observation.raw_percent is None:
            return False
        anchor = Sample(
            taken_at=now,
            raw_percent=observation.raw_percent,
            deficit_mm=0.0,
            reference_moisture=self.soil.field_capacity,
            soil_fingerprint=self.soil.fingerprint(),
            cycle_index=0,
            probe_temperature_c=observation.probe_temperature_c,
            ambient_temperature_c=observation.ambient_temperature_c,
        )
        self.tracker.note_irrigation(now)
        cycle = self.tracker.observe(anchor)
        if cycle is None:
            return False
        self.buffer.add(
            Sample(
                taken_at=anchor.taken_at,
                raw_percent=anchor.raw_percent,
                deficit_mm=anchor.deficit_mm,
                reference_moisture=anchor.reference_moisture,
                soil_fingerprint=anchor.soil_fingerprint,
                cycle_index=cycle.index,
                probe_temperature_c=anchor.probe_temperature_c,
                ambient_temperature_c=anchor.ambient_temperature_c,
            )
        )
        self.last_sample_at = now
        return True

    # ── Persistence ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize the whole session for the sample store."""
        assert self.tracker is not None
        return {
            "soil": self.soil.to_dict(),
            "admission_policy": self.admission_policy.to_dict(),
            "cycle_policy": self.cycle_policy.to_dict(),
            "gates": self.gates.to_dict(),
            "samples": self.buffer.to_list(),
            "tracker": self.tracker.to_dict(),
            "fit": self.fit.to_dict() if self.fit else None,
            "status": str(self.status),
            "last_rejection": str(self.last_rejection) if self.last_rejection else None,
            "rejection_counts": dict(self.rejection_counts),
            "last_sample_at": self.last_sample_at.isoformat() if self.last_sample_at else None,
            "last_fit_at": self.last_fit_at.isoformat() if self.last_fit_at else None,
            "invalidation_reason": str(self.invalidation_reason) if self.invalidation_reason else None,
            "drift_rmse": self.drift_rmse,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CalibrationSession:
        """Rebuild a session from the store, tolerating a partially unreadable payload.

        A corrupted or outdated store must cost the user their history, never
        their integration: anything that fails to parse is dropped and the
        session restarts collecting.
        """
        soil = SoilProfile.from_dict(data.get("soil", {}))
        admission_policy = AdmissionPolicy.from_dict(data.get("admission_policy", {}))
        cycle_policy = CyclePolicy.from_dict(data.get("cycle_policy", {}))
        gates = QualityGates.from_dict(data.get("gates", {}))
        buffer = SampleBuffer.from_list(data.get("samples", []))
        tracker = CycleTracker.from_dict(data.get("tracker", {}), cycle_policy, soil.total_available_water_mm)

        fit_data = data.get("fit")
        fit: CalibrationFit | None = None
        if fit_data:
            try:
                fit = CalibrationFit.from_dict(fit_data)
            except (KeyError, TypeError, ValueError):
                fit = None
        if fit is not None and fit.soil_fingerprint != soil.fingerprint():
            fit = None

        session = cls(
            soil=soil,
            admission_policy=admission_policy,
            cycle_policy=cycle_policy,
            gates=gates,
            buffer=buffer,
            tracker=tracker,
            fit=fit,
        )
        session.status = CalibrationStatus(data.get("status", CalibrationStatus.COLLECTING))
        if fit is None and session.status is CalibrationStatus.CALIBRATED:
            session.status = CalibrationStatus.COLLECTING
        last_rejection = data.get("last_rejection")
        session.last_rejection = RejectionReason(last_rejection) if last_rejection else None
        session.rejection_counts = dict(data.get("rejection_counts", {}))
        last_sample_at = data.get("last_sample_at")
        session.last_sample_at = datetime.fromisoformat(last_sample_at) if last_sample_at else None
        last_fit_at = data.get("last_fit_at")
        session.last_fit_at = datetime.fromisoformat(last_fit_at) if last_fit_at else None
        invalidation = data.get("invalidation_reason")
        session.invalidation_reason = InvalidationReason(invalidation) if invalidation else None
        drift = data.get("drift_rmse")
        session.drift_rmse = float(drift) if drift is not None else None
        return session


def line_summary(fit: CalibrationFit) -> str:
    """One-line human form of a calibration, for logs and diagnostics."""
    return (
        f"theta = {fit.slope:.5f} * raw + {fit.intercept:.5f}"
        f" (+{fit.temperature_coefficient:.5f} per degC)"
        f" R2={fit.r_squared:.3f} rmse={fit.rmse:.4f}"
        f" n={fit.sample_count} cycles={fit.cycle_count}"
    )


__all__ = [
    "CalibratedReading",
    "CalibrationFit",
    "CalibrationSession",
    "CalibrationStatus",
    "GateVerdict",
    "InvalidationReason",
    "LineFit",
    "QualityGates",
    "line_summary",
]
