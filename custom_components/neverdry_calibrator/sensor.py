"""The entities a user reads: one calibrated value and its diagnostics.

The split is deliberate and follows the honesty rule of this integration. The
main entity carries only what has been earned: a soil moisture, published solely
once the calibration passed its gates, and unknown before that. Everything the
user needs while waiting, or to understand why a calibration was refused, is in
the diagnostic entities, where an automation is unlikely to mistake it for a
measurement.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import CalibrationCoordinator
from .entity import CalibratorEntity, hub_device_info
from .model import CalibrationStatus, PlacementConfidence


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the calibrated sensor and the diagnostics of every configured probe."""
    runtime: dict[str, CalibrationCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [InstallationSummarySensor(entry, runtime)]
    for coordinator in runtime.values():
        entities.extend(
            [
                CalibratedMoistureSensor(coordinator),
                CalibrationStatusSensor(coordinator),
                ProbePlacementSensor(coordinator),
                CalibrationProgressSensor(coordinator),
                CompleteCyclesSensor(coordinator),
                CalibrationDriftSensor(coordinator),
                RequiredDepletionSensor(coordinator),
            ]
        )
    async_add_entities(entities)


class InstallationSummarySensor(SensorEntity):
    """How many probes of this installation are calibrated, out of how many.

    The one entity that belongs to the hub rather than to a probe. It exists so
    that the integration answers its own headline question, "is anything actually
    calibrated", without opening a device.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: ConfigEntry, runtime: dict[str, CalibrationCoordinator]) -> None:
        """Watch every probe of the entry at once."""
        self._entry = entry
        self._runtime = runtime
        self._attr_name = "Calibrated probes"
        self._attr_unique_id = f"{entry.entry_id}_calibrated_probes"
        self._attr_translation_key = "calibrated_probes"
        self._attr_device_info = hub_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Subscribe to every probe: any of them changing changes this total."""
        await super().async_added_to_hass()
        for coordinator in self._runtime.values():
            self.async_on_remove(coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> int:
        """Number of probes currently publishing an earned calibration."""
        return sum(
            1
            for coordinator in self._runtime.values()
            if coordinator.data is not None and coordinator.data.status is CalibrationStatus.CALIBRATED
        )

    @property
    def extra_state_attributes(self) -> dict:
        """One line per probe: where each of them stands."""
        return {
            "probes_configured": len(self._runtime),
            "probes": {
                coordinator.probe.name: {
                    "status": str(coordinator.data.status) if coordinator.data else "unknown",
                    "progress": coordinator.data.calibration_progress if coordinator.data else 0.0,
                    "complete_cycles": coordinator.data.complete_cycles if coordinator.data else 0,
                    "placement": (
                        str(coordinator.data.placement.confidence)
                        if coordinator.data and coordinator.data.placement
                        else "unknown"
                    ),
                }
                for coordinator in self._runtime.values()
            },
        }


class CalibratedMoistureSensor(CalibratorEntity, SensorEntity):
    """Soil water content inferred from the probe, once the probe has earned it."""

    entity_description = SensorEntityDescription(
        key="calibrated_moisture",
        translation_key="calibrated_moisture",
        device_class=SensorDeviceClass.MOISTURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    )

    def __init__(self, coordinator: CalibrationCoordinator) -> None:
        """Name the entity after the pair it calibrates."""
        super().__init__(coordinator, "calibrated_moisture")
        self._attr_name = "Calibrated soil moisture"
        self._attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        """Volumetric water content as a percentage, or ``None`` while uncalibrated.

        Returning ``None`` is the point of the integration: an uncalibrated cheap
        probe has nothing to say about the soil, and publishing a plausible
        number anyway is exactly the failure this project exists to remove.
        """
        data = self.data
        if data is None or data.reading is None:
            return None
        return round(data.reading.moisture * 100.0, 2)

    @property
    def extra_state_attributes(self) -> dict:
        """Everything needed to judge the published value without opening the logs."""
        data = self.data
        if data is None:
            return {}
        attributes: dict = {
            "calibration_status": str(data.status),
            "raw_percent": data.raw_percent,
            "reference_deficit_mm": round(data.deficit_mm, 2) if data.deficit_mm is not None else None,
            "probe_temperature_c": data.probe_temperature_c,
        }
        if data.reading is not None:
            attributes.update(
                {
                    "available_water_percent": round(data.reading.available_fraction * 100.0, 1),
                    "implied_deficit_mm": round(data.reading.deficit_mm, 2),
                    "extrapolated": data.reading.extrapolated,
                    "temperature_corrected": data.reading.temperature_corrected,
                }
            )
        elif data.provisional_moisture is not None:
            attributes["provisional_moisture_percent"] = round(data.provisional_moisture * 100.0, 2)
            attributes["provisional_note"] = "anchors only, not published as a measurement"
        if data.fit is not None:
            attributes.update(
                {
                    "slope": round(data.fit.slope, 6),
                    "intercept": round(data.fit.intercept, 6),
                    "r_squared": round(data.fit.r_squared, 4),
                    "fitted_at": data.fit.fitted_at.isoformat(),
                }
            )
        return attributes


class CalibrationStatusSensor(CalibratorEntity, SensorEntity):
    """Where the calibration stands, and what is missing if it is not calibrated."""

    entity_description = SensorEntityDescription(
        key="calibration_status",
        translation_key="calibration_status",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[str(status) for status in CalibrationStatus],
    )

    def __init__(self, coordinator: CalibrationCoordinator) -> None:
        """Create the diagnostic entity that explains the main one."""
        super().__init__(coordinator, "calibration_status")
        self._attr_name = "Calibration status"
        self._attr_options = [str(status) for status in CalibrationStatus]

    @property
    def native_value(self) -> str | None:
        """The current status as a plain enum value."""
        data = self.data
        return str(data.status) if data else None

    @property
    def extra_state_attributes(self) -> dict:
        """The full picture: evidence collected, gates missing, last refusal, companions."""
        data = self.data
        if data is None:
            return {}
        session = self.coordinator.session
        attributes: dict = {
            "samples": data.sample_count,
            "complete_cycles": data.complete_cycles,
            "cycles_required": session.gates.min_cycles,
            "open_cycle": data.open_cycle_index,
            "last_rejection": str(data.last_rejection) if data.last_rejection else None,
            "rejection_counts": dict(session.rejection_counts),
            "probe_liveness": str(data.liveness),
            "battery_percent": data.battery_percent,
            "irrigation_active": data.irrigation_active,
            # Rain is reported even where no gauge is configured, as
            # ``rain_watched: false``. A user whose samples are being refused
            # needs to be able to tell "it rained" from "nothing is watching".
            "rain_watched": data.rain_watched,
            "raining": data.raining,
            "rain_accumulated_mm": round(data.rain_accumulated_mm, 1),
            "rain_event_mm": (round(data.rain_event_depth_mm, 1) if data.rain_event_depth_mm is not None else None),
            "cycles_by_water_source": dict(data.cycles_by_source),
            "total_available_water_mm": round(session.soil.total_available_water_mm, 1),
            **(data.thresholds.to_dict() if data.thresholds else {}),
            "irrigation_threshold_mm": (
                round(data.irrigation_threshold_mm, 1) if data.irrigation_threshold_mm is not None else None
            ),
            "regime_can_calibrate": (
                data.thresholds.is_reachable_with(data.irrigation_threshold_mm) if data.thresholds else None
            ),
            "soil_texture": str(session.soil.texture),
            "field_capacity": session.soil.field_capacity,
            "wilting_point": session.soil.wilting_point,
            "root_depth_m": session.soil.root_depth_m,
            "discovered": self.coordinator.companions.to_dict(),
        }
        if data.verdict is not None:
            attributes["missing_gates"] = list(data.verdict.failures)
            attributes["progress"] = data.verdict.progress
        if session.invalidation_reason is not None:
            attributes["invalidation_reason"] = str(session.invalidation_reason)
        if data.fit is not None:
            attributes.update(
                {
                    "slope": round(data.fit.slope, 6),
                    "intercept": round(data.fit.intercept, 6),
                    "temperature_coefficient": round(data.fit.temperature_coefficient, 6),
                    "reference_temperature_c": round(data.fit.reference_temperature_c, 1),
                    "r_squared": round(data.fit.r_squared, 4),
                    "rmse": round(data.fit.rmse, 5),
                    "fit_sample_count": data.fit.sample_count,
                    "fit_cycle_count": data.fit.cycle_count,
                    "fit_raw_range": [data.fit.raw_min, data.fit.raw_max],
                    "fitted_at": data.fit.fitted_at.isoformat(),
                }
            )
            offset, gain_deviation = self.coordinator.suggested_device_offset()
            attributes["suggested_device_offset"] = round(offset, 2) if offset is not None else None
            attributes["device_gain_deviation"] = round(gain_deviation, 3) if gain_deviation is not None else None
        return attributes


class ProbePlacementSensor(CalibratorEntity, SensorEntity):
    """Whether the collected cycles suggest the probe is where it should be.

    The fit answers "how much water is in the soil". The cycles quietly answer a
    second question nobody asked them, and before this entity existed the answer
    was thrown away: a placement fault came out as a failed gate named
    ``r_squared``, which tells a user that a regression is unhappy and not that
    the probe is sitting in a gravel pocket.

    It states ``plausible`` at its most confident, never "good". These five
    signatures catch five known ways of being wrong; silence from them is the
    absence of evidence against the installation, not evidence for it.
    """

    entity_description = SensorEntityDescription(
        key="probe_placement",
        translation_key="probe_placement",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[str(confidence) for confidence in PlacementConfidence],
    )

    def __init__(self, coordinator: CalibrationCoordinator) -> None:
        """Create the entity that says whether the probe is in the right place."""
        super().__init__(coordinator, "probe_placement")
        self._attr_name = "Probe placement"
        self._attr_options = [str(confidence) for confidence in PlacementConfidence]

    @property
    def native_value(self) -> str | None:
        """The verdict, or unknown until enough cycles exist to have one."""
        data = self.data
        if data is None or data.placement is None:
            return None
        return str(data.placement.confidence)

    @property
    def extra_state_attributes(self) -> dict:
        """Every suspicion, not only the one that became a repair, with its numbers.

        The repair carries the most severe finding because a notification has to
        be worth reading. This carries all of them, because the user who opens
        the entity is already looking, and a second signature found on the way is
        exactly what saves the next three weeks.
        """
        data = self.data
        if data is None or data.placement is None:
            return {}
        return data.placement.to_dict()


class CalibrationProgressSensor(CalibratorEntity, SensorEntity):
    """How close the probe is to being calibrated, as a single percentage."""

    entity_description = SensorEntityDescription(
        key="calibration_progress",
        translation_key="calibration_progress",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    def __init__(self, coordinator: CalibrationCoordinator) -> None:
        """Create the entity a user watches while the first cycles accumulate."""
        super().__init__(coordinator, "calibration_progress")
        self._attr_name = "Calibration progress"

    @property
    def native_value(self) -> float | None:
        """Progress towards the least satisfied gate."""
        data = self.data
        return data.calibration_progress if data else None


class CompleteCyclesSensor(CalibratorEntity, SensorEntity):
    """Number of irrigation-to-dry-down cycles that counted as evidence."""

    entity_description = SensorEntityDescription(
        key="complete_cycles",
        translation_key="complete_cycles",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    def __init__(self, coordinator: CalibrationCoordinator) -> None:
        """Create the entity that makes the five-cycle requirement visible."""
        super().__init__(coordinator, "complete_cycles")
        self._attr_name = "Complete cycles"

    @property
    def native_value(self) -> int | None:
        """Count of complete cycles."""
        data = self.data
        return data.complete_cycles if data else None

    @property
    def extra_state_attributes(self) -> dict:
        """The cycles themselves, so a refused one can be understood."""
        session = self.coordinator.session
        return {
            "cycles_required": session.gates.min_cycles,
            "cycles": [
                {
                    "index": cycle.index,
                    "opened_at": cycle.opened_at.isoformat(),
                    "closed_at": cycle.closed_at.isoformat() if cycle.closed_at else None,
                    "samples": cycle.sample_count,
                    "deficit_span_mm": round(cycle.deficit_span_mm, 1),
                    "raw_span": round(cycle.raw_span, 1),
                    "counts": cycle.counts_as_evidence(session.cycle_policy, session.soil.total_available_water_mm),
                }
                for cycle in session.tracker.cycles[-10:]
            ],
        }


class RequiredDepletionSensor(CalibratorEntity, SensorEntity):
    """How dry the soil must get, between two irrigations, for a cycle to count.

    The number the integration always knew and used to keep to itself. It exists
    as an entity, and not only as an attribute, so it can sit on a dashboard next
    to the irrigation threshold of the zone: if the threshold is the smaller of
    the two, no cycle will ever count and the calibration will never finish, and
    that is visible at a glance instead of after a month of waiting.
    """

    entity_description = SensorEntityDescription(
        key="required_depletion",
        translation_key="required_depletion",
        native_unit_of_measurement="mm",
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    def __init__(self, coordinator: CalibrationCoordinator) -> None:
        """Create the entity that makes the calibration's own requirement visible."""
        super().__init__(coordinator, "required_depletion")
        self._attr_name = "Required depletion"
        self._attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        """Minimum deficit span of a countable cycle, in millimetres."""
        data = self.data
        if data is None or data.thresholds is None:
            return None
        return round(data.thresholds.min_cycle_span_mm, 1)

    @property
    def extra_state_attributes(self) -> dict:
        """The other two derived thresholds, and how the site compares to them."""
        data = self.data
        if data is None or data.thresholds is None:
            return {}
        thresholds = data.thresholds
        attributes: dict = thresholds.to_dict()
        attributes["irrigation_threshold_mm"] = (
            round(data.irrigation_threshold_mm, 1) if data.irrigation_threshold_mm is not None else None
        )
        reachable = thresholds.is_reachable_with(data.irrigation_threshold_mm)
        attributes["regime_can_calibrate"] = reachable
        if reachable is False:
            attributes["advice"] = (
                f"the irrigation waters at {data.irrigation_threshold_mm:.1f} mm of depletion, "
                f"which is less than the {thresholds.min_cycle_span_mm:.1f} mm a cycle must cover. "
                "Raise the irrigation threshold, or no cycle will ever count."
            )
        return attributes


class CalibrationDriftSensor(CalibratorEntity, SensorEntity):
    """Recent disagreement between the published calibration and the reference.

    A probe does not fail all at once: it corrodes, roots grow past it, the soil
    settles around it. This entity is the early warning, expressed on the same
    percentage scale as the moisture it would spoil.
    """

    entity_description = SensorEntityDescription(
        key="calibration_drift",
        translation_key="calibration_drift",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    def __init__(self, coordinator: CalibrationCoordinator) -> None:
        """Create the drift entity."""
        super().__init__(coordinator, "calibration_drift")
        self._attr_name = "Calibration drift"
        self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        """Residual of the recent samples against the published line, in percentage points."""
        data = self.data
        if data is None or data.drift_rmse is None:
            return None
        return round(data.drift_rmse * 100.0, 3)
