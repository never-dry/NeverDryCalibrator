"""The dry-down cycle: the unit of evidence this calibration is counted in.

Samples are cheap and correlated. A probe read every ten minutes for a day
produces one hundred and forty points that all say the same thing about the same
soil state, and a regression fed with them looks confident while having seen a
single fact. The unit that actually carries information is the *cycle*: water
goes in, the deficit collapses to nearly zero, the soil dries out again, and the
probe is forced to travel its whole range once.

That is why the quality gate downstream counts cycles, not samples: five
irrigation-to-dry-down cycles, by default, before the calibration is allowed to
call itself calibrated. Each cycle contributes two physical anchors that need no
faith in the regression at all:

* the **wet anchor**, the first settled reading after irrigation and drainage,
  where the profile is at field capacity by definition of drainage;
* the **dry anchor**, the reading at the largest deficit reached before the next
  irrigation, which is the driest point the probe was asked about in that cycle.

A cycle that never dried appreciably is not evidence, it is a flat line, so a
cycle only counts once its deficit span covers a configurable share of the total
available water.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .samples import Sample


class TrackerState(StrEnum):
    """Where the tracker is in the irrigate/drain/dry loop."""

    WAITING_FOR_WATER = "waiting_for_water"
    DRAINING = "draining"
    DRYING = "drying"


class CycleClosure(StrEnum):
    """Why a cycle stopped collecting."""

    IRRIGATION = "irrigation"
    SOIL_CHANGED = "soil_changed"
    RESET = "reset"


@dataclass(frozen=True, slots=True)
class CyclePolicy:
    """Thresholds that decide what counts as water, as a wet anchor, and as a cycle.

    All three are fractions of the total available water rather than millimetres:
    ten millimetres is a whole reservoir on sand at fifteen centimetres of root
    depth and a rounding error on clay at a metre, so an absolute default would
    be wrong for most sites.
    """

    irrigation_drop_fraction: float = 0.20
    wet_anchor_fraction: float = 0.10
    min_cycle_span_fraction: float = 0.30
    min_samples_per_cycle: int = 4

    def irrigation_drop_mm(self, total_available_water_mm: float) -> float:
        """Deficit drop that is read as an irrigation or a downpour [mm]."""
        return self.irrigation_drop_fraction * total_available_water_mm

    def wet_anchor_deficit_mm(self, total_available_water_mm: float) -> float:
        """Deficit below which a settled reading is taken as field capacity [mm]."""
        return self.wet_anchor_fraction * total_available_water_mm

    def min_cycle_span_mm(self, total_available_water_mm: float) -> float:
        """Deficit span a cycle must cover before it counts as evidence [mm]."""
        return self.min_cycle_span_fraction * total_available_water_mm

    def to_dict(self) -> dict[str, float | int]:
        """Serialize for the config entry options."""
        return {
            "irrigation_drop_fraction": self.irrigation_drop_fraction,
            "wet_anchor_fraction": self.wet_anchor_fraction,
            "min_cycle_span_fraction": self.min_cycle_span_fraction,
            "min_samples_per_cycle": self.min_samples_per_cycle,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CyclePolicy:
        """Rebuild a policy from options, falling back to the default of each field."""
        blank = cls()
        return cls(
            irrigation_drop_fraction=float(data.get("irrigation_drop_fraction", blank.irrigation_drop_fraction)),
            wet_anchor_fraction=float(data.get("wet_anchor_fraction", blank.wet_anchor_fraction)),
            min_cycle_span_fraction=float(data.get("min_cycle_span_fraction", blank.min_cycle_span_fraction)),
            min_samples_per_cycle=int(data.get("min_samples_per_cycle", blank.min_samples_per_cycle)),
        )


@dataclass(slots=True)
class DryDownCycle:
    """One irrigation-to-dry-down span, with the two anchors it produced.

    Identity is the ``index``: cycles are numbered in order and samples carry the
    number of the cycle they were taken in, which is how the fit selects the
    complete ones without keeping a second copy of the data.
    """

    index: int
    opened_at: datetime
    wet_anchor: Sample | None = None
    dry_anchor: Sample | None = None
    sample_count: int = 0
    min_deficit_mm: float | None = None
    max_deficit_mm: float | None = None
    min_raw_percent: float | None = None
    max_raw_percent: float | None = None
    closed_at: datetime | None = None
    closure: CycleClosure | None = None

    @property
    def is_open(self) -> bool:
        """True while the cycle is still collecting samples."""
        return self.closed_at is None

    @property
    def deficit_span_mm(self) -> float:
        """How far the deficit travelled inside this cycle [mm]."""
        if self.min_deficit_mm is None or self.max_deficit_mm is None:
            return 0.0
        return self.max_deficit_mm - self.min_deficit_mm

    @property
    def raw_span(self) -> float:
        """How far the probe index travelled inside this cycle [percent points]."""
        if self.min_raw_percent is None or self.max_raw_percent is None:
            return 0.0
        return self.max_raw_percent - self.min_raw_percent

    def absorb(self, sample: Sample) -> None:
        """Fold a sample into the cycle, updating the running extremes and the dry anchor.

        The wet anchor is set once, by the tracker, at the moment the cycle
        opens: it is the *first* settled reading after drainage, and a later
        reading at an equally low deficit is not the same physical event.
        """
        self.sample_count += 1
        if self.min_deficit_mm is None or sample.deficit_mm < self.min_deficit_mm:
            self.min_deficit_mm = sample.deficit_mm
        if self.max_deficit_mm is None or sample.deficit_mm > self.max_deficit_mm:
            self.max_deficit_mm = sample.deficit_mm
            self.dry_anchor = sample
        if self.min_raw_percent is None or sample.raw_percent < self.min_raw_percent:
            self.min_raw_percent = sample.raw_percent
        if self.max_raw_percent is None or sample.raw_percent > self.max_raw_percent:
            self.max_raw_percent = sample.raw_percent

    def close(self, at: datetime, closure: CycleClosure) -> None:
        """Stop collecting. A cycle is closed once and keeps whatever it gathered."""
        if self.closed_at is None:
            self.closed_at = at
            self.closure = closure

    def counts_as_evidence(self, policy: CyclePolicy, total_available_water_mm: float) -> bool:
        """Whether this cycle may be counted towards the minimum number of cycles.

        Three conditions, all of them about information rather than bookkeeping:
        the wet end was actually observed, the soil actually dried, and there are
        enough points in between to place a line rather than a segment.
        """
        if self.wet_anchor is None or self.dry_anchor is None:
            return False
        if self.sample_count < policy.min_samples_per_cycle:
            return False
        return self.deficit_span_mm >= policy.min_cycle_span_mm(total_available_water_mm)

    def to_dict(self) -> dict:
        """Serialize for the sample store."""
        return {
            "index": self.index,
            "opened_at": self.opened_at.isoformat(),
            "wet_anchor": self.wet_anchor.to_dict() if self.wet_anchor else None,
            "dry_anchor": self.dry_anchor.to_dict() if self.dry_anchor else None,
            "sample_count": self.sample_count,
            "min_deficit_mm": self.min_deficit_mm,
            "max_deficit_mm": self.max_deficit_mm,
            "min_raw_percent": self.min_raw_percent,
            "max_raw_percent": self.max_raw_percent,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "closure": str(self.closure) if self.closure else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DryDownCycle:
        """Rebuild a cycle from the store."""
        wet = data.get("wet_anchor")
        dry = data.get("dry_anchor")
        closed_at = data.get("closed_at")
        closure = data.get("closure")
        return cls(
            index=int(data["index"]),
            opened_at=datetime.fromisoformat(data["opened_at"]),
            wet_anchor=Sample.from_dict(wet) if wet else None,
            dry_anchor=Sample.from_dict(dry) if dry else None,
            sample_count=int(data.get("sample_count", 0)),
            min_deficit_mm=_optional_float(data.get("min_deficit_mm")),
            max_deficit_mm=_optional_float(data.get("max_deficit_mm")),
            min_raw_percent=_optional_float(data.get("min_raw_percent")),
            max_raw_percent=_optional_float(data.get("max_raw_percent")),
            closed_at=datetime.fromisoformat(closed_at) if closed_at else None,
            closure=CycleClosure(closure) if closure else None,
        )


def _optional_float(value: object) -> float | None:
    """Coerce a stored value to float, keeping ``None`` as ``None``."""
    if value is None:
        return None
    return float(value)


@dataclass(slots=True)
class CycleTracker:
    """Turns a stream of irrigation events and samples into numbered cycles.

    The tracker is a three-state machine, and the states exist because the probe
    lies in two of them. Right after water is delivered the wetting front sits
    around the electrode and the reading is about the water in transit, not the
    water the root zone will keep; only after the drainage window does a reading
    describe field capacity. And before the first irrigation is ever seen there
    is no wet anchor, so samples cannot be attributed to a cycle at all.
    """

    policy: CyclePolicy
    total_available_water_mm: float
    state: TrackerState = TrackerState.WAITING_FOR_WATER
    cycles: list[DryDownCycle] = field(default_factory=list)
    last_irrigation_at: datetime | None = None
    next_index: int = 1

    @property
    def open_cycle(self) -> DryDownCycle | None:
        """The cycle currently collecting, if any."""
        if self.cycles and self.cycles[-1].is_open:
            return self.cycles[-1]
        return None

    @property
    def current_index(self) -> int:
        """Index samples should be tagged with: the open cycle, or 0 when none is open."""
        current = self.open_cycle
        return current.index if current else 0

    def complete_cycles(self) -> list[DryDownCycle]:
        """Every closed cycle that counts as evidence, oldest first."""
        return [
            cycle
            for cycle in self.cycles
            if not cycle.is_open and cycle.counts_as_evidence(self.policy, self.total_available_water_mm)
        ]

    def complete_cycle_indices(self) -> set[int]:
        """Indices of the cycles the fit is allowed to read."""
        return {cycle.index for cycle in self.complete_cycles()}

    def note_irrigation(self, at: datetime) -> None:
        """Record that water was delivered: close the open cycle and start draining.

        Called both when an irrigation entity switches off and when the deficit
        itself collapses, because a site may irrigate by hand or be rained on and
        the deficit is the only witness the integration is guaranteed to have.
        """
        current = self.open_cycle
        if current is not None:
            current.close(at, CycleClosure.IRRIGATION)
        self.last_irrigation_at = at
        self.state = TrackerState.DRAINING

    def observe(self, sample: Sample) -> DryDownCycle | None:
        """Attribute a sample to a cycle, opening one when the wet anchor arrives.

        Returns the cycle the sample was filed under, or ``None`` when no cycle
        could be opened yet. Samples taken before the first observed wet anchor
        are deliberately discarded from the fit: without an anchor there is no
        way to tell a dry soil from a probe reading low.
        """
        wet_threshold = self.policy.wet_anchor_deficit_mm(self.total_available_water_mm)

        if self.state is not TrackerState.DRYING:
            if sample.deficit_mm > wet_threshold:
                return None
            cycle = DryDownCycle(index=self.next_index, opened_at=sample.taken_at, wet_anchor=sample)
            self.next_index += 1
            self.cycles.append(cycle)
            self.state = TrackerState.DRYING
            cycle.absorb(sample)
            return cycle

        cycle = self.open_cycle
        if cycle is None:
            self.state = TrackerState.WAITING_FOR_WATER
            return None
        cycle.absorb(sample)
        return cycle

    def detects_irrigation(self, previous_deficit_mm: float, current_deficit_mm: float) -> bool:
        """Whether a deficit drop is large enough to be read as water reaching the soil."""
        drop = previous_deficit_mm - current_deficit_mm
        return drop >= self.policy.irrigation_drop_mm(self.total_available_water_mm)

    def close_all(self, at: datetime, closure: CycleClosure) -> None:
        """Close whatever is open: used on soil changes and on manual reset."""
        current = self.open_cycle
        if current is not None:
            current.close(at, closure)
        self.state = TrackerState.WAITING_FOR_WATER

    def reset(self) -> None:
        """Forget every cycle. The calibration starts from scratch."""
        self.cycles = []
        self.state = TrackerState.WAITING_FOR_WATER
        self.last_irrigation_at = None
        self.next_index = 1

    def to_dict(self) -> dict:
        """Serialize for the sample store."""
        return {
            "state": str(self.state),
            "cycles": [cycle.to_dict() for cycle in self.cycles],
            "last_irrigation_at": self.last_irrigation_at.isoformat() if self.last_irrigation_at else None,
            "next_index": self.next_index,
        }

    @classmethod
    def from_dict(cls, data: dict, policy: CyclePolicy, total_available_water_mm: float) -> CycleTracker:
        """Rebuild a tracker from the store, skipping cycles that no longer parse."""
        cycles: list[DryDownCycle] = []
        for item in data.get("cycles", []):
            try:
                cycles.append(DryDownCycle.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        last_irrigation = data.get("last_irrigation_at")
        return cls(
            policy=policy,
            total_available_water_mm=total_available_water_mm,
            state=TrackerState(data.get("state", TrackerState.WAITING_FOR_WATER)),
            cycles=cycles,
            last_irrigation_at=datetime.fromisoformat(last_irrigation) if last_irrigation else None,
            next_index=int(data.get("next_index", len(cycles) + 1)),
        )
