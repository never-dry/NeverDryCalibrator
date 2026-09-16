"""Finding the entities that travel with a probe, without asking the user for them.

A cheap soil probe never arrives alone. The same device publishes a temperature,
usually a battery level, and on Tuya-style hardware a handful of calibration
knobs the user can turn from the device page. The user should not have to name
any of them: they are already related to the moisture entity by the device
registry, and an integration that asks for information Home Assistant already
holds is asking the user to make a mistake.

Two of those companions change what this integration does:

* the **temperature** channel is the liveness sentinel. A probe with a flat
  battery keeps publishing its last moisture value indefinitely, so the only
  cheap way to notice is that its temperature stopped arriving.
* the **calibration knobs** are read, not written, by default. If the user turns
  one, every raw reading collected before that moment describes a different
  instrument, and the calibration must be invalidated rather than quietly
  continued.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import ATTR_DEVICE_CLASS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CALIBRATION_KEYWORDS,
    HUMIDITY_KEYWORDS,
    SOIL_KEYWORDS,
    TEMPERATURE_KEYWORDS,
    THRESHOLD_KEYWORDS,
)


@dataclass(frozen=True, slots=True)
class CompanionEntities:
    """Everything found on the probe's device, classified by the role it can play."""

    device_id: str | None = None
    probe_temperature: str | None = None
    battery: str | None = None
    temperature_calibration: str | None = None
    humidity_calibration: str | None = None
    soil_calibration: str | None = None
    other_calibration: tuple[str, ...] = field(default_factory=tuple)

    @property
    def calibration_entities(self) -> tuple[str, ...]:
        """Every device-side calibration knob found, in a stable order.

        Stable because the tuple is snapshotted and compared across restarts to
        detect that the user turned one of them.
        """
        found = [
            self.temperature_calibration,
            self.humidity_calibration,
            self.soil_calibration,
            *self.other_calibration,
        ]
        return tuple(sorted({entity_id for entity_id in found if entity_id}))

    @property
    def moisture_calibration(self) -> str | None:
        """The knob an offset would be written to: the soil one, else the humidity one.

        Devices that expose both mean different things by them. The soil knob is
        the one wired to the probe electrode, so it is preferred whenever it
        exists.
        """
        return self.soil_calibration or self.humidity_calibration

    def to_dict(self) -> dict:
        """Serialize for diagnostics and for the config entry."""
        return {
            "device_id": self.device_id,
            "probe_temperature": self.probe_temperature,
            "battery": self.battery,
            "temperature_calibration": self.temperature_calibration,
            "humidity_calibration": self.humidity_calibration,
            "soil_calibration": self.soil_calibration,
            "other_calibration": list(self.other_calibration),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> CompanionEntities:
        """Rebuild from a config entry payload, tolerating a missing or partial one."""
        if not data:
            return cls()
        return cls(
            device_id=data.get("device_id"),
            probe_temperature=data.get("probe_temperature"),
            battery=data.get("battery"),
            temperature_calibration=data.get("temperature_calibration"),
            humidity_calibration=data.get("humidity_calibration"),
            soil_calibration=data.get("soil_calibration"),
            other_calibration=tuple(data.get("other_calibration", [])),
        )


def _label(hass: HomeAssistant, entry: er.RegistryEntry) -> str:
    """Lowercased haystack for keyword matching: entity id plus whatever name exists."""
    state = hass.states.get(entry.entity_id)
    friendly = state.name if state else None
    parts = [entry.entity_id, entry.name or "", entry.original_name or "", friendly or ""]
    return " ".join(parts).lower()


def _device_class(hass: HomeAssistant, entry: er.RegistryEntry) -> str | None:
    """Device class of an entity, preferring the registry override over the state."""
    if entry.device_class:
        return entry.device_class
    if entry.original_device_class:
        return entry.original_device_class
    state = hass.states.get(entry.entity_id)
    if state:
        return state.attributes.get(ATTR_DEVICE_CLASS)
    return None


def _matches(haystack: str, keywords: tuple[str, ...]) -> bool:
    """Whether any keyword appears in the haystack."""
    return any(keyword in haystack for keyword in keywords)


def discover_companions(hass: HomeAssistant, moisture_entity_id: str) -> CompanionEntities:
    """Find the temperature, battery and calibration entities of the probe's device.

    Returns an empty result rather than raising when the moisture entity is not
    registered or belongs to no device, which is the normal case for a template
    sensor or a manually created helper. Discovery failing must degrade the
    integration, never block it: the calibration itself only needs the raw index
    and the deficit.
    """
    registry = er.async_get(hass)
    source = registry.async_get(moisture_entity_id)
    if source is None or source.device_id is None:
        return CompanionEntities()

    probe_temperature: str | None = None
    battery: str | None = None
    temperature_calibration: str | None = None
    humidity_calibration: str | None = None
    soil_calibration: str | None = None
    others: list[str] = []

    for entry in er.async_entries_for_device(registry, source.device_id, include_disabled_entities=False):
        if entry.entity_id == moisture_entity_id:
            continue
        domain = entry.entity_id.split(".", 1)[0]
        haystack = _label(hass, entry)
        device_class = _device_class(hass, entry)

        if _matches(haystack, CALIBRATION_KEYWORDS) and domain in (NUMBER_DOMAIN, SENSOR_DOMAIN):
            if _matches(haystack, SOIL_KEYWORDS) and soil_calibration is None:
                soil_calibration = entry.entity_id
            elif _matches(haystack, TEMPERATURE_KEYWORDS) and temperature_calibration is None:
                temperature_calibration = entry.entity_id
            elif _matches(haystack, HUMIDITY_KEYWORDS) and humidity_calibration is None:
                humidity_calibration = entry.entity_id
            else:
                others.append(entry.entity_id)
            continue

        if domain == SENSOR_DOMAIN and device_class == "temperature" and probe_temperature is None:
            probe_temperature = entry.entity_id
            continue

        if domain in (SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN) and device_class == "battery" and battery is None:
            battery = entry.entity_id

    return CompanionEntities(
        device_id=source.device_id,
        probe_temperature=probe_temperature,
        battery=battery,
        temperature_calibration=temperature_calibration,
        humidity_calibration=humidity_calibration,
        soil_calibration=soil_calibration,
        other_calibration=tuple(others),
    )


def discover_irrigation_threshold(hass: HomeAssistant, deficit_entity_id: str) -> str | None:
    """Find the irrigation threshold published alongside a zone deficit.

    A water balance integration that exposes a deficit usually exposes, on the
    same zone device, the depletion at which it decides to irrigate. That number
    is what determines whether a cycle can ever be large enough to calibrate
    against, so it is worth finding without asking: a site whose threshold sits
    below the minimum useful depletion will never produce a countable cycle, and
    would otherwise wait for weeks to discover it.

    Returns ``None`` when the deficit belongs to no device or the device publishes
    no threshold, which is the normal case for a template sensor.
    """
    registry = er.async_get(hass)
    source = registry.async_get(deficit_entity_id)
    if source is None or source.device_id is None:
        return None
    for entry in er.async_entries_for_device(registry, source.device_id, include_disabled_entities=False):
        if entry.entity_id == deficit_entity_id or not entry.entity_id.startswith(f"{SENSOR_DOMAIN}."):
            continue
        if _matches(_label(hass, entry), THRESHOLD_KEYWORDS):
            return entry.entity_id
    return None


def snapshot_calibration_values(hass: HomeAssistant, entity_ids: tuple[str, ...]) -> dict[str, str]:
    """Current value of every device-side calibration knob, as strings.

    Strings rather than floats on purpose: the comparison is for equality across
    restarts, and a float round-tripped through JSON storage is not reliably
    equal to itself.
    """
    snapshot: dict[str, str] = {}
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is not None and state.state not in ("unknown", "unavailable"):
            snapshot[entity_id] = state.state
    return snapshot
