"""NeverDry Calibrator: turn cheap soil probes into calibrated moisture sensors.

A capacitive probe that costs a few euros publishes an index from 0 to 100 that
is monotone in soil water and otherwise arbitrary. This integration pairs each
such probe with a water deficit computed by a scientific model in another
integration, watches several irrigation-to-dry-down cycles, and learns the map
between the two so that the probe can finally be read as soil moisture.

The integration is a **hub**: one entry for the installation, holding a list of
probes, exactly as NeverDry holds its zones. Everything is in one place, and each
probe still gets its own device, its own entities and its own sample history.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PROBE_TEMPERATURE_ENTITY,
    DOMAIN,
    PLATFORMS,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)
from .coordinator import CalibrationCoordinator
from .discovery import CompanionEntities, discover_companions
from .model import CalibrationSession, SoilProfile
from .model.soil import InvalidSoilProfile
from .probe import ProbeConfig, probes_of
from .services import async_register_services, async_remove_services
from .settings import admission_policy, cycle_policy, merged_settings, quality_gates, soil_profile

_LOGGER = logging.getLogger(__name__)

#: Runtime of one entry: the coordinator of each probe, keyed by probe id.
#: A plain alias rather than a ``type`` statement, so the architectural tests can
#: parse this file with any Python the developer happens to have.
EntryRuntime = dict[str, CalibrationCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up every probe of the entry: restore its history, find its companions."""
    runtime: EntryRuntime = {}
    for probe in probes_of(dict(entry.data)):
        try:
            runtime[probe.probe_id] = await _build_coordinator(hass, entry, probe)
        except InvalidSoilProfile as err:
            raise HomeAssistantError(f"probe {probe.name} has an invalid soil configuration: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    for coordinator in runtime.values():
        await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    async_register_services(hass)
    return True


async def _build_coordinator(
    hass: HomeAssistant,
    entry: ConfigEntry,
    probe: ProbeConfig,
) -> CalibrationCoordinator:
    """Assemble one probe: soil, stored session, discovered companions, coordinator."""
    tuning = merged_settings(entry)
    soil = soil_profile(probe.soil_settings())

    store = _store_for(hass, entry.entry_id, probe.probe_id)
    stored = await store.async_load() or {}

    session = _restore_session(stored, soil, probe.name)
    session.admission_policy = admission_policy(tuning)
    session.cycle_policy = cycle_policy(tuning)
    session.gates = quality_gates(tuning)
    session.tracker.policy = session.cycle_policy
    session.apply_soil(soil, dt_util.utcnow())

    companions = _resolve_companions(hass, probe)

    coordinator = CalibrationCoordinator(
        hass=hass,
        entry=entry,
        probe=probe,
        session=session,
        store=store,
        companions=companions,
        stored_snapshot=stored.get("calibration_snapshot"),
    )
    await coordinator.async_prepare()
    return coordinator


def _store_for(hass: HomeAssistant, entry_id: str, probe_id: str) -> Store:
    """The sample store of one probe."""
    return Store(hass, STORAGE_VERSION, STORAGE_KEY_TEMPLATE.format(entry_id=entry_id, probe_id=probe_id))


def _restore_session(stored: dict, soil: SoilProfile, probe_name: str) -> CalibrationSession:
    """Rebuild a persisted session, starting fresh when the payload is unusable.

    A store that cannot be read costs the user their sample history, which is
    weeks of waiting, so the failure is logged loudly. It must never cost them
    the integration, so it is not raised.
    """
    payload = stored.get("session")
    if not payload:
        return CalibrationSession(soil=soil)
    try:
        return CalibrationSession.from_dict(payload)
    except (KeyError, TypeError, ValueError):
        _LOGGER.exception("Stored calibration of %s could not be read; starting a new one", probe_name)
        return CalibrationSession(soil=soil)


def _resolve_companions(hass: HomeAssistant, probe: ProbeConfig) -> CompanionEntities:
    """Discover the probe's companions, letting an explicit temperature win.

    Discovery is redone at every setup rather than stored: a device that gains a
    temperature channel after a firmware update should be picked up on the next
    restart, and nothing here is worth a write to the config entry.
    """
    companions = discover_companions(hass, probe.moisture_entity)
    override = probe.probe_temperature_entity
    if not override or override == companions.probe_temperature:
        return companions
    return CompanionEntities(
        device_id=companions.device_id,
        probe_temperature=override,
        battery=companions.battery,
        temperature_calibration=companions.temperature_calibration,
        humidity_calibration=companions.humidity_calibration,
        soil_calibration=companions.soil_calibration,
        other_calibration=companions.other_calibration,
    )


def coordinators_of(hass: HomeAssistant, entry: ConfigEntry) -> EntryRuntime:
    """The runtime of one entry, for the platforms to iterate."""
    return hass.data.get(DOMAIN, {}).get(entry.entry_id, {})


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Persist what every probe learned, then tear the entry down."""
    for coordinator in coordinators_of(hass, entry).values():
        await coordinator.async_save_now()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            async_remove_services(hass)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after a probe was added, edited or removed, or the tuning changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete every sample store of the entry: the history has no other owner."""
    for probe in probes_of(dict(entry.data)):
        await _store_for(hass, entry.entry_id, probe.probe_id).async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: dr.DeviceEntry,
) -> bool:
    """Allow deleting the device of a probe that is no longer configured.

    Removing a probe is done in the options, and it leaves its device behind
    until Home Assistant is told the device may go. Refusing to delete the device
    of a probe that still exists is deliberate: the device is how the user reads
    that probe, and deleting it from the device page would be a confusing way to
    unconfigure it.
    """
    configured = {f"{entry.entry_id}_{probe.probe_id}" for probe in probes_of(dict(entry.data))}
    configured.add(entry.entry_id)
    return not any(identifier[1] in configured for identifier in device.identifiers if identifier[0] == DOMAIN)


async def async_remove_probe_store(hass: HomeAssistant, entry: ConfigEntry, probe_id: str) -> None:
    """Delete the history of a probe that has just been removed from the entry."""
    await _store_for(hass, entry.entry_id, probe_id).async_remove()


__all__ = [
    "CONF_PROBE_TEMPERATURE_ENTITY",
    "async_remove_probe_store",
    "async_setup_entry",
    "async_unload_entry",
    "coordinators_of",
]
