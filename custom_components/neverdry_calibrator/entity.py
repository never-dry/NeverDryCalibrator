"""Common base for the entities of one probe, and for the hub they belong to.

Each probe is its own device, named as the user named it, attached to the
integration hub through ``via_device``. That is what gives the two views people
actually use: one integration card holding the whole installation, and one device
per probe with its eight entities.

All entities read the same published snapshot and hold no state of their own.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CalibrationCoordinator


def hub_device_info(entry: ConfigEntry) -> DeviceInfo:
    """The device representing the installation itself."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="NeverDry",
        model="Probe calibration hub",
    )


def probe_device_info(coordinator: CalibrationCoordinator) -> DeviceInfo:
    """The device of one calibrated probe, hanging from the hub.

    Keyed by the probe identifier and not by its name, so renaming a probe
    renames the device instead of creating a second one and orphaning the first.
    """
    entry = coordinator.entry
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{coordinator.probe.probe_id}")},
        name=coordinator.probe.name,
        manufacturer="NeverDry",
        model="Calibrated soil probe",
        via_device=(DOMAIN, entry.entry_id),
    )


class CalibratorEntity(CoordinatorEntity[CalibrationCoordinator]):
    """An entity of one calibrated probe."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CalibrationCoordinator, key: str) -> None:
        """Bind the entity to its probe and give it a stable unique id."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{coordinator.probe.probe_id}_{key}"
        self._attr_device_info = probe_device_info(coordinator)

    @property
    def data(self):
        """The current published snapshot, or ``None`` before the first refresh."""
        return self.coordinator.data
