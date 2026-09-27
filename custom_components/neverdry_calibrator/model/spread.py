"""How far apart several probes are, when several probes are in the same soil.

The calibration teaches one probe against a modelled deficit, and the error
budget that comes out of it is argued from physics rather than measured. Nothing
in this project has ever established how much of the residual is the soil, how
much is the model and how much is simply this piece of hardware.

Two probes in the *same* soil answer that last part directly. Whatever they
disagree about cannot be the soil and cannot be the model, because they share
both: it is the instrument. And it is a bound, not a curiosity. If two nominally
identical probes in one pot differ by X index points, then no calibration of
either one of them can honestly claim to resolve better than about X, whatever
the fit statistics say.

**The whole correctness of this module is one precondition: the probes have to be
in the same soil, and only the user knows whether they are.** Two probes in
different beds are *supposed* to read differently, and computing a spread across
them produces a number that looks like a measurement and is noise. So nothing is
compared unless it has been explicitly declared comparable: a probe with no
declared group is not in any group, and a group with fewer than two usable probes
has no spread rather than a spread of zero.

That distinction matters more than it looks. Zero means "they agree perfectly",
which is the most interesting possible result; ``None`` means "nobody asked the
question". Collapsing the second into the first would quietly publish the best
possible news whenever the feature was not configured.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import StrEnum


class Excluded(StrEnum):
    """Why a probe of a declared group was left out of its spread.

    Named rather than silently dropped, because a spread computed over two of
    five probes and a spread computed over all five are different claims, and the
    reader cannot tell them apart from the number.
    """

    #: The probe published nothing usable: unavailable, unknown, not a number.
    NO_READING = "no_reading"
    #: The probe's device has gone quiet, so its last reading is of unknown age.
    #: Comparing a fresh reading with a stale one measures the delay, not the
    #: instruments.
    STALE = "stale"
    #: The electrode has stopped answering. Its reading is not a measurement of
    #: anything, so including it would inflate the spread with a known fault.
    SENSING_STALLED = "sensing_stalled"


@dataclass(frozen=True, slots=True)
class ProbeReading:
    """What one probe contributes to a comparison, and whether it may contribute.

    Built by the caller from whatever it knows; this module never asks where the
    numbers came from.
    """

    probe_id: str
    name: str
    group: str
    raw_percent: float | None
    #: False when the probe's device has gone quiet. The liveness rules live in
    #: :mod:`~.liveness`; this only consumes their verdict.
    fresh: bool = True
    #: True when the electrode has stopped answering while the device talks on.
    sensing_stalled: bool = False

    def exclusion(self) -> Excluded | None:
        """Why this reading cannot be compared, or ``None`` when it can.

        Order matters: a probe that is both silent and stalled is reported as
        silent, because silence is the finding that can be established without
        trusting the reading, and the stall verdict rests on readings.
        """
        if not self.fresh:
            return Excluded.STALE
        if self.raw_percent is None:
            return Excluded.NO_READING
        if self.sensing_stalled:
            return Excluded.SENSING_STALLED
        return None


@dataclass(frozen=True, slots=True)
class GroupSpread:
    """The disagreement inside one declared group, with what it was computed over."""

    group: str
    #: Distance between the lowest and highest index in the group [points].
    spread: float
    median: float
    #: Median absolute deviation [points]. Reported beside the spread because the
    #: two answer different questions: the spread is the bound the error budget
    #: has to respect, the deviation says whether it comes from the whole group
    #: or from one probe standing apart.
    deviation: float
    considered: tuple[str, ...]
    excluded: dict[str, Excluded] = field(default_factory=dict)
    #: Probe name to index, for the ones that were actually compared. Private
    #: because a caller wanting the readings should take them from to_dict, which
    #: rounds them to the precision the instrument can support.
    _values: dict[str, float] = field(default_factory=dict, repr=False)

    @property
    def outlier(self) -> str | None:
        """The probe furthest from the median, when one is clearly apart.

        Only named above three probes and only when it is at least twice as far
        from the median as the typical member. With two probes there is no
        majority to be an outlier from, and saying which of the two is wrong
        would be a coin toss wearing a statistic.
        """
        if len(self.considered) < 3 or self.deviation <= 0:
            return None
        worst, distance = None, 0.0
        for probe_id, value in self._values.items():
            d = abs(value - self.median)
            if d > distance:
                worst, distance = probe_id, d
        return worst if distance >= 2.0 * self.deviation else None

    def to_dict(self) -> dict:
        """Serialize for entity attributes and for the diagnostics download."""
        return {
            "group": self.group,
            "spread": round(self.spread, 2),
            "median": round(self.median, 2),
            "deviation": round(self.deviation, 2),
            "considered": list(self.considered),
            "readings": {k: round(v, 2) for k, v in self._values.items()},
            "excluded": {k: str(v) for k, v in self.excluded.items()},
            "outlier": self.outlier,
        }


def spreads_by_group(readings: list[ProbeReading], min_members: int = 2) -> list[GroupSpread]:
    """Disagreement inside every declared group that has enough usable members.

    Probes with an empty group are not in a group and are never compared: see the
    module note on why silence is the only honest answer for a comparison nobody
    asked for.

    A group whose usable members fall below ``min_members`` produces no entry at
    all rather than an entry of zero, and the probes it lost are still named in
    the entry of any group that survives, so a comparison that quietly shrank can
    be told from one that did not.
    """
    grouped: dict[str, list[ProbeReading]] = {}
    for reading in readings:
        if reading.group:
            grouped.setdefault(reading.group, []).append(reading)

    out: list[GroupSpread] = []
    for group, members in sorted(grouped.items()):
        usable: dict[str, float] = {}
        excluded: dict[str, Excluded] = {}
        for member in members:
            why = member.exclusion()
            if why is not None:
                excluded[member.name] = why
            else:
                assert member.raw_percent is not None
                usable[member.name] = member.raw_percent

        if len(usable) < min_members:
            continue

        values = list(usable.values())
        median = statistics.median(values)
        out.append(
            GroupSpread(
                group=group,
                spread=max(values) - min(values),
                median=median,
                deviation=statistics.median([abs(v - median) for v in values]),
                considered=tuple(sorted(usable)),
                excluded=excluded,
                _values=usable,
            )
        )
    return out


def worst_spread(spreads: list[GroupSpread]) -> GroupSpread | None:
    """The group that disagrees most, or ``None`` when nothing could be compared.

    The worst rather than the average, for the same reason the calibration
    progress reports its least satisfied gate: a bound is only as good as the
    place it is loosest, and averaging two groups would hide the one that matters.
    """
    return max(spreads, key=lambda s: s.spread, default=None)
