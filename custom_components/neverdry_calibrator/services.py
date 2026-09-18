"""Domain services, dispatched to the right probe across every entry.

Home Assistant registers services per domain, not per config entry, so a call has
to find its target. Probes are addressed by name, which is what the user typed and
what appears on the device, and the lookup spans every configured entry: with two
installations the service must still reach the probe that belongs to the other
one, rather than silently missing it.

Services that make sense for a whole installation accept no probe and fan out;
services that throw away data require one explicitly, because a reset that hits
every probe by accident costs weeks of collected cycles.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_OFFSET,
    ATTR_PROBE,
    CONF_ALLOW_DEVICE_WRITEBACK,
    DOMAIN,
    SERVICE_APPLY_DEVICE_OFFSET,
    SERVICE_CALIBRATE_NOW,
    SERVICE_EXPORT_SAMPLES,
    SERVICE_MARK_FIELD_CAPACITY,
    SERVICE_RESET_CALIBRATION,
)
from .coordinator import CalibrationCoordinator

_LOGGER = logging.getLogger(__name__)

OPTIONAL_PROBE_SCHEMA = vol.Schema({vol.Optional(ATTR_PROBE): cv.string})
REQUIRED_PROBE_SCHEMA = vol.Schema({vol.Required(ATTR_PROBE): cv.string})
OFFSET_SCHEMA = REQUIRED_PROBE_SCHEMA.extend({vol.Optional(ATTR_OFFSET): vol.Coerce(float)})


def all_coordinators(hass: HomeAssistant) -> list[CalibrationCoordinator]:
    """Every probe of every configured entry, in setup order."""
    coordinators: list[CalibrationCoordinator] = []
    for entry_runtime in hass.data.get(DOMAIN, {}).values():
        coordinators.extend(entry_runtime.values())
    return coordinators


def _resolve(hass: HomeAssistant, call: ServiceCall, *, required: bool) -> list[CalibrationCoordinator]:
    """Find the probes a call targets, by name or by identifier.

    Matching is case-insensitive on the name and exact on the identifier, so a
    call written against a probe called "Melino" keeps working after the name is
    displayed differently, and an automation may address the stable id instead.
    """
    everything = all_coordinators(hass)
    if not everything:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_probe_configured")

    wanted = call.data.get(ATTR_PROBE)
    if wanted is None:
        if required:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="probe_name_required")
        return everything

    needle = str(wanted).strip().lower()
    matches = [
        coordinator
        for coordinator in everything
        if coordinator.probe.name.lower() == needle or coordinator.probe.probe_id == needle
    ]
    if not matches:
        known = ", ".join(sorted(coordinator.probe.name for coordinator in everything))
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="unknown_probe",
            translation_placeholders={"wanted": str(wanted), "known": known},
        )
    return matches


def async_register_services(hass: HomeAssistant) -> None:
    """Register the domain services once, on the first entry that is set up."""
    if hass.services.has_service(DOMAIN, SERVICE_CALIBRATE_NOW):
        return

    async def _calibrate_now(call: ServiceCall) -> None:
        """Force a refit on one probe, or on all of them."""
        for coordinator in _resolve(hass, call, required=False):
            verdict = await coordinator.async_calibrate_now()
            _LOGGER.info(
                "%s: refit requested, passed=%s failures=%s",
                coordinator.probe.name,
                verdict.passed,
                verdict.failures,
            )

    async def _reset_calibration(call: ServiceCall) -> None:
        """Throw away the calibration and its evidence for one named probe."""
        for coordinator in _resolve(hass, call, required=True):
            await coordinator.async_reset_calibration()
            _LOGGER.warning("%s: calibration reset, all samples and cycles dropped", coordinator.probe.name)

    async def _mark_field_capacity(call: ServiceCall) -> None:
        """Record the current reading of one probe as a field capacity anchor."""
        for coordinator in _resolve(hass, call, required=True):
            if not await coordinator.async_mark_field_capacity():
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="probe_not_reporting",
                    translation_placeholders={"name": coordinator.probe.name},
                )

    async def _apply_device_offset(call: ServiceCall) -> None:
        """Write an offset into one probe's own calibration entity, if allowed."""
        for coordinator in _resolve(hass, call, required=True):
            if not coordinator.entry.options.get(CONF_ALLOW_DEVICE_WRITEBACK, False):
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="device_writeback_disabled",
                )
            applied = await coordinator.async_apply_device_offset(call.data.get(ATTR_OFFSET))
            _LOGGER.warning(
                "%s: wrote offset %.2f to the probe; the collected samples were dropped",
                coordinator.probe.name,
                applied,
            )

    async def _export_samples(call: ServiceCall) -> ServiceResponse:
        """Return the evidence behind one calibration, or behind all of them."""
        return {
            coordinator.probe.name: coordinator.export_samples() for coordinator in _resolve(hass, call, required=False)
        }

    hass.services.async_register(DOMAIN, SERVICE_CALIBRATE_NOW, _calibrate_now, schema=OPTIONAL_PROBE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESET_CALIBRATION, _reset_calibration, schema=REQUIRED_PROBE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_MARK_FIELD_CAPACITY, _mark_field_capacity, schema=REQUIRED_PROBE_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_APPLY_DEVICE_OFFSET, _apply_device_offset, schema=OFFSET_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_SAMPLES,
        _export_samples,
        schema=OPTIONAL_PROBE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def async_remove_services(hass: HomeAssistant) -> None:
    """Drop the domain services when the last entry goes away."""
    for service in (
        SERVICE_CALIBRATE_NOW,
        SERVICE_RESET_CALIBRATION,
        SERVICE_MARK_FIELD_CAPACITY,
        SERVICE_APPLY_DEVICE_OFFSET,
        SERVICE_EXPORT_SAMPLES,
    ):
        hass.services.async_remove(DOMAIN, service)
