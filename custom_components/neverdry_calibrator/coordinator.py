"""The Home Assistant side of the calibration: read the sources, feed the domain.

This module is deliberately thin and dull. It reads states, converts their units,
decides when water reached the soil, hands an :class:`Observation` to the
:class:`CalibrationSession` and publishes whatever the session concluded. Every
judgement about whether a reading is usable, whether a cycle counts and whether a
calibration may be published lives in ``model/`` and is tested without a Home
Assistant runtime.

Two responsibilities genuinely belong here because they are about Home Assistant
rather than about soil:

* **Water detection.** The integration is told about water in whichever way the
  site can afford, by three witnesses that fail differently and are therefore
  all kept: a valve entity, which knows nothing about the weather; a rain gauge,
  which knows nothing about the valve; and the collapse of the deficit itself,
  which is slower than both, cannot say what delivered the water, and is the
  only one always present. Whichever notices first opens the drainage window,
  and each says what it saw so the cycle can record which water filled it.
* **Invalidation on device-side change.** If a calibration knob on the probe is
  turned, every sample collected before that moment describes a different
  instrument. The coordinator notices and tells the session to drop everything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AMBIENT_TEMPERATURE_ENTITY,
    CONF_DEFICIT_ENTITY,
    CONF_IRRIGATION_ENTITY,
    CONF_MOISTURE_ENTITY,
    CONF_PROBE_TEMPERATURE_ENTITY,
    CONF_RAIN_ENTITY,
    DOMAIN,
    ISSUE_PLACEMENT_PREFIX,
    ISSUE_THRESHOLD_TOO_LOW,
    REFIT_INTERVAL_MINUTES,
    STORAGE_SAVE_DELAY_S,
    UPDATE_INTERVAL_MINUTES,
)
from .discovery import CompanionEntities, discover_irrigation_threshold, snapshot_calibration_values
from .model import (
    CalibratedReading,
    CalibrationFit,
    CalibrationSession,
    CalibrationStatus,
    GateVerdict,
    Observation,
    PlacementSuspicion,
    PlacementVerdict,
    ProbeLiveness,
    RainUpdate,
    RejectionReason,
    WaterSource,
    deficit_to_mm,
    probe_liveness,
    raw_index_to_percent,
    temperature_to_celsius,
)
from .model.calibration import InvalidationReason
from .probe import ProbeConfig
from .settings import merged_settings, rain_sensor_kind

_LOGGER = logging.getLogger(__name__)

#: States that mean water is being delivered right now, across the entity
#: domains a site may point at: switch, valve, binary_sensor, input_boolean.
IRRIGATION_ACTIVE_STATES: frozenset[str] = frozenset({"on", "open", "opening"})

#: How far the fitted gain may sit from the device's own scale before a
#: write-back offset stops being meaningful. A constant offset can remove a
#: bias, never a wrong slope.
MAX_WRITEBACK_GAIN_DEVIATION: float = 0.20


@dataclass(frozen=True, slots=True)
class DerivedThresholds:
    """The thresholds of the calibration, expressed in the millimetres a user reads.

    The domain states them as fractions of the reservoir, which is the only way
    they can be right for both sand at fifteen centimetres and clay at a metre.
    That is correct and unreadable: nobody configures an irrigation system in
    fractions of total available water. These are the same numbers translated
    into the unit the deficit is published in, so that the one that actually
    constrains the site, the minimum depletion a cycle must cover, can be
    compared with the threshold the irrigation is set to.
    """

    total_available_water_mm: float
    irrigation_drop_mm: float
    wet_anchor_deficit_mm: float
    min_cycle_span_mm: float

    def is_reachable_with(self, irrigation_threshold_mm: float | None) -> bool | None:
        """Whether an irrigation regime can ever produce a countable cycle.

        ``None`` when the threshold is unknown, which must not read as a failure:
        plenty of sites irrigate on a timer and publish no threshold at all.
        """
        if irrigation_threshold_mm is None:
            return None
        return irrigation_threshold_mm >= self.min_cycle_span_mm

    def to_dict(self) -> dict[str, float]:
        """Serialize for entity attributes."""
        return {
            "total_available_water_mm": round(self.total_available_water_mm, 1),
            "irrigation_drop_mm": round(self.irrigation_drop_mm, 1),
            "wet_anchor_deficit_mm": round(self.wet_anchor_deficit_mm, 1),
            "min_cycle_span_mm": round(self.min_cycle_span_mm, 1),
        }


@dataclass(frozen=True, slots=True)
class CalibratorData:
    """One published snapshot: what was read, what was concluded, and how sure.

    Entities read this and nothing else, which keeps every entity free of logic
    and makes the whole integration's output inspectable in one object.
    """

    status: CalibrationStatus
    raw_percent: float | None
    deficit_mm: float | None
    probe_temperature_c: float | None
    ambient_temperature_c: float | None
    reading: CalibratedReading | None
    provisional_moisture: float | None
    liveness: ProbeLiveness
    fit: CalibrationFit | None
    verdict: GateVerdict | None
    drift_rmse: float | None
    last_rejection: RejectionReason | None
    sample_count: int
    complete_cycles: int
    open_cycle_index: int
    irrigation_active: bool
    seconds_since_irrigation: float | None
    battery_percent: float | None
    thresholds: DerivedThresholds | None = None
    irrigation_threshold_mm: float | None = None
    placement: PlacementVerdict | None = None
    raining: bool = False
    #: Depth accumulated inside the rain event in progress [mm]. Zero between
    #: events, which is different from "no gauge": that is ``rain_watched``.
    rain_accumulated_mm: float = 0.0
    rain_event_depth_mm: float | None = None
    rain_watched: bool = False
    cycles_by_source: dict[str, int] = field(default_factory=dict)

    @property
    def calibration_progress(self) -> float:
        """How far the calibration is towards passing its gates, as a percentage.

        The minimum across the counting gates rather than an average: a
        calibration is as ready as its least satisfied condition, and averaging
        would show eighty percent to a probe that has seen a single cycle.
        """
        if self.status is CalibrationStatus.CALIBRATED:
            return 100.0
        if self.verdict is None:
            return 0.0
        progress = self.verdict.progress
        ratios: list[float] = []
        for value_key, required_key in (
            ("cycles", "cycles_required"),
            ("samples", "samples_required"),
            ("raw_span", "raw_span_required"),
        ):
            required = progress.get(required_key, 0.0)
            if required > 0:
                ratios.append(min(1.0, progress.get(value_key, 0.0) / required))
        if not ratios:
            return 0.0
        return round(min(ratios) * 100.0, 1)


class CalibrationCoordinator(DataUpdateCoordinator[CalibratorData]):
    """Polls the sources, feeds the session, and persists what was learned."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        probe: ProbeConfig,
        session: CalibrationSession,
        store: Store,
        companions: CompanionEntities,
        stored_snapshot: dict[str, str] | None = None,
    ) -> None:
        """Wire the coordinator to one probe of one config entry."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {probe.name}",
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self.entry = entry
        self.probe = probe
        self.session = session
        self.companions = companions
        self._store = store
        self._previous_deficit_mm: float | None = None
        self._last_irrigation_end: datetime | None = None
        self._irrigation_was_active = False
        self._last_refit: datetime | None = session.last_fit_at
        self._complete_cycles_seen = 0
        self._verdict: GateVerdict | None = None
        self._placement: PlacementVerdict | None = None
        self._calibration_snapshot: dict[str, str] = {}
        self._stored_snapshot: dict[str, str] = dict(stored_snapshot or {})
        self._threshold_entity: str | None = None

    # ── Source reading ───────────────────────────────────────────

    def _state_of(self, key: str) -> State | None:
        """Current state of a configured source, or ``None`` when unset or unusable."""
        entity_id = self.probe.entity_for(key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        return state

    @staticmethod
    def _age_seconds(state: State, now: datetime) -> float:
        """How long ago the device last spoke, in seconds.

        ``last_reported`` is preferred where the Home Assistant version provides
        it: a probe that keeps sending the same value updates it while leaving
        ``last_updated`` frozen, and that difference is exactly the case the
        liveness sentinel exists to catch.
        """
        stamp = getattr(state, "last_reported", None) or state.last_updated
        return max(0.0, (now - stamp).total_seconds())

    @staticmethod
    def _numeric(state: State | None) -> float | None:
        """Parse a state as a float, or ``None`` when it is not numeric."""
        if state is None:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _read_temperature(self, key: str) -> tuple[float | None, float | None]:
        """Read a temperature source as degrees Celsius plus the age of the reading."""
        state = self._state_of(key)
        value = self._numeric(state)
        if state is None or value is None:
            return None, None
        celsius = temperature_to_celsius(value, state.attributes.get(ATTR_UNIT_OF_MEASUREMENT))
        if celsius is None:
            return None, None
        return celsius, self._age_seconds(state, dt_util.utcnow())

    def _read_irrigation(self) -> bool:
        """Whether the configured irrigation entity says water is flowing right now."""
        state = self._state_of(CONF_IRRIGATION_ENTITY)
        if state is None:
            return False
        return state.state in IRRIGATION_ACTIVE_STATES

    @property
    def rain_entity(self) -> str | None:
        """The gauge this probe listens to, or ``None`` when it listens to none.

        The entity is configured once for the installation and read per probe,
        because whether rain counts is a property of the probe: one under a roof
        is told nothing, and every probe weighs the same shower against its own
        reservoir.
        """
        if self.probe.sheltered_from_rain:
            return None
        return merged_settings(self.entry).get(CONF_RAIN_ENTITY) or None

    def _read_rain(self) -> tuple[float | None, str | None]:
        """Read the gauge as millimetres, with the identity of the reading.

        The marker is the state's ``last_updated`` rather than its value, and
        that is the whole reason an event gauge works at all: two tips of two
        millimetres are two events carrying the same number, while a poll that
        lands on an untouched state is no event carrying that same number.
        """
        entity_id = self.rain_entity
        if not entity_id:
            return None, None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None, None
        value = self._numeric(state)
        if value is None:
            return None, None
        depth = deficit_to_mm(value, state.attributes.get(ATTR_UNIT_OF_MEASUREMENT))
        marker = state.last_updated.isoformat() if state.last_updated else None
        return depth, marker

    def _detect_rain(self, now: datetime) -> RainUpdate | None:
        """Offer the gauge to the session, and open the drainage window if rain ended.

        Called before the observation is built, unlike the other two witnesses,
        because rain is the only one whose verdict changes the observation
        itself: whether it is raining right now decides whether this reading may
        become a sample at all.
        """
        depth, marker = self._read_rain()
        if depth is None:
            return None
        update = self.session.note_rain(depth, now, marker)
        if update.wetting_ended_at is not None:
            _LOGGER.debug(
                "%s: %.1f mm of rain ended at %s, counted as water delivered",
                self.probe.name,
                update.wetting_depth_mm,
                update.wetting_ended_at.isoformat(),
            )
            self._last_irrigation_end = update.wetting_ended_at
            self._schedule_save()
        return update

    def _battery_percent(self) -> float | None:
        """Battery level of the probe, when the device publishes one."""
        if not self.companions.battery:
            return None
        state = self.hass.states.get(self.companions.battery)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    # ── Lifecycle ────────────────────────────────────────────────

    async def async_prepare(self) -> None:
        """Take the first snapshot of the device-side calibration knobs.

        Done once at setup rather than lazily, so that the first comparison after
        a restart is against a value read in this run and not against a stale one
        that would report a change nobody made. The irrigation threshold is
        located here for the same reason: it is a registry lookup, and the
        registry is settled by the time the entry is set up.
        """
        self._calibration_snapshot = snapshot_calibration_values(self.hass, self.companions.calibration_entities)
        self._threshold_entity = discover_irrigation_threshold(self.hass, self.probe.deficit_entity)
        # The gauge's shape is told to the session here rather than at
        # construction because this also drops the baseline, which is what a
        # restart needs: a restored tipping-bucket state is the echo of a tip
        # that was already counted, and a restored running total predates this
        # boot entirely. Both are rebased instead of credited.
        self.session.set_rain_sensor_kind(rain_sensor_kind(merged_settings(self.entry)))
        stored = self._stored_snapshot
        if stored and self._calibration_snapshot and stored != self._calibration_snapshot:
            _LOGGER.warning(
                "%s: device calibration changed while Home Assistant was down; "
                "the collected samples describe a different instrument and were dropped",
                self.probe.name,
            )
            self.session.invalidate(InvalidationReason.DEVICE_CALIBRATION_CHANGED, dt_util.utcnow())

    def store_payload(self) -> dict:
        """What gets written to disk: the session plus the device-knob snapshot.

        The snapshot travels with the samples rather than with the config entry
        because it is evidence about the data, not configuration: it is what
        allows a restart to notice that the probe was re-calibrated by hand while
        Home Assistant was down.
        """
        return {
            "session": self.session.to_dict(),
            "calibration_snapshot": dict(self._calibration_snapshot),
        }

    def _schedule_save(self) -> None:
        """Ask the store to persist the session shortly, coalescing bursts of samples."""
        self._store.async_delay_save(self.store_payload, STORAGE_SAVE_DELAY_S)

    async def async_save_now(self) -> None:
        """Persist immediately: used on unload and after a destructive service call."""
        await self._store.async_save(self.store_payload())

    # ── Update cycle ─────────────────────────────────────────────

    def _detect_water(self, now: datetime, deficit_mm: float | None, irrigation_active: bool) -> None:
        """Notice that water reached the soil, from the valve or from the deficit.

        Both witnesses are kept because both fail differently: a valve entity
        knows nothing about rain or a watering can, and a deficit model that is
        slow to credit water would delay the drainage window. Whichever fires
        first opens the window.
        """
        if irrigation_active:
            self._last_irrigation_end = now
            if not self._irrigation_was_active:
                self.session.note_irrigation(now, WaterSource.IRRIGATION)
            self._irrigation_was_active = True
            return

        if self._irrigation_was_active:
            self._last_irrigation_end = now
            self._irrigation_was_active = False

        if deficit_mm is None:
            return
        if self._previous_deficit_mm is not None and self.session.deficit_drop_is_irrigation(
            self._previous_deficit_mm, deficit_mm
        ):
            _LOGGER.debug(
                "%s: deficit fell from %.1f to %.1f mm, read as water delivered",
                self.probe.name,
                self._previous_deficit_mm,
                deficit_mm,
            )
            # Deliberately unnamed water. This witness sees a reservoir refill
            # and cannot see what refilled it, and the tracker knows not to let
            # an unnamed witness overwrite what the gauge or the valve already
            # said about the same wetting.
            self.session.note_irrigation(now, WaterSource.UNKNOWN)
            self._last_irrigation_end = now
        self._previous_deficit_mm = deficit_mm

    def _check_device_calibration(self, now: datetime) -> None:
        """Invalidate the calibration if a device-side knob moved since the last look."""
        current = snapshot_calibration_values(self.hass, self.companions.calibration_entities)
        if not current or not self._calibration_snapshot:
            self._calibration_snapshot = current or self._calibration_snapshot
            return
        if current != self._calibration_snapshot:
            _LOGGER.warning(
                "%s: device calibration changed (%s -> %s); calibration invalidated",
                self.probe.name,
                self._calibration_snapshot,
                current,
            )
            self._calibration_snapshot = current
            self.session.invalidate(InvalidationReason.DEVICE_CALIBRATION_CHANGED, now)
            self._schedule_save()

    def build_observation(self, now: datetime) -> Observation:
        """Assemble the current state of every source into one domain observation."""
        moisture_state = self._state_of(CONF_MOISTURE_ENTITY)
        raw_value = self._numeric(moisture_state)
        raw_percent = raw_index_to_percent(raw_value) if raw_value is not None else None

        deficit_state = self._state_of(CONF_DEFICIT_ENTITY)
        deficit_value = self._numeric(deficit_state)
        deficit_mm = None
        deficit_age = None
        if deficit_state is not None and deficit_value is not None:
            deficit_mm = deficit_to_mm(deficit_value, deficit_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT))
            deficit_age = self._age_seconds(deficit_state, now)

        probe_temperature, probe_age = self._read_temperature(CONF_PROBE_TEMPERATURE_ENTITY)
        ambient_temperature, _ = self._read_temperature(CONF_AMBIENT_TEMPERATURE_ENTITY)
        irrigation_active = self._read_irrigation()

        seconds_since_irrigation = None
        if self._last_irrigation_end is not None:
            seconds_since_irrigation = (now - self._last_irrigation_end).total_seconds()

        raining, seconds_since_rain = self.session.rain_state(now)

        return Observation(
            taken_at=now,
            raw_percent=raw_percent,
            deficit_mm=deficit_mm,
            deficit_age_s=deficit_age,
            probe_temperature_c=probe_temperature,
            probe_temperature_age_s=probe_age,
            ambient_temperature_c=ambient_temperature,
            irrigation_active=irrigation_active,
            seconds_since_irrigation=seconds_since_irrigation,
            rain_active=raining,
            seconds_since_rain=seconds_since_rain,
        )

    def derived_thresholds(self) -> DerivedThresholds:
        """Translate the fraction-based policy into millimetres of this reservoir."""
        session = self.session
        taw = session.soil.total_available_water_mm
        policy = session.cycle_policy
        return DerivedThresholds(
            total_available_water_mm=taw,
            irrigation_drop_mm=policy.irrigation_drop_mm(taw),
            wet_anchor_deficit_mm=policy.wet_anchor_deficit_mm(taw),
            min_cycle_span_mm=policy.min_cycle_span_mm(taw),
        )

    def configured_irrigation_threshold_mm(self) -> float | None:
        """The depletion the irrigation source waters at, in millimetres, if it says.

        Read from the entity discovered next to the deficit, and converted like
        any other foreign quantity: a threshold published in inches is still a
        threshold.
        """
        if not self._threshold_entity:
            return None
        state = self.hass.states.get(self._threshold_entity)
        value = self._numeric(state)
        if state is None or value is None:
            return None
        return deficit_to_mm(value, state.attributes.get(ATTR_UNIT_OF_MEASUREMENT))

    def _review_irrigation_regime(self, thresholds: DerivedThresholds, configured: float | None) -> None:
        """Raise or clear the repair telling the user their regime cannot calibrate.

        This is the one piece of advice the integration can give before weeks are
        wasted: it already knows the minimum depletion a cycle must cover, and it
        can see the depletion the site waters at. Saying nothing, and letting the
        calibration sit at "collecting" forever, is what the first version did.
        """
        issue_id = f"{ISSUE_THRESHOLD_TOO_LOW}_{self.entry.entry_id}_{self.probe.probe_id}"
        if thresholds.is_reachable_with(configured) is not False:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_THRESHOLD_TOO_LOW,
            translation_placeholders={
                "probe": self.probe.name,
                "configured": f"{configured:.1f}",
                "required": f"{thresholds.min_cycle_span_mm:.1f}",
                "reservoir": f"{thresholds.total_available_water_mm:.0f}",
                "deficit_entity": self.probe.deficit_entity,
            },
        )

    def _placement_placeholders(self) -> dict[str, str]:
        """Every number a placement repair might quote, formatted for a template.

        One set for all six messages rather than one per message: each template
        uses the two or three it needs, the unused ones cost nothing, and a
        signature that did not run leaves a dash instead of breaking the string.
        """
        evidence = self._placement.evidence if self._placement else {}
        keys = (
            "cycles_considered",
            "median_raw_span",
            "points_per_reservoir",
            "points_per_reservoir_required",
            "slope_spread",
            "wet_anchor_drift",
            "still_raw_step",
            "wet_anchor_gap",
            "rain_cycles",
            "irrigation_cycles",
        )
        placeholders = {key: f"{evidence[key]:g}" if key in evidence else "-" for key in keys}
        placeholders["probe"] = self.probe.name
        placeholders["moisture_entity"] = self.probe.moisture_entity
        return placeholders

    def _review_placement(self) -> None:
        """Raise or clear the repair that says the probe may be in the wrong place.

        Only the most severe suspicion becomes a repair, and the rest stay on the
        placement entity. The reasoning is the same one that made the irrigation
        repair worth writing: the advice has to arrive before weeks are spent,
        and a user shown six warnings about one probe learns to close all six
        without reading them.

        This never touches the calibration. The thresholds behind these
        signatures are argued and not yet measured against probes whose placement
        is independently known, which is exactly the kind of number that may
        advise a user and may not overrule one.
        """
        primary = self._placement.primary if self._placement else None
        for suspicion in PlacementSuspicion:
            if suspicion is primary:
                continue
            ir.async_delete_issue(self.hass, DOMAIN, self._placement_issue_id(suspicion))
        if primary is None:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._placement_issue_id(primary),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=f"{ISSUE_PLACEMENT_PREFIX}_{primary}",
            translation_placeholders=self._placement_placeholders(),
        )

    def _placement_issue_id(self, suspicion: PlacementSuspicion) -> str:
        """Issue id of one signature on one probe of one entry."""
        return f"{ISSUE_PLACEMENT_PREFIX}_{suspicion}_{self.entry.entry_id}_{self.probe.probe_id}"

    def _should_refit(self, now: datetime, complete_cycles: int) -> bool:
        """Refit when a cycle completed, or when the periodic interval has elapsed.

        A completed cycle is the only event that can change the verdict from
        "not enough evidence" to "calibrated", so it always forces a refit; the
        interval exists only to keep a long dry-down from freezing the residual.
        """
        if complete_cycles != self._complete_cycles_seen:
            return True
        if self._last_refit is None:
            return True
        return (now - self._last_refit) >= timedelta(minutes=REFIT_INTERVAL_MINUTES)

    async def _async_update_data(self) -> CalibratorData:
        """Read the sources, offer them to the session and publish the conclusion."""
        now = dt_util.utcnow()
        self._check_device_calibration(now)

        rain = self._detect_rain(now)
        observation = self.build_observation(now)
        self._detect_water(now, observation.deficit_mm, observation.irrigation_active)

        admission = self.session.observe(observation)
        if admission.accepted:
            self._schedule_save()

        complete_cycles = len(self.session.tracker.complete_cycles())
        if self._should_refit(now, complete_cycles):
            self._verdict = await self.hass.async_add_executor_job(self.session.refit, now)
            self._last_refit = now
            self._complete_cycles_seen = complete_cycles
            self.session.update_drift()
            self._placement = self.session.assess_placement()
            self._schedule_save()

        reading = None
        if observation.raw_percent is not None:
            reading = self.session.calibrated_reading(observation.raw_percent, observation.probe_temperature_c)

        provisional = None
        if reading is None and self.session.provisional_fit and observation.raw_percent is not None:
            provisional = self.session.provisional_fit.estimate(
                observation.raw_percent, observation.probe_temperature_c
            )
            provisional = min(self.session.soil.saturation, max(0.0, provisional))

        thresholds = self.derived_thresholds()
        configured_threshold = self.configured_irrigation_threshold_mm()
        self._review_irrigation_regime(thresholds, configured_threshold)
        self._review_placement()

        return CalibratorData(
            status=self.session.status,
            raw_percent=observation.raw_percent,
            deficit_mm=observation.deficit_mm,
            probe_temperature_c=observation.probe_temperature_c,
            ambient_temperature_c=observation.ambient_temperature_c,
            reading=reading,
            provisional_moisture=provisional,
            liveness=probe_liveness(observation, self.session.admission_policy),
            fit=self.session.fit,
            verdict=self._verdict,
            drift_rmse=self.session.drift_rmse,
            last_rejection=self.session.last_rejection,
            sample_count=len(self.session.buffer),
            complete_cycles=complete_cycles,
            open_cycle_index=self.session.tracker.current_index,
            irrigation_active=observation.irrigation_active,
            seconds_since_irrigation=observation.seconds_since_irrigation,
            battery_percent=self._battery_percent(),
            thresholds=thresholds,
            irrigation_threshold_mm=configured_threshold,
            placement=self._placement,
            raining=observation.rain_active,
            rain_accumulated_mm=rain.accumulated_mm if rain else 0.0,
            rain_event_depth_mm=(
                self.session.rain_witness.event_depth_mm if self.session.rain_witness is not None else None
            ),
            rain_watched=self.rain_entity is not None,
            cycles_by_source=self.session.tracker.complete_cycles_by_source(),
        )

    # ── Service entry points ─────────────────────────────────────

    async def async_calibrate_now(self) -> GateVerdict:
        """Force an immediate refit and report the verdict, passed or not."""
        now = dt_util.utcnow()
        self._verdict = await self.hass.async_add_executor_job(self.session.refit, now)
        self._last_refit = now
        self.session.update_drift()
        await self.async_save_now()
        await self.async_request_refresh()
        return self._verdict

    async def async_reset_calibration(self) -> None:
        """Throw away samples, cycles and fit: the probe starts earning trust again."""
        self.session.reset(dt_util.utcnow())
        self._previous_deficit_mm = None
        self._last_irrigation_end = None
        self._complete_cycles_seen = 0
        self._verdict = None
        self._placement = None
        await self.async_save_now()
        await self.async_request_refresh()

    async def async_mark_field_capacity(self) -> bool:
        """Record by hand that the soil is at field capacity right now."""
        now = dt_util.utcnow()
        observation = self.build_observation(now)
        recorded = self.session.mark_field_capacity(observation, now)
        if recorded:
            self._last_irrigation_end = now
            await self.async_save_now()
            await self.async_request_refresh()
        return recorded

    def suggested_device_offset(self) -> tuple[float | None, float | None]:
        """Offset to push to the device, and how far the fitted gain is from its scale.

        The device knob adds a constant to the index it publishes, so it can
        remove a bias and nothing else. The suggestion is the difference, at the
        centre of the range the fit was tested on, between the available water
        the calibration implies and the index the probe reports. The gain
        deviation is returned alongside because a probe whose slope is wrong
        cannot be fixed by any offset, and the caller must be able to refuse.
        """
        fit = self.session.fit
        if fit is None:
            return None, None
        soil = self.session.soil
        available_span = soil.field_capacity - soil.wilting_point
        if available_span <= 0:
            return None, None
        midpoint = (fit.raw_min + fit.raw_max) / 2.0
        moisture = fit.estimate(midpoint)
        available_percent = soil.available_fraction_at_moisture(moisture) * 100.0
        device_gain = fit.slope * 100.0 / available_span
        return available_percent - midpoint, abs(device_gain - 1.0)

    async def async_apply_device_offset(self, offset: float | None = None) -> float:
        """Write an offset into the probe's own calibration knob, then start over.

        Writing to the device is opt-in because it changes what every other
        consumer of that probe sees, and because it invalidates this
        integration's own history by construction: after the write, the index no
        longer means what the collected samples say it means.
        """
        target = self.companions.moisture_calibration
        if target is None:
            raise ValueError("no device-side moisture calibration entity was discovered")
        suggested, gain_deviation = self.suggested_device_offset()
        value = offset if offset is not None else suggested
        if value is None:
            raise ValueError("no calibration is available to derive an offset from")
        if offset is None and gain_deviation is not None and gain_deviation > MAX_WRITEBACK_GAIN_DEVIATION:
            raise ValueError(
                f"fitted gain differs from the device scale by {gain_deviation:.0%}; "
                "an offset cannot correct a slope, pass one explicitly to override"
            )
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": target, "value": round(value, 2)},
            blocking=True,
        )
        self.session.invalidate(InvalidationReason.DEVICE_CALIBRATION_CHANGED, dt_util.utcnow())
        self._calibration_snapshot = snapshot_calibration_values(self.hass, self.companions.calibration_entities)
        await self.async_save_now()
        await self.async_request_refresh()
        return value

    def export_samples(self) -> dict:
        """Everything the calibration is built on, for a service response or a bug report."""
        return {
            "soil": self.session.soil.to_dict(),
            "status": str(self.session.status),
            "fit": self.session.fit.to_dict() if self.session.fit else None,
            "provisional_fit": self.session.provisional_fit.to_dict() if self.session.provisional_fit else None,
            "cycles": [cycle.to_dict() for cycle in self.session.tracker.cycles],
            "samples": self.session.buffer.to_list(),
            "rejections": dict(self.session.rejection_counts),
        }
