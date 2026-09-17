"""Diagnostics download: every probe of the installation, minus nothing that matters.

Nothing collected here is personal: raw probe indices, water deficits and
temperatures of a patch of soil. The download is therefore the full picture
rather than a redacted one, which is what makes a field bug report actionable,
with each sample list capped so the file stays openable.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import CalibrationCoordinator

#: Most recent samples included per probe.
SAMPLE_LIMIT: int = 500


def _probe_diagnostics(coordinator: CalibrationCoordinator) -> dict[str, Any]:
    """Configuration, discovery, current publication and calibration of one probe."""
    export = coordinator.export_samples()
    export["samples"] = export["samples"][-SAMPLE_LIMIT:]
    data = coordinator.data
    return {
        "probe": {"id": coordinator.probe.probe_id, "name": coordinator.probe.name, **coordinator.probe.settings},
        "companions": coordinator.companions.to_dict(),
        "published": {
            "status": str(data.status) if data else None,
            "raw_percent": data.raw_percent if data else None,
            "deficit_mm": data.deficit_mm if data else None,
            "moisture_percent": round(data.reading.moisture * 100.0, 2) if data and data.reading else None,
            "progress": data.calibration_progress if data else None,
            "complete_cycles": data.complete_cycles if data else None,
            "cycles_by_water_source": dict(data.cycles_by_source) if data else None,
        },
        "rain": {
            "entity": coordinator.rain_entity,
            "sheltered": coordinator.probe.sheltered_from_rain,
            "raining": data.raining if data else None,
            "accumulated_mm": data.rain_accumulated_mm if data else None,
            "event_mm": data.rain_event_depth_mm if data else None,
        },
        "calibration": export,
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return the tuning of the installation and the full state of every probe."""
    runtime: dict[str, CalibrationCoordinator] = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": {"title": entry.title, "options": dict(entry.options)},
        "probes": {coordinator.probe.name: _probe_diagnostics(coordinator) for coordinator in runtime.values()},
    }
