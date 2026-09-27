"""Two questions an automation actually asks: is the probe alive, and can I trust it.

Both are diagnostics rather than measurements, and both exist because the failure
modes of a cheap probe are silent. A flat battery keeps the last value on screen,
and a drifting calibration keeps producing numbers in the right range. Neither
shows up as an unavailable entity, so each gets an explicit witness.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import CalibrationCoordinator
from .entity import CalibratorEntity
from .model import CalibrationStatus, ProbeLiveness


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the liveness and attention entities of every configured probe."""
    runtime: dict[str, CalibrationCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for coordinator in runtime.values():
        entities.extend([ProbeOnlineBinarySensor(coordinator), CalibrationProblemBinarySensor(coordinator)])
    async_add_entities(entities)


class ProbeOnlineBinarySensor(CalibratorEntity, BinarySensorEntity):
    """Whether the probe's device is still reporting, judged against its own cadence.

    Deliberately still ``on`` for a probe whose electrode has stopped while the
    device keeps talking. That probe *is* online, and saying otherwise would make
    this entity mean two things at once and be useful for neither. The stall is
    reported in the attributes here and carried by the attention entity below,
    which is where a consumer looks to find out that the reading cannot be used.
    """

    entity_description = BinarySensorEntityDescription(
        key="probe_online",
        translation_key="probe_online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    def __init__(self, coordinator: CalibrationCoordinator) -> None:
        """Create the liveness entity."""
        super().__init__(coordinator, "probe_online")
        self._attr_name = "Probe online"

    @property
    def is_on(self) -> bool | None:
        """On while the device is alive; unknown when there is no sentinel to read.

        With neither a resolvable device nor a temperature channel the question
        cannot be answered honestly, so the entity stays unknown rather than
        claiming the probe is fine because nothing said otherwise.
        """
        data = self.data
        if data is None:
            return None
        if data.liveness is ProbeLiveness.UNKNOWN:
            return None if data.raw_percent is None else True
        return data.liveness is ProbeLiveness.ALIVE

    @property
    def extra_state_attributes(self) -> dict:
        """What the verdict was based on."""
        data = self.data
        if data is None:
            return {}
        return {
            "liveness": str(data.liveness),
            # Both numbers or neither: an age without its bar invites the reader to
            # compare it with a constant they invented.
            "device_silence_s": round(data.device_age_s) if data.device_age_s is not None else None,
            "silence_allowed_s": round(data.liveness_bar_s) if data.liveness_bar_s is not None else None,
            "device_entities": list(self.coordinator.companions.device_entities),
            "sentinel_entity": self.coordinator.companions.probe_temperature,
            "probe_temperature_c": data.probe_temperature_c,
            "battery_percent": data.battery_percent,
            "sensing_stalled": data.sensing_stalled,
        }


class CalibrationProblemBinarySensor(CalibratorEntity, BinarySensorEntity):
    """Whether the calibration needs attention: drifting, invalidated or blind."""

    entity_description = BinarySensorEntityDescription(
        key="calibration_problem",
        translation_key="calibration_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    #: Statuses that mean a human should look. ``COLLECTING`` is not one of them:
    #: a probe that is still learning is working as designed.
    PROBLEM_STATUSES = frozenset(
        {CalibrationStatus.DRIFTING, CalibrationStatus.INVALIDATED, CalibrationStatus.PROBE_OFFLINE}
    )

    def __init__(self, coordinator: CalibrationCoordinator) -> None:
        """Create the attention entity."""
        super().__init__(coordinator, "calibration_problem")
        self._attr_name = "Calibration problem"

    @property
    def is_on(self) -> bool | None:
        """On when the published calibration cannot be relied on as it stands."""
        data = self.data
        if data is None:
            return None
        return data.status in self.PROBLEM_STATUSES

    @property
    def extra_state_attributes(self) -> dict:
        """Which problem it is, in the terms the status entity uses."""
        data = self.data
        if data is None:
            return {}
        session = self.coordinator.session
        return {
            "status": str(data.status),
            "drift_percent": round(data.drift_rmse * 100.0, 3) if data.drift_rmse is not None else None,
            "invalidation_reason": str(session.invalidation_reason) if session.invalidation_reason else None,
            # Which of the two silent failures is behind a ``probe_offline``
            # status. The status cannot say, because one status is what a consumer
            # of this integration needs and two is what a human fixing it needs.
            "sensing_stalled": data.sensing_stalled,
            "stalled_travel_mm": round(data.stalled_travel_mm, 1),
            "stalled_travel_required_mm": round(data.stalled_travel_required_mm, 1),
        }
