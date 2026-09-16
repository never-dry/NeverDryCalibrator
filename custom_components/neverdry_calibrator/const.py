"""Configuration keys, defaults and service names for the calibrator integration.

Keys are grouped by where the user meets them: the sources step of the config
flow, the soil step, and the options flow. The grouping is not cosmetic, it is
what keeps the options flow and the storage schema from drifting apart.
"""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "neverdry_calibrator"

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# ── Probes ─────────────────────────────────────────────────────
#: The config entry holds a list of probe records under this key, the way
#: NeverDry holds its zones: one integration entry, everything inside it.
CONF_PROBES = "probes"
CONF_PROBE_ID = "probe_id"
CONF_PROBE_NAME = "probe_name"

# ── Sources (per probe) ────────────────────────────────────────
CONF_MOISTURE_ENTITY = "moisture_entity"
CONF_DEFICIT_ENTITY = "deficit_entity"
CONF_AMBIENT_TEMPERATURE_ENTITY = "ambient_temperature_entity"
CONF_IRRIGATION_ENTITY = "irrigation_entity"
CONF_PROBE_TEMPERATURE_ENTITY = "probe_temperature_entity"
CONF_BATTERY_ENTITY = "battery_entity"
CONF_DEVICE_CALIBRATION_ENTITIES = "device_calibration_entities"

# ── Soil reservoir ─────────────────────────────────────────────
CONF_SOIL_TEXTURE = "soil_texture"
CONF_FIELD_CAPACITY = "field_capacity"
CONF_WILTING_POINT = "wilting_point"
CONF_SATURATION = "saturation"
CONF_ROOT_DEPTH = "root_depth"
CONF_ROOT_DEPTH_UNIT = "root_depth_unit"

# ── Admission and cycle tuning (options flow) ──────────────────
CONF_MIN_SAMPLE_INTERVAL = "min_sample_interval_s"
CONF_MAX_DEFICIT_AGE = "max_deficit_age_s"
CONF_PROBE_TIMEOUT = "probe_timeout_s"
CONF_DRAINAGE_MINUTES = "drainage_minutes"
CONF_MIN_SOIL_TEMPERATURE = "min_soil_temperature_c"
CONF_REQUIRE_PROBE_TEMPERATURE = "require_probe_temperature"
CONF_IRRIGATION_DROP_FRACTION = "irrigation_drop_fraction"
CONF_WET_ANCHOR_FRACTION = "wet_anchor_fraction"
CONF_MIN_CYCLE_SPAN_FRACTION = "min_cycle_span_fraction"
CONF_MIN_SAMPLES_PER_CYCLE = "min_samples_per_cycle"

# ── Quality gates (options flow) ───────────────────────────────
CONF_MIN_CYCLES = "min_cycles"
CONF_MIN_SAMPLES = "min_samples"
CONF_MIN_RAW_SPAN = "min_raw_span"
CONF_MIN_R_SQUARED = "min_r_squared"
CONF_MAX_RMSE_FRACTION = "max_rmse_fraction"

# ── Device write-back (options flow, opt-in) ───────────────────
CONF_ALLOW_DEVICE_WRITEBACK = "allow_device_writeback"

#: Length units offered for the root depth. Imperial is not an afterthought:
#: a large share of Home Assistant installs measures a garden in inches.
ROOT_DEPTH_UNITS: list[str] = ["cm", "in"]
DEFAULT_ROOT_DEPTH_CM: float = 30.0

#: How often the coordinator looks at its sources. A probe reports far more
#: often than soil changes, and the admission policy rate-limits sampling
#: anyway, so polling is both sufficient and cheaper than reacting to
#: every state write of a chatty Zigbee device.
UPDATE_INTERVAL_MINUTES: int = 5

#: Smallest gap between two refits when no cycle has completed. A completed
#: cycle always forces one, because that is the event that can change the
#: verdict.
REFIT_INTERVAL_MINUTES: int = 30

#: Storage
STORAGE_VERSION: int = 1
#: One store per probe: the history belongs to the probe, not to the entry.
STORAGE_KEY_TEMPLATE = f"{DOMAIN}.{{entry_id}}_{{probe_id}}"
#: Delay before the sample store is written, so a burst of samples costs one write.
STORAGE_SAVE_DELAY_S: int = 60

# ── Services ───────────────────────────────────────────────────
SERVICE_CALIBRATE_NOW = "calibrate_now"
SERVICE_RESET_CALIBRATION = "reset_calibration"
SERVICE_MARK_FIELD_CAPACITY = "mark_field_capacity"
SERVICE_APPLY_DEVICE_OFFSET = "apply_device_offset"
SERVICE_EXPORT_SAMPLES = "export_samples"

#: Where the field method for choosing a soil texture is written out. Passed
#: to the soil steps as a placeholder: hassfest refuses URLs inside strings.
SOIL_DOC_URL = "https://github.com/never-dry/NeverDryCalibrator#which-soil-should-i-pick"

#: Default title of the single integration entry.
DEFAULT_TITLE = "NeverDry Calibrator"

ATTR_PROBE = "probe"
ATTR_OFFSET = "offset"

#: Substrings used to recognise the calibration knobs cheap probes expose.
#: Matched against entity id and friendly name, lowercased.
CALIBRATION_KEYWORDS: tuple[str, ...] = ("calibration", "calibrate", "offset", "correction")
TEMPERATURE_KEYWORDS: tuple[str, ...] = ("temperature", "temp")
HUMIDITY_KEYWORDS: tuple[str, ...] = ("humidity", "moisture", "hum")
SOIL_KEYWORDS: tuple[str, ...] = ("soil", "earth", "ground")
#: Words a water balance integration uses for the depletion it irrigates at.
THRESHOLD_KEYWORDS: tuple[str, ...] = ("threshold", "soglia", "trigger")

#: Repair issue raised when the irrigation regime cannot produce a countable cycle.
ISSUE_THRESHOLD_TOO_LOW = "irrigation_threshold_too_low"
