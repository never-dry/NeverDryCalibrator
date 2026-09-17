"""What the collected data says about *where* the probe was put.

The estimator answers one question: given this index and this deficit, how much
water is in the soil. The samples answer a second one it never asks, and the
gates throw the answer away. A fit that fails on ``r_squared`` has told us
something specific about the installation, and the user reads "r_squared".

The distinction that matters is not fit quality but *which of two things* is
wrong. A probe can read its own few centimetres perfectly and still be useless,
because those centimetres are not where the water the model describes goes. That
is not a worse regression, it is a different problem with a different fix: one
says recalibrate, the other says dig it up and move it.

Six signatures, computed from data the cycles already carry:

* **no_response** - the reservoir emptied and the index barely moved. The probe
  is outside the wetted volume, or outside the root zone the deficit describes.
* **coarse_response** - the index moves, but so little that its own quantisation
  eats the declared error budget before any calibration error is counted.
* **unstable_between_cycles** - each cycle on its own is clean, and the cycles
  disagree with each other. The probe reads well; the spot is not representative,
  or the water does not reach it the same way twice.
* **wet_anchor_drift** - field capacity keeps reading lower (or higher) cycle
  after cycle. The soil is settling away from the shaft, or roots have grown
  into the sensing volume.
* **poor_contact** - the index jumps while the deficit stands still. Soil does
  not do that. An air gap around the shaft does.
* **outside_wetted_bulb** - the probe fills up when it rains and stays dry when
  the zone is irrigated. Rain is the only water that is not aimed, so this is
  the one comparison that separates "the probe is in a bad spot" from "the probe
  is in a spot the dripper never reaches".

The sixth one deserves its argument spelled out, because it is a comparison
across two populations and those are usually where a statistic goes wrong. It is
legitimate here for one specific reason: a cycle only opens when the *reference*
says the profile is within ``wet_anchor_fraction`` of full, ten percent of the
reservoir by default. Both anchors therefore describe the same soil state by
construction, and the only thing free to differ between them is what the probe
reported about it. The comparison is one-sided on purpose: rain reading higher
than irrigation is the signature, and irrigation reading higher than rain is
just a dripper that delivers more than a qualifying shower.

Two limits are structural and neither more data nor a better statistic removes
them, so they are stated here rather than discovered later:

**Identifiability.** With one probe against one reference, "the probe is in the
wrong place" and "the water balance model is wrong for this zone" produce the
same disagreement between the two series. Magnitude cannot separate them. Only
shape can: a water balance is smooth by construction, so a lag, a jump at
constant deficit and a hysteresis loop are necessarily the probe's. Every
signature here is deliberately one of those, or a comparison of cycles against
each other rather than against the model.

**Sample size.** Five cycles means five observations for any between-cycle
statistic. That supports naming a suspicion and showing the number behind it. It
does not support a calibrated probability, and this module never produces one:
without a field dataset of probes whose placement is independently known, a
number between zero and one would be invented precision. The gates already
refuse to publish a moisture they have not earned; this refuses to publish a
confidence it has not earned.

Consequently **nothing here blocks a calibration**. The thresholds below are
argued, not measured. Making them gates would refuse good calibrations on the
strength of numbers nobody has checked yet. They diagnose, the gates decide.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from .cycles import DryDownCycle, WaterSource
from .samples import Sample
from .soil import SoilProfile


class PlacementSuspicion(StrEnum):
    """One named way a placement can be wrong, as distinct from a fit being bad."""

    NO_RESPONSE = "no_response"
    COARSE_RESPONSE = "coarse_response"
    UNSTABLE_BETWEEN_CYCLES = "unstable_between_cycles"
    WET_ANCHOR_DRIFT = "wet_anchor_drift"
    POOR_CONTACT = "poor_contact"
    OUTSIDE_WETTED_BULB = "outside_wetted_bulb"


#: Worst first. Used to pick the single suspicion worth interrupting the user
#: for: a probe that is not in the water at all makes every other finding about
#: it moot, and six warnings about one probe is how a user learns to ignore
#: warnings.
SUSPICION_SEVERITY: tuple[PlacementSuspicion, ...] = (
    PlacementSuspicion.NO_RESPONSE,
    # Second, and above poor contact, because it is the most actionable finding
    # the module can produce: it does not say "something is off with this
    # probe", it says where the water is and where the probe is not.
    PlacementSuspicion.OUTSIDE_WETTED_BULB,
    PlacementSuspicion.POOR_CONTACT,
    PlacementSuspicion.UNSTABLE_BETWEEN_CYCLES,
    PlacementSuspicion.WET_ANCHOR_DRIFT,
    PlacementSuspicion.COARSE_RESPONSE,
)


class PlacementConfidence(StrEnum):
    """The published verdict.

    ``PLAUSIBLE`` is the strongest word available and is chosen over "good" on
    purpose: these signatures can only catch placements that misbehave in one of
    six known ways. Silence from them is the absence of evidence against, never
    evidence for.
    """

    NOT_ENOUGH_EVIDENCE = "not_enough_evidence"
    PLAUSIBLE = "plausible"
    SUSPECT = "suspect"


@dataclass(frozen=True, slots=True)
class PlacementPolicy:
    """Thresholds for the six signatures.

    One of them is derived rather than chosen. ``min_points_per_reservoir``
    follows from the error budget: over the whole plant-available range, a loam
    at thirty centimetres spans roughly thirteen percentage points of volumetric
    water content. A probe covering ``R`` index points over that range makes one
    index point worth ``13/R`` points of moisture, so below ten the probe's own
    quantisation alone costs more than a point of VWC, which is a large share of
    the "few percentage points" the method promises, before a single source of
    calibration error is added.

    The others are argued from how the failure looks, not measured. They are
    starting points to be tuned against probes whose placement is known, and
    until that exists they should be read as "worth a look", not as facts.
    """

    #: Complete cycles required before any suspicion may be published at all.
    min_cycles: int = 3
    #: Index travel per cycle below which the probe is not answering [points].
    min_raw_span: float = 2.0
    #: Index points per full reservoir below which resolution costs too much.
    min_points_per_reservoir: float = 10.0
    #: Spread of the per-cycle slope, as a share of its median.
    max_slope_spread: float = 0.5
    #: Wet-anchor excursion across cycles, as a share of the median cycle span.
    max_wet_anchor_drift_fraction: float = 0.25
    #: Deficit change below which the soil counts as standing still [of TAW].
    still_deficit_fraction: float = 0.01
    #: Index step tolerated between two such standing-still samples [points].
    max_still_raw_step: float = 2.0
    #: Standing-still pairs needed before their spread means anything.
    min_still_pairs: int = 5
    #: Complete cycles of *each* kind of water before the two may be compared.
    #: Two is the smallest number that is not an anecdote, and asking for more
    #: would keep the comparison silent through most seasons: rain arrives when
    #: it arrives, and a gardener cannot schedule the control group.
    min_cycles_per_water_source: int = 2
    #: Gap between the rain and irrigation wet anchors, as a share of the median
    #: cycle span, above which the two waters are not filling the same soil.
    max_wet_anchor_gap_fraction: float = 0.5

    def still_deficit_mm(self, total_available_water_mm: float) -> float:
        """Deficit change below which the soil is treated as unchanged [mm]."""
        return self.still_deficit_fraction * total_available_water_mm

    def to_dict(self) -> dict[str, float | int]:
        """Serialize for the config entry options."""
        return {
            "min_cycles": self.min_cycles,
            "min_raw_span": self.min_raw_span,
            "min_points_per_reservoir": self.min_points_per_reservoir,
            "max_slope_spread": self.max_slope_spread,
            "max_wet_anchor_drift_fraction": self.max_wet_anchor_drift_fraction,
            "still_deficit_fraction": self.still_deficit_fraction,
            "max_still_raw_step": self.max_still_raw_step,
            "min_still_pairs": self.min_still_pairs,
            "min_cycles_per_water_source": self.min_cycles_per_water_source,
            "max_wet_anchor_gap_fraction": self.max_wet_anchor_gap_fraction,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlacementPolicy:
        """Rebuild a policy from options, falling back to the default of each field."""
        blank = cls()
        return cls(
            min_cycles=int(data.get("min_cycles", blank.min_cycles)),
            min_raw_span=float(data.get("min_raw_span", blank.min_raw_span)),
            min_points_per_reservoir=float(data.get("min_points_per_reservoir", blank.min_points_per_reservoir)),
            max_slope_spread=float(data.get("max_slope_spread", blank.max_slope_spread)),
            max_wet_anchor_drift_fraction=float(
                data.get("max_wet_anchor_drift_fraction", blank.max_wet_anchor_drift_fraction)
            ),
            still_deficit_fraction=float(data.get("still_deficit_fraction", blank.still_deficit_fraction)),
            max_still_raw_step=float(data.get("max_still_raw_step", blank.max_still_raw_step)),
            min_still_pairs=int(data.get("min_still_pairs", blank.min_still_pairs)),
            min_cycles_per_water_source=int(data.get("min_cycles_per_water_source", blank.min_cycles_per_water_source)),
            max_wet_anchor_gap_fraction=float(
                data.get("max_wet_anchor_gap_fraction", blank.max_wet_anchor_gap_fraction)
            ),
        )


@dataclass(frozen=True, slots=True)
class PlacementVerdict:
    """Every suspicion at once, with the numbers that raised them.

    All of them rather than the first, for the same reason ``GateVerdict``
    reports every failing gate: a user who moves the probe for one reason only to
    be told next month about another has been made to wait twice for one
    diagnosis.
    """

    confidence: PlacementConfidence
    suspicions: tuple[PlacementSuspicion, ...]
    evidence: dict[str, float]

    @property
    def primary(self) -> PlacementSuspicion | None:
        """The one suspicion worth raising a repair for, or ``None``."""
        for candidate in SUSPICION_SEVERITY:
            if candidate in self.suspicions:
                return candidate
        return None

    def to_dict(self) -> dict:
        """Serialize for entity attributes and for the diagnostics download."""
        return {
            "confidence": str(self.confidence),
            "suspicions": [str(suspicion) for suspicion in self.suspicions],
            "primary": str(self.primary) if self.primary else None,
            "evidence": dict(self.evidence),
        }


def _two_point_slope(cycle: DryDownCycle) -> float | None:
    """Slope of the line through this cycle's own two anchors [m3/m3 per point].

    Deliberately not the regression: the anchors are physical, field capacity at
    one end and the driest observed state at the other, so comparing them across
    cycles compares measurements rather than comparing fits of fits.
    """
    wet, dry = cycle.wet_anchor, cycle.dry_anchor
    if wet is None or dry is None:
        return None
    raw_gap = wet.raw_percent - dry.raw_percent
    if raw_gap == 0.0:
        return None
    return (wet.reference_moisture - dry.reference_moisture) / raw_gap


def _is_monotone(values: Sequence[float]) -> bool:
    """Whether a series only ever goes one way, ties allowed.

    Strict-ish monotonicity is the conservative choice on four or five points:
    a drift that reverses is noise, and calling it drift would send a user to
    dig up a probe that is fine.
    """
    if len(values) < 3:
        return False
    deltas = [later - earlier for earlier, later in zip(values[:-1], values[1:], strict=True)]
    return all(delta >= 0 for delta in deltas) or all(delta <= 0 for delta in deltas)


def _anchors_of(cycles: Sequence[DryDownCycle], source: WaterSource) -> list[float]:
    """Wet-anchor index readings of the cycles opened by one kind of water.

    Cycles carrying ``UNKNOWN`` are left out rather than assigned to the likelier
    of the two. They were collected before the gauge existed, or while it was
    unavailable, and a guess here would be a guess in the one comparison whose
    whole value is that the two groups are known apart.
    """
    return [
        cycle.wet_anchor.raw_percent
        for cycle in cycles
        if cycle.water_source is source and cycle.wet_anchor is not None
    ]


def _still_steps(samples: Sequence[Sample], still_deficit_mm: float) -> list[float]:
    """Index steps between consecutive samples taken while the soil stood still.

    Consecutive *within one cycle*: the gap across a cycle boundary contains an
    irrigation, which is the one moment the index is supposed to jump.
    """
    ordered = sorted(samples, key=lambda sample: (sample.cycle_index, sample.taken_at))
    steps: list[float] = []
    for earlier, later in zip(ordered[:-1], ordered[1:], strict=True):
        if earlier.cycle_index != later.cycle_index:
            continue
        if abs(later.deficit_mm - earlier.deficit_mm) >= still_deficit_mm:
            continue
        steps.append(abs(later.raw_percent - earlier.raw_percent))
    return steps


def assess_placement(
    cycles: Sequence[DryDownCycle],
    samples: Sequence[Sample],
    soil: SoilProfile,
    policy: PlacementPolicy | None = None,
) -> PlacementVerdict:
    """Read the collected cycles for signs that the probe is in the wrong place.

    ``cycles`` must already be the ones that count as evidence, and ``samples``
    the ones belonging to them: deciding what counts is the cycle tracker's job
    and is not second-guessed here.

    Below ``min_cycles`` the answer is ``NOT_ENOUGH_EVIDENCE`` and the suspicion
    list is empty. Saying "looks fine" after one cycle would be the same mistake
    the honesty rule was written to prevent, one level up.
    """
    policy = policy or PlacementPolicy()
    taw = soil.total_available_water_mm
    evidence: dict[str, float] = {
        "cycles_considered": float(len(cycles)),
        "cycles_required": float(policy.min_cycles),
    }

    if len(cycles) < policy.min_cycles or taw <= 0:
        return PlacementVerdict(PlacementConfidence.NOT_ENOUGH_EVIDENCE, (), evidence)

    suspicions: list[PlacementSuspicion] = []

    # 1 and 2: does the probe answer the question, and answer it finely enough.
    raw_spans = [cycle.raw_span for cycle in cycles]
    median_raw_span = statistics.median(raw_spans)
    evidence["median_raw_span"] = round(median_raw_span, 2)
    evidence["min_raw_span"] = policy.min_raw_span

    resolutions = [cycle.raw_span / (cycle.deficit_span_mm / taw) for cycle in cycles if cycle.deficit_span_mm > 0]
    median_resolution = statistics.median(resolutions) if resolutions else 0.0
    evidence["points_per_reservoir"] = round(median_resolution, 1)
    evidence["points_per_reservoir_required"] = policy.min_points_per_reservoir

    if median_raw_span < policy.min_raw_span:
        # No response subsumes a coarse one: reporting both would read as two
        # problems when there is one, and the fix for the coarse case (accept a
        # weaker calibration) is wrong for a probe that is simply not in the water.
        suspicions.append(PlacementSuspicion.NO_RESPONSE)
    elif median_resolution < policy.min_points_per_reservoir:
        suspicions.append(PlacementSuspicion.COARSE_RESPONSE)

    # 3: do the cycles agree with each other.
    slopes = [slope for slope in (_two_point_slope(cycle) for cycle in cycles) if slope is not None]
    if len(slopes) >= policy.min_cycles:
        median_slope = statistics.median(slopes)
        if median_slope != 0.0:
            # Range rather than a median absolute deviation, and the choice is not
            # a detail. The signature being looked for is often a *single* cycle
            # that disagrees, the one where the water went somewhere else. A MAD
            # is built to survive exactly that observation, so it would hide the
            # thing worth reporting. Five cycles do not need protection from one
            # outlier; they need to be told when one exists.
            spread = (max(slopes) - min(slopes)) / abs(median_slope)
            evidence["slope_spread"] = round(spread, 3)
            evidence["slope_spread_limit"] = policy.max_slope_spread
            if spread > policy.max_slope_spread:
                suspicions.append(PlacementSuspicion.UNSTABLE_BETWEEN_CYCLES)

    # 4: is field capacity reading the same thing it read a month ago.
    wet_anchors = [cycle.wet_anchor.raw_percent for cycle in cycles if cycle.wet_anchor is not None]
    if len(wet_anchors) >= policy.min_cycles and median_raw_span > 0:
        excursion = abs(wet_anchors[-1] - wet_anchors[0])
        limit = policy.max_wet_anchor_drift_fraction * median_raw_span
        evidence["wet_anchor_drift"] = round(excursion, 2)
        evidence["wet_anchor_drift_limit"] = round(limit, 2)
        if _is_monotone(wet_anchors) and excursion > limit:
            suspicions.append(PlacementSuspicion.WET_ANCHOR_DRIFT)

    # 5: does the index move when the soil does not.
    steps = _still_steps(samples, policy.still_deficit_mm(taw))
    evidence["still_pairs"] = float(len(steps))
    if len(steps) >= policy.min_still_pairs:
        median_step = statistics.median(steps)
        evidence["still_raw_step"] = round(median_step, 2)
        evidence["still_raw_step_limit"] = policy.max_still_raw_step
        if median_step > policy.max_still_raw_step:
            suspicions.append(PlacementSuspicion.POOR_CONTACT)

    # 6: does the probe fill up for rain and stay dry for the irrigation.
    rain_anchors = _anchors_of(cycles, WaterSource.RAIN)
    irrigation_anchors = _anchors_of(cycles, WaterSource.IRRIGATION)
    evidence["rain_cycles"] = float(len(rain_anchors))
    evidence["irrigation_cycles"] = float(len(irrigation_anchors))
    evidence["cycles_per_water_source_required"] = float(policy.min_cycles_per_water_source)
    enough_of_both = (
        len(rain_anchors) >= policy.min_cycles_per_water_source
        and len(irrigation_anchors) >= policy.min_cycles_per_water_source
    )
    if enough_of_both and median_raw_span > 0:
        # Medians rather than means, with two or three values each: one cycle
        # where the sprinkler was left on, or where a storm drowned the plot,
        # would otherwise decide the verdict on its own.
        gap = statistics.median(rain_anchors) - statistics.median(irrigation_anchors)
        limit = policy.max_wet_anchor_gap_fraction * median_raw_span
        evidence["wet_anchor_gap"] = round(gap, 2)
        evidence["wet_anchor_gap_limit"] = round(limit, 2)
        if gap > limit:
            suspicions.append(PlacementSuspicion.OUTSIDE_WETTED_BULB)

    confidence = PlacementConfidence.SUSPECT if suspicions else PlacementConfidence.PLAUSIBLE
    return PlacementVerdict(confidence, tuple(suspicions), evidence)
