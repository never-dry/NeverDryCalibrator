"""Translating a config entry into the domain objects that actually enforce it.

A config entry is a flat mapping of whatever the user typed; the domain works in
policies and profiles with invariants. This module is the single crossing point
between the two, used by the config flow to validate what was typed and by the
setup to build what will run. Having one crossing is what keeps a renamed option
from silently falling back to a default somewhere else.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DRAINAGE_MINUTES,
    CONF_FIELD_CAPACITY,
    CONF_IRRIGATION_DROP_FRACTION,
    CONF_MAX_DEFICIT_AGE,
    CONF_MAX_RMSE_FRACTION,
    CONF_MIN_CYCLE_SPAN_FRACTION,
    CONF_MIN_CYCLES,
    CONF_MIN_R_SQUARED,
    CONF_MIN_RAW_SPAN,
    CONF_MIN_SAMPLE_INTERVAL,
    CONF_MIN_SAMPLES,
    CONF_MIN_SAMPLES_PER_CYCLE,
    CONF_MIN_SOIL_TEMPERATURE,
    CONF_PROBE_TIMEOUT,
    CONF_RAIN_EVENT_FRACTION,
    CONF_RAIN_QUIET_MINUTES,
    CONF_RAIN_SENSOR_TYPE,
    CONF_REQUIRE_PROBE_TEMPERATURE,
    CONF_ROOT_DEPTH,
    CONF_ROOT_DEPTH_UNIT,
    CONF_SATURATION,
    CONF_SOIL_TEXTURE,
    CONF_WET_ANCHOR_FRACTION,
    CONF_WILTING_POINT,
    DEFAULT_ROOT_DEPTH_CM,
    RAIN_TYPE_ACCUMULATOR,
)
from .model import (
    AdmissionPolicy,
    CyclePolicy,
    QualityGates,
    RainPolicy,
    RainSensorKind,
    SoilProfile,
    SoilTexture,
    depth_to_m,
)
from .model.soil import DEFAULT_ROOT_DEPTH_M, SOIL_TEXTURE_DEFAULTS, InvalidSoilProfile

#: Gap between field capacity and porosity assumed for a custom soil whose
#: saturation the user did not state. Only used as the upper clamp of the
#: published moisture, so a rough value is honest and a missing one is not.
CUSTOM_SATURATION_MARGIN: float = 0.12


def merged_settings(entry: ConfigEntry) -> dict[str, Any]:
    """The effective configuration: what was set up, overridden by what was tuned."""
    return {**entry.data, **entry.options}


def root_depth_m(settings: Mapping[str, Any]) -> float:
    """Root-zone depth in metres, honouring the unit the user chose."""
    value = float(settings.get(CONF_ROOT_DEPTH, DEFAULT_ROOT_DEPTH_CM))
    unit = str(settings.get(CONF_ROOT_DEPTH_UNIT, "cm"))
    converted = depth_to_m(value, unit)
    if converted is None or converted <= 0:
        return DEFAULT_ROOT_DEPTH_M
    return converted


def soil_profile(settings: Mapping[str, Any]) -> SoilProfile:
    """Build the soil reservoir described by the entry, raising on an impossible one.

    A custom texture is the only case where the three water contents come from
    the user. The saturation is allowed to be absent because most people know
    their field capacity and have never met a porosity figure; it is then taken
    a fixed margin above field capacity, which only affects the upper clamp of
    the published value.
    """
    texture = SoilTexture(settings.get(CONF_SOIL_TEXTURE, SoilTexture.AUTO))
    depth = root_depth_m(settings)

    if texture is not SoilTexture.CUSTOM:
        return SoilProfile.from_texture(texture, root_depth_m=depth)

    defaults = SOIL_TEXTURE_DEFAULTS[SoilTexture.AUTO]
    field_capacity = float(settings.get(CONF_FIELD_CAPACITY, defaults.field_capacity))
    wilting_point = float(settings.get(CONF_WILTING_POINT, defaults.wilting_point))
    saturation = settings.get(CONF_SATURATION)
    resolved_saturation = (
        float(saturation) if saturation is not None else min(0.95, field_capacity + CUSTOM_SATURATION_MARGIN)
    )
    return SoilProfile(
        texture=SoilTexture.CUSTOM,
        field_capacity=field_capacity,
        wilting_point=wilting_point,
        root_depth_m=depth,
        saturation=resolved_saturation,
    )


def admission_policy(settings: Mapping[str, Any]) -> AdmissionPolicy:
    """Build the sample admission policy from the tuned options."""
    return AdmissionPolicy.from_dict(
        {
            "min_sample_interval_s": settings.get(CONF_MIN_SAMPLE_INTERVAL, AdmissionPolicy().min_sample_interval_s),
            "max_deficit_age_s": settings.get(CONF_MAX_DEFICIT_AGE, AdmissionPolicy().max_deficit_age_s),
            "probe_timeout_s": settings.get(CONF_PROBE_TIMEOUT, AdmissionPolicy().probe_timeout_s),
            "drainage_minutes": settings.get(CONF_DRAINAGE_MINUTES, AdmissionPolicy().drainage_minutes),
            "min_soil_temperature_c": settings.get(CONF_MIN_SOIL_TEMPERATURE, AdmissionPolicy().min_soil_temperature_c),
            "require_probe_temperature": settings.get(
                CONF_REQUIRE_PROBE_TEMPERATURE, AdmissionPolicy().require_probe_temperature
            ),
        }
    )


def cycle_policy(settings: Mapping[str, Any]) -> CyclePolicy:
    """Build the cycle detection policy from the tuned options."""
    blank = CyclePolicy()
    return CyclePolicy.from_dict(
        {
            "irrigation_drop_fraction": settings.get(CONF_IRRIGATION_DROP_FRACTION, blank.irrigation_drop_fraction),
            "wet_anchor_fraction": settings.get(CONF_WET_ANCHOR_FRACTION, blank.wet_anchor_fraction),
            "min_cycle_span_fraction": settings.get(CONF_MIN_CYCLE_SPAN_FRACTION, blank.min_cycle_span_fraction),
            "min_samples_per_cycle": settings.get(CONF_MIN_SAMPLES_PER_CYCLE, blank.min_samples_per_cycle),
        }
    )


def rain_policy(settings: Mapping[str, Any]) -> RainPolicy:
    """Build the rain policy, defaulting the event size to the irrigation one.

    The fallback is the point of this function. A user who never opens the rain
    options gets a shower counted exactly when a deficit drop of the same size
    would have been counted, which is the promise the feature was asked for:
    rain counts as an irrigation. Tuning the two apart stays possible and stays
    a deliberate act.
    """
    blank = RainPolicy()
    irrigation_fraction = settings.get(CONF_IRRIGATION_DROP_FRACTION, CyclePolicy().irrigation_drop_fraction)
    return RainPolicy.from_dict(
        {
            "event_fraction": settings.get(CONF_RAIN_EVENT_FRACTION, irrigation_fraction),
            "quiet_minutes": settings.get(CONF_RAIN_QUIET_MINUTES, blank.quiet_minutes),
        }
    )


def rain_sensor_kind(settings: Mapping[str, Any]) -> RainSensorKind:
    """Which shape of gauge the entry describes."""
    if settings.get(CONF_RAIN_SENSOR_TYPE) == RAIN_TYPE_ACCUMULATOR:
        return RainSensorKind.ACCUMULATOR
    return RainSensorKind.EVENT


def quality_gates(settings: Mapping[str, Any]) -> QualityGates:
    """Build the publication gates from the tuned options."""
    blank = QualityGates()
    return QualityGates.from_dict(
        {
            "min_cycles": settings.get(CONF_MIN_CYCLES, blank.min_cycles),
            "min_samples": settings.get(CONF_MIN_SAMPLES, blank.min_samples),
            "min_raw_span": settings.get(CONF_MIN_RAW_SPAN, blank.min_raw_span),
            "min_r_squared": settings.get(CONF_MIN_R_SQUARED, blank.min_r_squared),
            "max_rmse_fraction": settings.get(CONF_MAX_RMSE_FRACTION, blank.max_rmse_fraction),
        }
    )


def validate_soil(settings: Mapping[str, Any]) -> str | None:
    """Return the config-flow error key for an impossible soil, or ``None`` if valid."""
    try:
        soil_profile(settings)
    except InvalidSoilProfile:
        return "invalid_soil"
    return None
