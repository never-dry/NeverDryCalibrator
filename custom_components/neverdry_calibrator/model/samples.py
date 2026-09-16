"""What counts as a usable observation, and the buffer that keeps the usable ones.

Calibrating a cheap probe is not a regression problem, it is an *admission*
problem. The arithmetic at the end is three lines; everything that decides
whether the calibration is worth anything happens here, when a pair
(raw reading, reference deficit) is either accepted as a :class:`Sample` or
rejected with a named reason.

The rules are stated as a policy object rather than scattered ``if`` clauses so
that the whole admission contract can be read in one place, tested without a
Home Assistant runtime, and tuned by the user through the options flow.

Rejection is never silent: every refused observation carries a
:class:`RejectionReason`, the integration counts them, and the diagnostic
entity publishes the last one. A probe that never calibrates must be able to
say why.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .soil import SoilProfile


class RejectionReason(StrEnum):
    """Why an observation did not become a sample."""

    PROBE_UNAVAILABLE = "probe_unavailable"
    PROBE_STALE = "probe_stale"
    RAW_OUT_OF_RANGE = "raw_out_of_range"
    DEFICIT_UNAVAILABLE = "deficit_unavailable"
    DEFICIT_STALE = "deficit_stale"
    IRRIGATION_ACTIVE = "irrigation_active"
    DRAINAGE_WINDOW = "drainage_window"
    FROZEN_SOIL = "frozen_soil"
    SAMPLED_TOO_SOON = "sampled_too_soon"
    MISSING_PROBE_TEMPERATURE = "missing_probe_temperature"
    AWAITING_WET_ANCHOR = "awaiting_wet_anchor"


class ProbeLiveness(StrEnum):
    """What the temperature sentinel says about the probe being alive.

    A cheap probe with a flat battery does not disappear from Home Assistant, it
    keeps reporting its last moisture value forever. The temperature channel of
    the same device is the cheapest sentinel available: it moves every day by
    several degrees, so a temperature that has not changed or not arrived within
    the timeout means the device stopped talking, whatever the moisture entity
    still shows.
    """

    ALIVE = "alive"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Observation:
    """One snapshot of every input, as read from Home Assistant at a given instant.

    This is the boundary type: the Home Assistant layer builds it, the domain
    decides what to do with it. Ages are passed in seconds rather than as
    timestamps because the caller already knows how old each state is, and the
    domain must not need a clock of its own.
    """

    taken_at: datetime
    raw_percent: float | None
    deficit_mm: float | None
    deficit_age_s: float | None = None
    probe_temperature_c: float | None = None
    probe_temperature_age_s: float | None = None
    ambient_temperature_c: float | None = None
    irrigation_active: bool = False
    seconds_since_irrigation: float | None = None

    @property
    def soil_temperature_c(self) -> float | None:
        """Best available soil temperature: the probe's own, else the ambient one.

        Ambient air is a poor proxy for soil at depth, but for the single use it
        has here, refusing samples while the ground is frozen, it is far better
        than no guard at all on a probe that reports no temperature.
        """
        if self.probe_temperature_c is not None:
            return self.probe_temperature_c
        return self.ambient_temperature_c


@dataclass(frozen=True, slots=True)
class AdmissionPolicy:
    """The thresholds that decide whether an observation may become a sample.

    Defaults are chosen for a garden bed with a daily irrigation cycle: sample at
    most every ten minutes (a probe reports far more often than the soil changes),
    trust a deficit for half an hour, declare a probe dead after two hours of
    silence, wait three hours after irrigation for the wetting front to
    redistribute, and refuse anything below two degrees because the permittivity
    of frozen water is nothing like that of liquid water.
    """

    min_sample_interval_s: float = 600.0
    max_deficit_age_s: float = 1800.0
    probe_timeout_s: float = 7200.0
    drainage_minutes: float = 180.0
    min_soil_temperature_c: float = 2.0
    require_probe_temperature: bool = False

    @property
    def drainage_seconds(self) -> float:
        """Drainage window expressed in seconds, the unit the observations carry."""
        return self.drainage_minutes * 60.0

    def to_dict(self) -> dict[str, float | bool]:
        """Serialize for the config entry options."""
        return {
            "min_sample_interval_s": self.min_sample_interval_s,
            "max_deficit_age_s": self.max_deficit_age_s,
            "probe_timeout_s": self.probe_timeout_s,
            "drainage_minutes": self.drainage_minutes,
            "min_soil_temperature_c": self.min_soil_temperature_c,
            "require_probe_temperature": self.require_probe_temperature,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AdmissionPolicy:
        """Rebuild a policy from options, falling back to the default of each field."""
        blank = cls()
        return cls(
            min_sample_interval_s=float(data.get("min_sample_interval_s", blank.min_sample_interval_s)),
            max_deficit_age_s=float(data.get("max_deficit_age_s", blank.max_deficit_age_s)),
            probe_timeout_s=float(data.get("probe_timeout_s", blank.probe_timeout_s)),
            drainage_minutes=float(data.get("drainage_minutes", blank.drainage_minutes)),
            min_soil_temperature_c=float(data.get("min_soil_temperature_c", blank.min_soil_temperature_c)),
            require_probe_temperature=bool(data.get("require_probe_temperature", blank.require_probe_temperature)),
        )


@dataclass(frozen=True, slots=True)
class Sample:
    """An admitted pair: what the probe said, and what the soil actually held.

    ``reference_moisture`` is always derived from ``deficit_mm`` through a
    :class:`SoilProfile`, and ``soil_fingerprint`` records which one. Keeping the
    deficit alongside the derived value is what allows a soil correction to
    re-derive the history instead of throwing it away (see :meth:`with_soil`).
    """

    taken_at: datetime
    raw_percent: float
    deficit_mm: float
    reference_moisture: float
    soil_fingerprint: str
    cycle_index: int = 0
    probe_temperature_c: float | None = None
    ambient_temperature_c: float | None = None

    def with_soil(self, soil: SoilProfile) -> Sample:
        """Re-derive the reference moisture against another reservoir.

        Used when the user corrects the soil or the root depth: the raw reading
        and the deficit are still facts, only their interpretation changed.
        """
        return Sample(
            taken_at=self.taken_at,
            raw_percent=self.raw_percent,
            deficit_mm=self.deficit_mm,
            reference_moisture=soil.moisture_at_deficit(self.deficit_mm),
            soil_fingerprint=soil.fingerprint(),
            cycle_index=self.cycle_index,
            probe_temperature_c=self.probe_temperature_c,
            ambient_temperature_c=self.ambient_temperature_c,
        )

    def to_dict(self) -> dict:
        """Serialize for the sample store."""
        return {
            "taken_at": self.taken_at.isoformat(),
            "raw_percent": self.raw_percent,
            "deficit_mm": self.deficit_mm,
            "reference_moisture": self.reference_moisture,
            "soil_fingerprint": self.soil_fingerprint,
            "cycle_index": self.cycle_index,
            "probe_temperature_c": self.probe_temperature_c,
            "ambient_temperature_c": self.ambient_temperature_c,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Sample:
        """Rebuild a sample from the store."""
        return cls(
            taken_at=datetime.fromisoformat(data["taken_at"]),
            raw_percent=float(data["raw_percent"]),
            deficit_mm=float(data["deficit_mm"]),
            reference_moisture=float(data["reference_moisture"]),
            soil_fingerprint=str(data.get("soil_fingerprint", "")),
            cycle_index=int(data.get("cycle_index", 0)),
            probe_temperature_c=_optional_float(data.get("probe_temperature_c")),
            ambient_temperature_c=_optional_float(data.get("ambient_temperature_c")),
        )


def _optional_float(value: object) -> float | None:
    """Coerce a stored value to float, keeping ``None`` as ``None``."""
    if value is None:
        return None
    return float(value)


@dataclass(frozen=True, slots=True)
class Admission:
    """The verdict on one observation: the sample it produced, or why it did not."""

    accepted: bool
    sample: Sample | None = None
    reason: RejectionReason | None = None

    @classmethod
    def refuse(cls, reason: RejectionReason) -> Admission:
        """Build a rejection carrying its named reason."""
        return cls(accepted=False, sample=None, reason=reason)

    @classmethod
    def accept(cls, sample: Sample) -> Admission:
        """Build an acceptance carrying the produced sample."""
        return cls(accepted=True, sample=sample, reason=None)


def probe_liveness(observation: Observation, policy: AdmissionPolicy) -> ProbeLiveness:
    """Read the temperature sentinel and say whether the probe is still reporting.

    ``UNKNOWN`` is returned when the device exposes no temperature channel at
    all. That is not a failure, it is a missing sentinel, and the difference
    matters: an unknown liveness must not make the probe look dead.
    """
    if observation.probe_temperature_age_s is None:
        return ProbeLiveness.UNKNOWN
    if observation.probe_temperature_age_s > policy.probe_timeout_s:
        return ProbeLiveness.STALE
    return ProbeLiveness.ALIVE


def evaluate(
    observation: Observation,
    policy: AdmissionPolicy,
    soil: SoilProfile,
    cycle_index: int,
    seconds_since_last_sample: float | None,
) -> Admission:
    """Decide whether an observation becomes a sample, in the order that matters.

    The order of the checks is the order of the causes: availability before
    freshness, freshness before physics, physics before rate limiting. It is
    written this way so that the reported reason is the *first* thing wrong, not
    whichever check happened to run last.
    """
    if observation.raw_percent is None:
        return Admission.refuse(RejectionReason.PROBE_UNAVAILABLE)
    if not 0.0 <= observation.raw_percent <= 100.0:
        return Admission.refuse(RejectionReason.RAW_OUT_OF_RANGE)
    if observation.deficit_mm is None:
        return Admission.refuse(RejectionReason.DEFICIT_UNAVAILABLE)
    if observation.deficit_age_s is not None and observation.deficit_age_s > policy.max_deficit_age_s:
        return Admission.refuse(RejectionReason.DEFICIT_STALE)

    liveness = probe_liveness(observation, policy)
    if liveness is ProbeLiveness.STALE:
        return Admission.refuse(RejectionReason.PROBE_STALE)
    if liveness is ProbeLiveness.UNKNOWN and policy.require_probe_temperature:
        return Admission.refuse(RejectionReason.MISSING_PROBE_TEMPERATURE)

    if observation.irrigation_active:
        return Admission.refuse(RejectionReason.IRRIGATION_ACTIVE)
    if (
        observation.seconds_since_irrigation is not None
        and observation.seconds_since_irrigation < policy.drainage_seconds
    ):
        return Admission.refuse(RejectionReason.DRAINAGE_WINDOW)

    soil_temperature = observation.soil_temperature_c
    if soil_temperature is not None and soil_temperature < policy.min_soil_temperature_c:
        return Admission.refuse(RejectionReason.FROZEN_SOIL)

    if seconds_since_last_sample is not None and seconds_since_last_sample < policy.min_sample_interval_s:
        return Admission.refuse(RejectionReason.SAMPLED_TOO_SOON)

    return Admission.accept(
        Sample(
            taken_at=observation.taken_at,
            raw_percent=observation.raw_percent,
            deficit_mm=observation.deficit_mm,
            reference_moisture=soil.moisture_at_deficit(observation.deficit_mm),
            soil_fingerprint=soil.fingerprint(),
            cycle_index=cycle_index,
            probe_temperature_c=observation.probe_temperature_c,
            ambient_temperature_c=observation.ambient_temperature_c,
        )
    )


class SampleBuffer:
    """Bounded, time-ordered store of admitted samples.

    Bounded because a probe sampled every ten minutes for a season is fifty
    thousand points and the fit gains nothing after the first few thousand;
    time-ordered because the cycle logic and the drift window both read the tail.
    """

    def __init__(self, capacity: int = 5000, samples: list[Sample] | None = None) -> None:
        """Create a buffer holding at most ``capacity`` samples, oldest dropped first."""
        self._samples: deque[Sample] = deque(samples or [], maxlen=capacity)

    def __len__(self) -> int:
        """Number of samples currently held."""
        return len(self._samples)

    def __iter__(self):
        """Iterate samples from oldest to newest."""
        return iter(self._samples)

    @property
    def capacity(self) -> int:
        """Maximum number of samples the buffer keeps."""
        return self._samples.maxlen or 0

    @property
    def samples(self) -> list[Sample]:
        """A snapshot list, oldest first."""
        return list(self._samples)

    @property
    def last(self) -> Sample | None:
        """The most recent sample, or ``None`` when empty."""
        return self._samples[-1] if self._samples else None

    def add(self, sample: Sample) -> None:
        """Append a sample."""
        self._samples.append(sample)

    def clear(self) -> None:
        """Drop every sample: used when the calibration is reset from scratch."""
        self._samples.clear()

    def tail(self, count: int) -> list[Sample]:
        """The last ``count`` samples, oldest first."""
        if count <= 0:
            return []
        return list(self._samples)[-count:]

    def for_cycles(self, cycle_indices: set[int]) -> list[Sample]:
        """Samples belonging to the given cycles, in order.

        The fit is built from *complete* cycles only, so it needs to select by
        cycle rather than by time window.
        """
        return [sample for sample in self._samples if sample.cycle_index in cycle_indices]

    def raw_span(self) -> float:
        """Distance between the lowest and highest raw reading held [percent points]."""
        if not self._samples:
            return 0.0
        values = [sample.raw_percent for sample in self._samples]
        return max(values) - min(values)

    def deficit_span_mm(self) -> float:
        """Distance between the lowest and highest deficit held [mm]."""
        if not self._samples:
            return 0.0
        values = [sample.deficit_mm for sample in self._samples]
        return max(values) - min(values)

    def rederive(self, soil: SoilProfile) -> None:
        """Recompute every reference moisture against a new reservoir, in place."""
        rederived = [sample.with_soil(soil) for sample in self._samples]
        self._samples = deque(rederived, maxlen=self._samples.maxlen)

    def to_list(self) -> list[dict]:
        """Serialize for the sample store."""
        return [sample.to_dict() for sample in self._samples]

    @classmethod
    def from_list(cls, data: list[dict], capacity: int = 5000) -> SampleBuffer:
        """Rebuild a buffer from the store, skipping entries that no longer parse."""
        samples: list[Sample] = []
        for item in data:
            try:
                samples.append(Sample.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(capacity=capacity, samples=samples)
