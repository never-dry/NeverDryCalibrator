"""The rain gauge: water nobody ordered, measured instead of inferred.

Rain was always part of this calibration, it just had no witness. Until the
gauge existed it reached the domain through one indirect path: the reference
deficit collapsing by a fifth of the reservoir, read as "somebody watered". That
path is right about downpours and blind to everything else, and both failures
cost evidence.

* **A shower is invisible.** Four millimetres on a forty millimetre reservoir
  wet the top centimetres, lift the probe index, and move the deficit by a
  tenth of what the irrigation threshold asks. The samples that follow are
  admitted as ordinary dry-down points, and a wet bias enters the slope with no
  name on it. This is the failure worth fixing: it is silent.
* **A downpour arrives late.** It is seen through the latency of somebody
  else's water balance, so the drainage window opens after the front has
  already redistributed, and the wet anchor can be taken on water still in
  transit.

A gauge fixes both, and pays for itself a third time: rain is the only water
that arrives *without being aimed*. Drip wets a bulb, rain wets everything, and
that difference is what :mod:`.placement` turns into the one diagnosis the five
original signatures could not make.

Two shapes of gauge, the taxonomy NeverDry already settled on, with the same
crediting rule and the same two incidents behind it:

* **event** - the value *is* the delta, millimetres per tip of the bucket. A new
  tip is told from a recomputation by the reading's marker, not by its value,
  so two consecutive 2 mm tips both count while a poll that happened to land on
  an unchanged state counts nothing.
* **accumulator** - a daily total, a rolling window or a lifetime counter.
  Only positive increments are credited. A fall is a midnight reset, a window
  ageing out, or a glitch, and never precipitation.

**When an event ends.** An irrigation announces its own end: the valve closes.
Rain does not, so it has to be declared over by silence - ``quiet_minutes``
without a positive credit. That instant, not the first drop, is when the
drainage window starts, because it is when the last water arrived.

**How much rain counts as a wetting.** The same share of total available water
that makes a deficit drop count as an irrigation, for the same reason the other
three thresholds here are fractions: ten millimetres is a whole reservoir on
sand at fifteen centimetres and a rounding error on clay at a metre.

**Two things this deliberately does not model.**

*Runoff.* Heavy rain on dry clay leaves the plot. The gauge measures what fell
on the funnel, not what entered the soil, so a qualifying event can overstate
the water by a lot. Modelling infiltration would need a soil hydraulic model
this project does not have and does not want; the gauge is therefore treated as
a witness that water arrived, never as a measurement of how much reached the
root zone. Nothing downstream multiplies by it.

*The depth asymmetry.* Light rain wets the sensing volume while the water
balance credits the millimetres to the whole root zone, so the probe reads wet
against a deficit that barely moved. That is exactly the contamination worth
refusing, and refusing is all that happens: correcting it would mean inventing
the infiltration model above and calling its output a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RainSensorKind(StrEnum):
    """What the number on the gauge entity means."""

    #: Millimetres delivered by the last tip. The value is the increment.
    EVENT = "event"
    #: A running total: since midnight, over a rolling window, or since install.
    ACCUMULATOR = "accumulator"


@dataclass(frozen=True, slots=True)
class RainPolicy:
    """When rain counts as a wetting, and when it counts as over."""

    #: Share of total available water that makes an accumulation a wetting event.
    #: Defaults to the irrigation drop fraction: the user asked for rain to count
    #: as an irrigation, and one number is easier to reason about than two.
    event_fraction: float = 0.20
    #: Silence that declares a rain event finished [minutes].
    quiet_minutes: float = 30.0

    @property
    def quiet_seconds(self) -> float:
        """The quiet window in the unit the observations carry."""
        return self.quiet_minutes * 60.0

    def event_depth_mm(self, total_available_water_mm: float) -> float:
        """Accumulated depth that makes the current event a wetting [mm]."""
        return self.event_fraction * total_available_water_mm

    def to_dict(self) -> dict[str, float]:
        """Serialize for the config entry options."""
        return {
            "event_fraction": self.event_fraction,
            "quiet_minutes": self.quiet_minutes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RainPolicy:
        """Rebuild a policy from options, falling back to the default of each field."""
        blank = cls()
        return cls(
            event_fraction=float(data.get("event_fraction", blank.event_fraction)),
            quiet_minutes=float(data.get("quiet_minutes", blank.quiet_minutes)),
        )


@dataclass(frozen=True, slots=True)
class RainUpdate:
    """What one look at the gauge changed.

    ``wetting_ended_at`` is the whole point of the type: it is non-``None``
    exactly once per qualifying event, at the poll that finds the rain has gone
    quiet, and it carries the instant the *last* water arrived rather than the
    instant it was noticed.
    """

    credited_mm: float
    accumulated_mm: float
    raining: bool
    wetting_ended_at: datetime | None = None
    wetting_depth_mm: float = 0.0

    @property
    def is_wetting(self) -> bool:
        """Whether this update closed an event big enough to count as water delivered."""
        return self.wetting_ended_at is not None


@dataclass(slots=True)
class RainWitness:
    """Turns gauge readings into credited millimetres and wetting events.

    One witness per probe rather than one per installation, even though the
    gauge entity is shared: the threshold is a share of *this* probe's
    reservoir, and the same shower is a wetting for a pot of sand and a drizzle
    for a metre of clay.

    The witness has no clock. Every instant it needs is passed in, which is what
    lets the whole of it be tested against a script of timestamps.
    """

    policy: RainPolicy
    total_available_water_mm: float
    kind: RainSensorKind = RainSensorKind.EVENT
    #: Last value seen, the reference a running total is differenced against.
    baseline_mm: float | None = None
    #: Identity of the last counted pulse, for an event gauge.
    last_marker: str | None = None
    #: Depth credited inside the event currently in progress [mm].
    accumulated_mm: float = 0.0
    event_started_at: datetime | None = None
    #: Last positive credit. Survives the end of an event: the admission rules
    #: keep refusing samples for a while after the rain stops, and that window
    #: is measured from here.
    last_rain_at: datetime | None = None
    qualified: bool = False

    @property
    def event_depth_mm(self) -> float:
        """Depth this probe's reservoir needs before rain counts as a wetting [mm]."""
        return self.policy.event_depth_mm(self.total_available_water_mm)

    def is_raining(self, now: datetime) -> bool:
        """Whether water is still arriving, or stopped too recently to say so."""
        if self.event_started_at is None or self.last_rain_at is None:
            return False
        return (now - self.last_rain_at).total_seconds() < self.policy.quiet_seconds

    def seconds_since_rain(self, now: datetime) -> float | None:
        """Time since the last drop was credited, or ``None`` if none ever was."""
        if self.last_rain_at is None:
            return None
        return (now - self.last_rain_at).total_seconds()

    def observe(self, value_mm: float | None, at: datetime, marker: str | None = None) -> RainUpdate:
        """Fold one gauge reading in, and report whether an event just closed.

        ``marker`` identifies the reading for an event gauge and is ignored by an
        accumulator. In Home Assistant it is the state's ``last_updated``: the
        state object is what changes when a bucket tips, and the value is not,
        because two identical tips are two events.
        """
        credited = self._credit(value_mm, marker)
        if credited > 0.0:
            if self.event_started_at is None:
                self.event_started_at = at
            self.accumulated_mm += credited
            self.last_rain_at = at
            if self.total_available_water_mm > 0.0 and self.accumulated_mm >= self.event_depth_mm:
                self.qualified = True

        if self.event_started_at is not None and not self.is_raining(at):
            depth = self.accumulated_mm
            ended_at = self.last_rain_at if self.qualified else None
            self._close_event()
            return RainUpdate(
                credited_mm=credited,
                accumulated_mm=depth,
                raining=False,
                wetting_ended_at=ended_at,
                wetting_depth_mm=depth if ended_at is not None else 0.0,
            )

        return RainUpdate(
            credited_mm=credited,
            accumulated_mm=self.accumulated_mm,
            raining=self.is_raining(at),
        )

    def _credit(self, value_mm: float | None, marker: str | None) -> float:
        """Millimetres this reading adds, by the rule that fits the gauge's shape.

        The first reading after a restart never credits, whatever the gauge
        shows: a tipping bucket restores the state of a tip that was already
        counted, and a running total restores an accumulation that predates this
        boot. Both are rebased instead, and the rain that fell while Home
        Assistant was down is lost on purpose rather than invented.
        """
        if value_mm is None or value_mm < 0.0:
            return 0.0

        if self.kind is RainSensorKind.EVENT:
            if self.baseline_mm is None:
                self.last_marker = marker
                self.baseline_mm = value_mm
                return 0.0
            if marker is None:
                # No identity available for the reading, so a repeated poll of an
                # unchanged state cannot be told from a second tip of the same
                # depth. Crediting on a value change is the conservative half of
                # that trade: it under-counts steady rain rather than inventing
                # millimetres out of a stale state.
                if value_mm == self.baseline_mm:
                    return 0.0
                self.baseline_mm = value_mm
                return value_mm
            if marker == self.last_marker:
                return 0.0
            self.last_marker = marker
            self.baseline_mm = value_mm
            return value_mm

        if self.baseline_mm is None:
            self.baseline_mm = value_mm
            return 0.0
        delta = value_mm - self.baseline_mm
        self.baseline_mm = value_mm
        if delta < 0.0:
            return 0.0
        return delta

    def _close_event(self) -> None:
        """Forget the event in progress, keeping the instant of the last drop."""
        self.accumulated_mm = 0.0
        self.event_started_at = None
        self.qualified = False

    def forget(self) -> None:
        """Drop everything, including the memory that it ever rained.

        Used when the calibration starts over. The baseline goes too: the next
        reading is a fresh reference, which is the safe direction to be wrong in.
        """
        self._close_event()
        self.last_rain_at = None
        self.rebase()

    def rebase(self) -> None:
        """Drop the baseline so the next reading is taken as a fresh reference.

        Used when the gauge entity changes underneath the integration: the new
        sensor's counter has nothing to do with the old one's, and differencing
        across the swap would credit a year of rain in one step.
        """
        self.baseline_mm = None
        self.last_marker = None

    def to_dict(self) -> dict:
        """Serialize for the sample store.

        The baseline and the marker are deliberately *not* stored. Home Assistant
        stamps a restored state with the restore time, so a persisted marker
        would never match and every restart would credit the last tip again;
        rebasing on the first reading after a restart is the same choice made for
        the same reason one integration over.
        """
        return {
            "kind": str(self.kind),
            "accumulated_mm": self.accumulated_mm,
            "event_started_at": self.event_started_at.isoformat() if self.event_started_at else None,
            "last_rain_at": self.last_rain_at.isoformat() if self.last_rain_at else None,
            "qualified": self.qualified,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict,
        policy: RainPolicy,
        total_available_water_mm: float,
        kind: RainSensorKind | None = None,
    ) -> RainWitness:
        """Rebuild a witness from the store, keeping an event that was in progress."""
        stored_kind = kind or RainSensorKind(data.get("kind", RainSensorKind.EVENT))
        started = data.get("event_started_at")
        last_rain = data.get("last_rain_at")
        return cls(
            policy=policy,
            total_available_water_mm=total_available_water_mm,
            kind=stored_kind,
            accumulated_mm=float(data.get("accumulated_mm", 0.0)),
            event_started_at=datetime.fromisoformat(started) if started else None,
            last_rain_at=datetime.fromisoformat(last_rain) if last_rain else None,
            qualified=bool(data.get("qualified", False)),
        )
