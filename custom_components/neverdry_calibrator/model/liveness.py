"""Two ways a cheap probe dies without disappearing, and the state that catches them.

A probe that stops working does not become unavailable. It keeps publishing, and
the integration keeps pairing what it publishes with a reference deficit that is
still moving. Every such pair is a sample that describes a dead instrument, and
because the pairs arrive on a timer they arrive in quantity.

Two distinct failures produce that, and neither is caught by the other:

* **The device stops talking.** Flat battery, radio out of range, coordinator
  dropped it. Home Assistant keeps the last state forever, so the only evidence
  is the *age* of the newest word from the device. :class:`ProbeCadence` supplies
  the bar that age is compared against.
* **The device keeps talking and the sensing element stops.** The radio, the
  battery level and the temperature channel all behave, while the moisture
  electrode returns the same number regardless of the soil. No silence exists to
  measure, so freshness cannot see it at all: what gives it away is a reference
  that travels while the raw reading does not. :class:`SensingWitness` watches
  for exactly that.

The second is the one the sibling project leaves open, and it is the more
expensive of the two here. NeverDry consumes a probe reading to decide one
irrigation, so a frozen reading costs it a day. This project turns probe readings
into a *published calibration* that other software then trusts, so a frozen
reading is baked into a line and keeps being wrong long after the probe is fixed.

Both classes are plain state machines over floats and datetimes, fed by the
session and persisted with it. Nothing here knows what Home Assistant is.

See ``docs/design/probe-liveness.md`` for the cost asymmetry that sets every
threshold here, the alternatives that were rejected and what remains wrong.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

#: How far back the freshness bar remembers, in seconds.
#:
#: Days rather than samples, and the distinction is the whole reason this is not
#: a rolling average. A probe publishes on change, so it speaks every thirty
#: seconds while the ground dries and once an hour while it sits still. A window
#: counted in samples fills with the fast readings and evicts the slow ones,
#: which are the only evidence of the device's real heartbeat, so the bar ends up
#: below the cadence the probe has always had and the probe is called dead for
#: behaving normally. A week holds a probe's slowest honest stretch and is short
#: enough that a one-off outage stops counting once the week has turned.
CADENCE_MEMORY_S: float = 7 * 24 * 3600

#: Slack above the longest quiet actually observed, as a multiplier.
#:
#: The bar has to tolerate the honest silence that has not happened yet. Without
#: any slack, the first gap longer than every previous gap marks the probe stale
#: for one poll and then widens the bar by having happened, which costs a refused
#: sample and a status flap to learn something the device was entitled to do.
#: Two is deliberately small: see the module note in :class:`ProbeCadence` on why
#: this estimator errs short where the sibling project's errs long.
CADENCE_TOLERANCE: float = 2.0

#: How long a device must have been watched before its own bar is used at all [s].
#:
#: A day, and it has to be a day rather than a few hours. A probe reports on
#: change, so its longest honest silence is the one that happens while nothing is
#: moving, and on a garden that is the night. A bar learned from six daylight hours
#: contains only the gaps of a soil that was drying, comes out far below the night
#: gap, and declares the probe dead at two in the morning of the first night. Until
#: the day is up the configured timeout applies, which is the behaviour this
#: project had before the bar existed, so the warm-up costs nothing.
#:
#: Counting elapsed time and not ended silences, because the decreasing sequence
#: below is not a census: a device with a perfectly regular heartbeat has every
#: silence but one dominated by a later equal one, so it would hold a single entry
#: forever and a count-based gate would never open for the commonest device there
#: is.
CADENCE_LEARNING_MIN_S: float = 24 * 3600

#: Watching time required as a multiple of the longest silence observed.
#:
#: The other half of the same gate, and the half that scales. A silence of length
#: ``L`` cannot be known to be the longest until the device has had several further
#: chances to beat it; four is the smallest number of chances that is not an
#: anecdote. Without this, a device whose first day happens to contain one long
#: outage would have that outage as its bar, which is the direction that hides a
#: dead probe.
CADENCE_LEARNING_MULTIPLE: float = 4.0

#: Hard ceiling on the freshness bar, whatever cadence the probe demonstrates.
#:
#: Six hours, where the sibling project's equivalent backstop is twenty-four, and
#: the gap between those two numbers is the cost asymmetry stated in
#: :class:`ProbeCadence`. It is also the point past which the probe cannot do
#: this job anyway: a drying cycle has to be resolved into enough samples to fit
#: a line through, and a device managing one reading every six hours cannot
#: describe the shape of a dry-down whatever its readings say.
LIVENESS_CEILING_S: float = 6 * 3600


@dataclass
class ProbeCadence:
    """The longest quiet the probe's device has come back from, within a window.

    The question this answers is "how long may this device stay silent before the
    silence means something", and the answer cannot be a constant. One probe
    publishes every thirty seconds and another twice a day; any number chosen
    here would call one of them dead. So the bar is the device's own demonstrated
    habit, and the only constants are the ones that bound it.

    **Maximum, not a quantile.** A high quantile is the right estimator for a
    fleet of devices reporting at a steady rate, and the wrong one for a single
    device reporting on change. Such a device produces two populations of gaps:
    many short ones while its readings are moving, and a few long ones that are
    the only evidence of its heartbeat. Any statistic that lets the first outvote
    the second sets the bar below the heartbeat. Measured next door, on a probe
    whose real cadence was 55 minutes: an evening of 30-second readings filled a
    forty-sample window, the 0.95 quantile discarded the single 55-minute gap,
    the bar came out at 33 minutes, and the probe was declared stopped at 34.

    **Only ended silences count.** Quiet that is still going on is the thing
    being judged; admitting it as evidence would make every silence normal by the
    act of lasting.

    **This estimator errs short, and the sibling project's errs long.** Same
    mechanism, opposite tuning, because the cost of being wrong is not the same
    on the two sides. There, a bar that is too short hands a zone from its probe
    to a weather estimate on a different scale, silently, for a night. Here, a
    bar that is too short refuses a sample and says which reason it refused it
    for, in a diagnostic built to be read; while a bar that is too long lets a
    dead probe's readings into the fit, and the fit is the product. So the slack
    above the observed maximum is small, the ceiling is a quarter of the sibling
    project's, and with too little evidence the bar falls back to the configured
    timeout rather than to the ceiling.

    Held as a decreasing sequence, which is what keeps it cheap: a sample is
    worth keeping only until a later and larger one arrives, so the front is
    always the window's maximum and what follows is a handful of entries rather
    than every gap the device has ever had.
    """

    window_s: float = CADENCE_MEMORY_S
    #: When this device was first watched. The gate below is a span of time, so
    #: something has to remember where the span starts.
    first_seen_s: float | None = None
    _samples: deque[tuple[float, float]] = field(default_factory=deque, repr=False)

    def record(self, at_s: float, quiet_s: float) -> None:
        """A stretch of quiet that ended: the device spoke again after ``quiet_s``."""
        if quiet_s <= 0:
            return
        # The start of the span is the beginning of the silence, not its end: the
        # device was already being watched while it was quiet.
        began_s = at_s - quiet_s
        if self.first_seen_s is None or began_s < self.first_seen_s:
            self.first_seen_s = began_s
        while self._samples and self._samples[-1][1] <= quiet_s:
            self._samples.pop()
        self._samples.append((at_s, quiet_s))
        self._prune(at_s)

    def observed_max(self, at_s: float) -> float | None:
        """Longest ended silence still inside the window, or ``None`` when there is none."""
        self._prune(at_s)
        return self._samples[0][1] if self._samples else None

    def watched_s(self, at_s: float) -> float:
        """How long this device has been under observation [s]."""
        if self.first_seen_s is None:
            return 0.0
        return max(0.0, at_s - self.first_seen_s)

    def is_established(self, at_s: float) -> bool:
        """Whether the observed bar has earned the right to replace the configured one.

        Both halves of the gate have to hold: a full day of watching, and several
        times the longest silence seen. The first keeps a bar from being learned
        out of a single daylight stretch, the second keeps one long outage from
        becoming the bar on the day it happened.
        """
        observed = self.observed_max(at_s)
        if observed is None:
            return False
        required = max(CADENCE_LEARNING_MIN_S, CADENCE_LEARNING_MULTIPLE * observed)
        return self.watched_s(at_s) >= required

    def bar_s(self, at_s: float, fallback_s: float) -> float:
        """The silence this device is allowed, in seconds.

        ``fallback_s`` is the configured timeout, used until the device has
        demonstrated a habit. It is the answer this project gave before the bar
        existed, so a probe whose cadence is not yet known is judged exactly as it
        used to be, and an installation that tuned that number keeps what it tuned.
        """
        if not self.is_established(at_s):
            return min(fallback_s, LIVENESS_CEILING_S)
        observed = self.observed_max(at_s) or 0.0
        return min(observed * CADENCE_TOLERANCE, LIVENESS_CEILING_S)

    def _prune(self, at_s: float) -> None:
        """Drop samples that have fallen out of the trailing window."""
        cutoff = at_s - self.window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def to_list(self) -> list[list[float]]:
        """The evidence, in a shape that survives a restart.

        It has to survive, because the other half of the judgement does. When the
        device last spoke is derivable from the states themselves the moment Home
        Assistant comes back; the bar it is compared against is not, so without
        this a reload leaves an age measured against no bar at all, and no bar
        means the configured fallback for as long as it takes to re-earn three
        silences. Next door, the same omission accepted a probe silent for fifteen
        hours as fresh.
        """
        return [[at_s, quiet_s] for at_s, quiet_s in self._samples]

    def to_dict(self) -> dict:
        """The bar plus the span it was learned over, for the sample store.

        The span has to travel with the samples. Restoring the silences without it
        would leave a device that has been watched for a week looking as though it
        had just been plugged in, and the bar would sit at the configured fallback
        until a fresh day had passed.
        """
        return {"samples": self.to_list(), "first_seen_s": self.first_seen_s}

    @classmethod
    def from_dict(cls, data: object, window_s: float = CADENCE_MEMORY_S) -> ProbeCadence:
        """Rebuild from the store, accepting the bare list an older store wrote.

        The older shape is not a migration worth writing: a bare list restores the
        silences and leaves the span unknown, which costs one day of falling back
        to the configured timeout and nothing else.
        """
        if isinstance(data, dict):
            cadence = cls.from_list(data.get("samples"), window_s=window_s)
            first_seen = _optional_float(data.get("first_seen_s"))
            if first_seen is not None:
                cadence.first_seen_s = min(first_seen, cadence.first_seen_s or first_seen)
            return cadence
        return cls.from_list(data, window_s=window_s)

    @classmethod
    def from_list(cls, data: object, window_s: float = CADENCE_MEMORY_S) -> ProbeCadence:
        """Take back evidence written by a previous run, and refuse anything else.

        A stored bar is the one piece of this state that makes the judgement
        stricter, so a malformed payload must not be allowed to silently widen it
        or to raise inside a restore. Unreadable entries are skipped and the
        device re-earns them.
        """
        cadence = cls(window_s=window_s)
        if not isinstance(data, list):
            return cadence
        pairs: list[tuple[float, float]] = []
        for item in data:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            try:
                at_s, quiet_s = float(item[0]), float(item[1])
            except (TypeError, ValueError):
                continue
            if quiet_s > 0:
                pairs.append((at_s, quiet_s))
        # Replayed through ``record`` rather than assigned, so the decreasing
        # invariant is established by the same code that maintains it. A stored
        # sequence that is not decreasing is then repaired instead of trusted.
        for at_s, quiet_s in sorted(pairs):
            cadence.record(at_s, quiet_s)
        return cadence


@dataclass
class SensingWitness:
    """Watches for a raw reading that stands still while the soil demonstrably dries.

    This is the failure freshness cannot see. The device is on the mesh, its
    battery is fine, its temperature channel moves with the day, and its moisture
    electrode has stopped answering: corroded contacts, water in the housing, a
    dead analogue front end. Everything that measures *whether the device speaks*
    reports a healthy probe.

    What gives it away is a comparison with the reference. The deficit is an
    independent account of the same soil, so a stretch where the deficit climbs
    by a quarter of the reservoir while the index does not move by half a point
    is not a probe in a bad spot, it is a probe that is not measuring. A probe in
    a bad spot still moves, only less.

    **Drying only, and the anchor resets on wetting.** Accumulating the absolute
    travel of the deficit would be wrong in a way that fires constantly: water
    arriving drops the deficit by most of the reservoir in one step, so a single
    irrigation would look like a full excursion and any probe that had not
    refreshed within the poll would be called dead. Water also arrives in
    discontinuities this cannot interpret, while a dry-down is the one stretch
    where the reference moves slowly, monotonically, and for a reason the probe is
    supposed to see. So wetting resets the anchor and only drying accumulates.

    **The threshold is a share of the reservoir, not millimetres.** Half of total
    available water is roughly twenty millimetres for a loam at thirty centimetres
    and eighty for a deep clay, and the same absolute figure cannot mean the same
    thing in both.

    **Half the reservoir, and the size of that fraction is what keeps this channel
    from stealing the placement diagnostics' work.** A probe in a gravel void or
    outside the wetted volume also answers weakly, and that finding belongs to
    :mod:`~.placement`, which reports it with advice about where the probe sits.
    So the two have to be separated by more than measurement error. Requiring half
    a point of movement across half the reservoir puts this threshold at one index
    point per reservoir, against the two points per cycle below which placement
    calls a probe unresponsive and the ten points per reservoir below which it
    calls one coarse: a factor of two below the weakest probe placement wants to
    talk about, and a factor of ten below the weakest one this project will publish
    a calibration for. A probe that moves at all lands in placement's hands; only
    one that does not move lands here.
    """

    #: Index movement at or above which the probe counts as having answered [points].
    #: Devices publish integers, so anything real clears this with room to spare
    #: and it is the noise floor rather than a tuning knob.
    raw_move_epsilon: float = 0.5
    #: Drying travel required before a motionless index means anything [of TAW].
    stall_deficit_fraction: float = 0.5
    #: Deficit fall treated as water arriving rather than as noise [of TAW].
    wetting_fraction: float = 0.02

    raw_at_anchor: float | None = None
    deficit_at_anchor: float | None = None
    last_deficit_mm: float | None = None
    anchored_at: datetime | None = None
    #: Largest drying travel accumulated against a motionless index [mm]. Kept
    #: rather than recomputed because it is the number the diagnostic shows, and
    #: a user told "the probe has not moved" deserves to be told across how much.
    stalled_travel_mm: float = 0.0

    def note(self, raw_percent: float | None, deficit_mm: float | None, at: datetime) -> None:
        """Offer the current pair, whether or not it was admitted as a sample.

        Fed from every observation on purpose. A stalled probe is refused as a
        sample once this fires, so a witness fed only from admitted samples would
        lose its evidence at the moment it started being right.
        """
        if raw_percent is None or deficit_mm is None:
            return

        previous = self.last_deficit_mm
        self.last_deficit_mm = deficit_mm

        if self.raw_at_anchor is None or self.deficit_at_anchor is None:
            self._anchor(raw_percent, deficit_mm, at)
            return

        if abs(raw_percent - self.raw_at_anchor) >= self.raw_move_epsilon:
            self._anchor(raw_percent, deficit_mm, at)
            return

        if previous is not None and deficit_mm < previous - self._wetting_mm():
            # Water arrived. Whatever the index did or did not do across the
            # discontinuity says nothing, and the stretch that was being measured
            # is over.
            self._anchor(raw_percent, deficit_mm, at)
            return

        travel = deficit_mm - self.deficit_at_anchor
        self.stalled_travel_mm = max(0.0, travel)

    def stalled(self, total_available_water_mm: float) -> bool:
        """Whether the soil has dried far enough for a motionless index to be a fault."""
        required = self.stall_deficit_fraction * total_available_water_mm
        if required <= 0:
            return False
        return self.stalled_travel_mm >= required

    def required_travel_mm(self, total_available_water_mm: float) -> float:
        """Drying travel a motionless index is tolerated across [mm]."""
        return self.stall_deficit_fraction * total_available_water_mm

    def reset(self) -> None:
        """Forget everything: used when the calibration starts over."""
        self.raw_at_anchor = None
        self.deficit_at_anchor = None
        self.last_deficit_mm = None
        self.anchored_at = None
        self.stalled_travel_mm = 0.0

    def _wetting_mm(self) -> float:
        """Deficit fall read as water rather than as noise [mm].

        Expressed against the anchor's own deficit rather than the reservoir,
        because this class is not told the reservoir and does not need to be: the
        fraction only has to be larger than the jitter of a deficit sensor and
        smaller than a real delivery, and both hold against any reasonable soil.
        """
        base = abs(self.deficit_at_anchor or 0.0)
        return max(0.2, self.wetting_fraction * base)

    def _anchor(self, raw_percent: float, deficit_mm: float, at: datetime) -> None:
        """Start a fresh stretch here: this index, this deficit, no travel yet."""
        self.raw_at_anchor = raw_percent
        self.deficit_at_anchor = deficit_mm
        self.anchored_at = at
        self.stalled_travel_mm = 0.0

    def to_dict(self) -> dict:
        """Serialize for the sample store."""
        return {
            "raw_at_anchor": self.raw_at_anchor,
            "deficit_at_anchor": self.deficit_at_anchor,
            "last_deficit_mm": self.last_deficit_mm,
            "anchored_at": self.anchored_at.isoformat() if self.anchored_at else None,
            "stalled_travel_mm": self.stalled_travel_mm,
        }

    @classmethod
    def from_dict(cls, data: object) -> SensingWitness:
        """Rebuild from the store, starting clean on anything that does not parse."""
        witness = cls()
        if not isinstance(data, dict):
            return witness
        witness.raw_at_anchor = _optional_float(data.get("raw_at_anchor"))
        witness.deficit_at_anchor = _optional_float(data.get("deficit_at_anchor"))
        witness.last_deficit_mm = _optional_float(data.get("last_deficit_mm"))
        stamp = data.get("anchored_at")
        if isinstance(stamp, str):
            try:
                witness.anchored_at = datetime.fromisoformat(stamp)
            except ValueError:
                witness.anchored_at = None
        witness.stalled_travel_mm = _optional_float(data.get("stalled_travel_mm")) or 0.0
        if witness.raw_at_anchor is None or witness.deficit_at_anchor is None:
            # Half a restored anchor is worse than none: the travel would be
            # measured from a deficit that no index was recorded against.
            witness.reset()
        return witness


def _optional_float(value: object) -> float | None:
    """Coerce a stored value to float, keeping ``None`` and refusing nonsense."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
