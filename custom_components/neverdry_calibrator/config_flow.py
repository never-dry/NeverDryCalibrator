"""Config and options flow: one integration entry, with the probes inside it.

The shape mirrors NeverDry: the installation is a single entry, and probes are
added, edited and removed from the options menu of that entry. Everything about
the calibration therefore lives in one place, and each probe still owns a device
and a history of its own.

Validation here is about *refusing to start wrong*, not about being thorough. A
probe that does not publish a number, a deficit in a unit nobody can convert, or
a soil whose wilting point sits above its field capacity are all failures that
would otherwise surface weeks later as a calibration that never converges.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOW_DEVICE_WRITEBACK,
    CONF_AMBIENT_TEMPERATURE_ENTITY,
    CONF_DEFICIT_ENTITY,
    CONF_DRAINAGE_MINUTES,
    CONF_FIELD_CAPACITY,
    CONF_IRRIGATION_ENTITY,
    CONF_MIN_CYCLES,
    CONF_MIN_R_SQUARED,
    CONF_MIN_RAW_SPAN,
    CONF_MIN_SAMPLE_INTERVAL,
    CONF_MIN_SAMPLES,
    CONF_MIN_SOIL_TEMPERATURE,
    CONF_MOISTURE_ENTITY,
    CONF_PROBE_TEMPERATURE_ENTITY,
    CONF_PROBE_TIMEOUT,
    CONF_PROBES,
    CONF_ROOT_DEPTH,
    CONF_ROOT_DEPTH_UNIT,
    CONF_SATURATION,
    CONF_SOIL_TEXTURE,
    CONF_WILTING_POINT,
    DEFAULT_ROOT_DEPTH_CM,
    DEFAULT_TITLE,
    DOMAIN,
    SOIL_DOC_URL,
)
from .discovery import discover_companions
from .model import AdmissionPolicy, QualityGates, SoilTexture, deficit_to_mm
from .probe import ProbeConfig, new_probe_id, probes_of, records_of
from .settings import validate_soil

#: Entity domains that can tell the integration water is being delivered.
IRRIGATION_DOMAINS = ["switch", "valve", "binary_sensor", "input_boolean"]

#: Field carrying the "add another probe" answer of the initial flow.
CONF_ADD_ANOTHER = "add_another"

#: Field carrying the probe picked in an edit or remove step.
CONF_SELECTED_PROBE = "selected_probe"

#: Field carrying the confirmation of a removal.
CONF_CONFIRM_REMOVAL = "confirm_removal"

SOIL_TEXTURE_LABELS: dict[str, str] = {
    SoilTexture.AUTO: "Automatic (a middle soil)",
    SoilTexture.SANDY: "Sandy / light, drains fast",
    SoilTexture.LOAM: "Loam / medium",
    SoilTexture.CLAY: "Clay / heavy, holds water",
    SoilTexture.CUSTOM: "Custom (set the values below)",
}


def _entity_selector(domain: str | list[str]) -> selector.EntitySelector:
    """Entity picker restricted to one or more domains."""
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))


def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> selector.NumberSelector:
    """Numeric box with explicit bounds, so a typo is refused by the form itself.

    The unit is omitted rather than passed as ``None`` when there is none: the
    selector schema expects a string and rejects the explicit null.
    """
    config: dict = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit is not None:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(config))


def _probe_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Form for one probe: its name, its two sources, and the optional helpers."""
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            vol.Required(CONF_MOISTURE_ENTITY, default=defaults.get(CONF_MOISTURE_ENTITY)): _entity_selector("sensor"),
            vol.Required(CONF_DEFICIT_ENTITY, default=defaults.get(CONF_DEFICIT_ENTITY)): _entity_selector("sensor"),
            vol.Optional(
                CONF_AMBIENT_TEMPERATURE_ENTITY,
                description={"suggested_value": defaults.get(CONF_AMBIENT_TEMPERATURE_ENTITY)},
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_IRRIGATION_ENTITY,
                description={"suggested_value": defaults.get(CONF_IRRIGATION_ENTITY)},
            ): _entity_selector(IRRIGATION_DOMAINS),
            vol.Optional(
                CONF_PROBE_TEMPERATURE_ENTITY,
                description={"suggested_value": defaults.get(CONF_PROBE_TEMPERATURE_ENTITY)},
            ): _entity_selector("sensor"),
        }
    )


def _soil_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Form for the reservoir: a texture preset, a depth, and the custom overrides."""
    return vol.Schema(
        {
            vol.Required(CONF_SOIL_TEXTURE, default=defaults.get(CONF_SOIL_TEXTURE, SoilTexture.AUTO)): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=str(value), label=label)
                            for value, label in SOIL_TEXTURE_LABELS.items()
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            ),
            vol.Required(CONF_ROOT_DEPTH, default=defaults.get(CONF_ROOT_DEPTH, DEFAULT_ROOT_DEPTH_CM)): _number(
                1.0, 300.0, 1.0
            ),
            vol.Required(CONF_ROOT_DEPTH_UNIT, default=defaults.get(CONF_ROOT_DEPTH_UNIT, "cm")): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="cm", label="centimetres"),
                            selector.SelectOptionDict(value="in", label="inches"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            ),
            vol.Optional(
                CONF_FIELD_CAPACITY, description={"suggested_value": defaults.get(CONF_FIELD_CAPACITY)}
            ): _number(0.05, 0.60, 0.01),
            vol.Optional(
                CONF_WILTING_POINT, description={"suggested_value": defaults.get(CONF_WILTING_POINT)}
            ): _number(0.01, 0.40, 0.01),
            vol.Optional(CONF_SATURATION, description={"suggested_value": defaults.get(CONF_SATURATION)}): _number(
                0.20, 0.95, 0.01
            ),
        }
    )


def validate_probe_sources(
    hass: HomeAssistant,
    user_input: dict[str, Any],
    existing: list[ProbeConfig],
    editing_id: str | None = None,
) -> dict[str, str]:
    """Check that the probe reads 0 to 100, the deficit converts, and nothing collides.

    An entity that is currently unavailable passes: a battery probe can be asleep
    during setup, and refusing it would send the user away to come back later
    with exactly the same configuration.
    """
    errors: dict[str, str] = {}

    moisture = hass.states.get(user_input[CONF_MOISTURE_ENTITY])
    if moisture is not None and moisture.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        try:
            value = float(moisture.state)
        except (TypeError, ValueError):
            errors[CONF_MOISTURE_ENTITY] = "moisture_not_numeric"
        else:
            if not 0.0 <= value <= 100.0:
                errors[CONF_MOISTURE_ENTITY] = "moisture_out_of_range"

    deficit = hass.states.get(user_input[CONF_DEFICIT_ENTITY])
    if deficit is not None and deficit.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        try:
            float(deficit.state)
        except (TypeError, ValueError):
            errors[CONF_DEFICIT_ENTITY] = "deficit_not_numeric"
        else:
            if deficit_to_mm(1.0, deficit.attributes.get(ATTR_UNIT_OF_MEASUREMENT)) is None:
                errors[CONF_DEFICIT_ENTITY] = "deficit_unit_unknown"

    if user_input[CONF_MOISTURE_ENTITY] == user_input[CONF_DEFICIT_ENTITY]:
        errors[CONF_DEFICIT_ENTITY] = "same_entity"

    others = [probe for probe in existing if probe.probe_id != editing_id]
    name = str(user_input.get(CONF_NAME, "")).strip()
    if any(probe.name.lower() == name.lower() for probe in others):
        errors[CONF_NAME] = "probe_already_exists"
    if any(probe.moisture_entity == user_input[CONF_MOISTURE_ENTITY] for probe in others):
        errors[CONF_MOISTURE_ENTITY] = "probe_already_configured"

    return errors


def _companion_placeholders(hass: HomeAssistant, moisture_entity: str) -> dict[str, str]:
    """What discovery found on the probe's device, for the confirmation screen."""
    companions = discover_companions(hass, moisture_entity)
    return {
        "probe_temperature": companions.probe_temperature or "not found",
        "battery": companions.battery or "not found",
        "temperature_calibration": companions.temperature_calibration or "not found",
        "humidity_calibration": companions.humidity_calibration or "not found",
        "soil_calibration": companions.soil_calibration or "not found",
    }


class NeverDryCalibratorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single entry, with as many probes as the user wants to add now."""

    VERSION = 1

    def __init__(self) -> None:
        """Start with an empty installation and an empty probe draft."""
        self._probes: list[ProbeConfig] = []
        self._draft: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NeverDryCalibratorOptionsFlow:
        """Expose the options flow, which is where probes are managed afterwards."""
        return NeverDryCalibratorOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the first probe: its name, its raw sensor and its deficit."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_probe_sources(self.hass, user_input, self._probes)
            if not errors:
                self._draft = dict(user_input)
                return await self.async_step_soil()

        return self.async_show_form(
            step_id="user",
            data_schema=_probe_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_soil(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the reservoir the deficit of this probe is defined against."""
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {**self._draft, **user_input}
            error = validate_soil(candidate)
            if error:
                errors["base"] = error
            else:
                self._draft = candidate
                return await self.async_step_confirm()

        return self.async_show_form(
            step_id="soil",
            data_schema=_soil_schema(user_input or {}),
            errors=errors,
            description_placeholders={"soil_doc": SOIL_DOC_URL},
        )

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show what was discovered on the probe's device, then bank the probe."""
        if user_input is not None:
            self._bank_draft()
            return await self.async_step_add_another()

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=_companion_placeholders(self.hass, self._draft[CONF_MOISTURE_ENTITY]),
        )

    def _bank_draft(self) -> None:
        """Turn the current draft into a probe of the installation."""
        settings = {key: value for key, value in self._draft.items() if key != CONF_NAME}
        name = str(self._draft[CONF_NAME]).strip()
        probe_id = new_probe_id(name, {probe.probe_id for probe in self._probes})
        self._probes.append(ProbeConfig(probe_id=probe_id, name=name, settings=settings))
        self._draft = {}

    async def async_step_add_another(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer to configure another probe before finishing."""
        if user_input is not None:
            if user_input.get(CONF_ADD_ANOTHER):
                return await self.async_step_user()
            return self.async_create_entry(
                title=DEFAULT_TITLE,
                data={CONF_PROBES: records_of(self._probes)},
            )

        return self.async_show_form(
            step_id="add_another",
            data_schema=vol.Schema({vol.Required(CONF_ADD_ANOTHER, default=False): selector.BooleanSelector()}),
            description_placeholders={
                "count": str(len(self._probes)),
                "names": ", ".join(probe.name for probe in self._probes),
            },
        )


class NeverDryCalibratorOptionsFlow(OptionsFlow):
    """Manage the installation: its probes, its sampling rules and its gates."""

    def __init__(self) -> None:
        """Start with no probe selected and no draft in flight."""
        self._draft: dict[str, Any] = {}
        self._selected: str | None = None

    # ── Menu ─────────────────────────────────────────────────────

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The one place everything is reached from."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_probe", "edit_probe", "remove_probe", "sampling", "gates"],
        )

    @property
    def _probes(self) -> list[ProbeConfig]:
        """Probes currently configured in the entry."""
        return probes_of(dict(self.config_entry.data))

    def _probe_selector(self) -> selector.SelectSelector:
        """Dropdown of the configured probes, by name."""
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value=probe.probe_id, label=probe.name) for probe in self._probes],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

    def _save_probes(self, probes: list[ProbeConfig]) -> ConfigFlowResult:
        """Write the probe list back into the entry and close the flow.

        The entry reloads through the update listener, so a probe added here is
        collecting samples a second later.
        """
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_PROBES: records_of(probes)},
        )
        return self.async_create_entry(title="", data=dict(self.config_entry.options))

    # ── Add ──────────────────────────────────────────────────────

    async def async_step_add_probe(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add a probe to the installation."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_probe_sources(self.hass, user_input, self._probes)
            if not errors:
                self._draft = dict(user_input)
                return await self.async_step_add_probe_soil()

        return self.async_show_form(step_id="add_probe", data_schema=_probe_schema(user_input or {}), errors=errors)

    async def async_step_add_probe_soil(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the reservoir of the probe being added."""
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {**self._draft, **user_input}
            error = validate_soil(candidate)
            if error:
                errors["base"] = error
            else:
                name = str(candidate.pop(CONF_NAME)).strip()
                probes = self._probes
                probe = ProbeConfig(
                    probe_id=new_probe_id(name, {existing.probe_id for existing in probes}),
                    name=name,
                    settings=candidate,
                )
                return self._save_probes([*probes, probe])

        return self.async_show_form(step_id="add_probe_soil", data_schema=_soil_schema(user_input or {}), errors=errors)

    # ── Edit ─────────────────────────────────────────────────────

    async def async_step_edit_probe(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the probe to edit."""
        if not self._probes:
            return self.async_abort(reason="no_probes")
        if user_input is not None:
            self._selected = user_input[CONF_SELECTED_PROBE]
            return await self.async_step_edit_probe_detail()

        return self.async_show_form(
            step_id="edit_probe",
            data_schema=vol.Schema({vol.Required(CONF_SELECTED_PROBE): self._probe_selector()}),
        )

    async def async_step_edit_probe_detail(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the sources of the selected probe."""
        current = next((probe for probe in self._probes if probe.probe_id == self._selected), None)
        if current is None:
            return self.async_abort(reason="no_probes")

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_probe_sources(self.hass, user_input, self._probes, editing_id=current.probe_id)
            if not errors:
                self._draft = dict(user_input)
                return await self.async_step_edit_probe_soil()

        defaults = {CONF_NAME: current.name, **current.settings}
        return self.async_show_form(
            step_id="edit_probe_detail",
            data_schema=_probe_schema(user_input or defaults),
            errors=errors,
            description_placeholders={"probe": current.name},
        )

    async def async_step_edit_probe_soil(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the reservoir of the selected probe.

        Correcting the soil re-derives the collected history against the new
        reservoir and drops the fit, which then has to be earned again on the
        same data. The samples are measurements and survive.
        """
        current = next((probe for probe in self._probes if probe.probe_id == self._selected), None)
        if current is None:
            return self.async_abort(reason="no_probes")

        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {**self._draft, **user_input}
            error = validate_soil(candidate)
            if error:
                errors["base"] = error
            else:
                name = str(candidate.pop(CONF_NAME)).strip()
                updated = ProbeConfig(probe_id=current.probe_id, name=name, settings=candidate)
                probes = [updated if probe.probe_id == current.probe_id else probe for probe in self._probes]
                return self._save_probes(probes)

        return self.async_show_form(
            step_id="edit_probe_soil",
            data_schema=_soil_schema(user_input or current.settings),
            errors=errors,
            description_placeholders={"probe": current.name, "soil_doc": SOIL_DOC_URL},
        )

    # ── Remove ───────────────────────────────────────────────────

    async def async_step_remove_probe(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the probe to remove."""
        if not self._probes:
            return self.async_abort(reason="no_probes")
        if user_input is not None:
            self._selected = user_input[CONF_SELECTED_PROBE]
            return await self.async_step_confirm_removal()

        return self.async_show_form(
            step_id="remove_probe",
            data_schema=vol.Schema({vol.Required(CONF_SELECTED_PROBE): self._probe_selector()}),
        )

    async def async_step_confirm_removal(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm, then drop the probe together with its device and its history.

        Asking twice is warranted here: removing a probe throws away the cycles
        it collected, and those cost weeks of calendar time to gather again.
        """
        current = next((probe for probe in self._probes if probe.probe_id == self._selected), None)
        if current is None:
            return self.async_abort(reason="no_probes")

        if user_input is not None:
            if not user_input.get(CONF_CONFIRM_REMOVAL):
                return await self.async_step_init()
            await self._forget_probe(current)
            return self._save_probes([probe for probe in self._probes if probe.probe_id != current.probe_id])

        return self.async_show_form(
            step_id="confirm_removal",
            data_schema=vol.Schema({vol.Required(CONF_CONFIRM_REMOVAL, default=False): selector.BooleanSelector()}),
            description_placeholders={"probe": current.name},
        )

    async def _forget_probe(self, probe: ProbeConfig) -> None:
        """Delete the device and the sample store of a probe being removed."""
        from . import async_remove_probe_store

        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, f"{self.config_entry.entry_id}_{probe.probe_id}")})
        if device is not None:
            registry.async_remove_device(device.id)
        await async_remove_probe_store(self.hass, self.config_entry, probe.probe_id)

    # ── Tuning, shared by every probe ────────────────────────────

    async def async_step_sampling(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Tune which observations are allowed to become samples."""
        if user_input is not None:
            return self.async_create_entry(title="", data={**self.config_entry.options, **user_input})

        current = {**self.config_entry.options}
        blank = AdmissionPolicy()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MIN_SAMPLE_INTERVAL,
                    default=current.get(CONF_MIN_SAMPLE_INTERVAL, blank.min_sample_interval_s),
                ): _number(60, 7200, 60, "s"),
                vol.Required(
                    CONF_DRAINAGE_MINUTES,
                    default=current.get(CONF_DRAINAGE_MINUTES, blank.drainage_minutes),
                ): _number(15, 1440, 15, "min"),
                vol.Required(
                    CONF_PROBE_TIMEOUT,
                    default=current.get(CONF_PROBE_TIMEOUT, blank.probe_timeout_s),
                ): _number(600, 86400, 600, "s"),
                vol.Required(
                    CONF_MIN_SOIL_TEMPERATURE,
                    default=current.get(CONF_MIN_SOIL_TEMPERATURE, blank.min_soil_temperature_c),
                ): _number(-5, 15, 0.5, "C"),
            }
        )
        return self.async_show_form(step_id="sampling", data_schema=schema)

    async def async_step_gates(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Tune how much evidence a calibration must have before it is published."""
        if user_input is not None:
            return self.async_create_entry(title="", data={**self.config_entry.options, **user_input})

        current = {**self.config_entry.options}
        blank = QualityGates()
        schema = vol.Schema(
            {
                vol.Required(CONF_MIN_CYCLES, default=current.get(CONF_MIN_CYCLES, blank.min_cycles)): _number(
                    1, 20, 1
                ),
                vol.Required(CONF_MIN_SAMPLES, default=current.get(CONF_MIN_SAMPLES, blank.min_samples)): _number(
                    10, 2000, 10
                ),
                vol.Required(CONF_MIN_RAW_SPAN, default=current.get(CONF_MIN_RAW_SPAN, blank.min_raw_span)): _number(
                    1, 80, 1, "%"
                ),
                vol.Required(CONF_MIN_R_SQUARED, default=current.get(CONF_MIN_R_SQUARED, blank.min_r_squared)): _number(
                    0.0, 0.99, 0.05
                ),
                vol.Required(
                    CONF_ALLOW_DEVICE_WRITEBACK,
                    default=current.get(CONF_ALLOW_DEVICE_WRITEBACK, False),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="gates", data_schema=schema)
