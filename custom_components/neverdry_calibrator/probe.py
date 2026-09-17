"""One calibrated probe inside the integration entry.

The integration is a hub: a single entry named after the installation, holding a
list of probes the way NeverDry holds its zones. Everything about one probe lives
in one record, so adding, editing and removing a probe is a list operation on the
entry and nothing else in the code has to know that several probes exist.

The record keeps a stable ``probe_id``, assigned once from the name and never
rewritten afterwards. It is what the sample store and the device registry are
keyed by, so renaming a probe in the user interface renames a device and keeps
weeks of collected cycles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .const import (
    CONF_AMBIENT_TEMPERATURE_ENTITY,
    CONF_DEFICIT_ENTITY,
    CONF_FIELD_CAPACITY,
    CONF_IRRIGATION_ENTITY,
    CONF_MOISTURE_ENTITY,
    CONF_PROBE_ID,
    CONF_PROBE_NAME,
    CONF_PROBE_TEMPERATURE_ENTITY,
    CONF_PROBES,
    CONF_ROOT_DEPTH,
    CONF_ROOT_DEPTH_UNIT,
    CONF_SATURATION,
    CONF_SHELTERED_FROM_RAIN,
    CONF_SOIL_TEXTURE,
    CONF_WILTING_POINT,
)

#: Keys that name an entity this probe reads.
SOURCE_KEYS: tuple[str, ...] = (
    CONF_MOISTURE_ENTITY,
    CONF_DEFICIT_ENTITY,
    CONF_AMBIENT_TEMPERATURE_ENTITY,
    CONF_IRRIGATION_ENTITY,
    CONF_PROBE_TEMPERATURE_ENTITY,
)

#: Keys that describe the reservoir this probe sits in.
SOIL_KEYS: tuple[str, ...] = (
    CONF_SOIL_TEXTURE,
    CONF_ROOT_DEPTH,
    CONF_ROOT_DEPTH_UNIT,
    CONF_FIELD_CAPACITY,
    CONF_WILTING_POINT,
    CONF_SATURATION,
)


def slugify_name(name: str) -> str:
    """Turn a probe name into an identifier fragment: lowercase, ascii, underscores."""
    folded = re.sub(r"[^a-z0-9]+", "_", name.strip().lower())
    return folded.strip("_") or "probe"


def new_probe_id(name: str, existing: set[str]) -> str:
    """Assign an identifier that is stable, readable, and unique within the entry."""
    base = slugify_name(name)
    if base not in existing:
        return base
    index = 2
    while f"{base}_{index}" in existing:
        index += 1
    return f"{base}_{index}"


@dataclass(frozen=True, slots=True)
class ProbeConfig:
    """Everything the integration knows about one probe before it starts learning."""

    probe_id: str
    name: str
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def moisture_entity(self) -> str:
        """The raw probe entity being calibrated."""
        return self.settings[CONF_MOISTURE_ENTITY]

    @property
    def deficit_entity(self) -> str:
        """The water deficit entity used as the reference."""
        return self.settings[CONF_DEFICIT_ENTITY]

    @property
    def ambient_temperature_entity(self) -> str | None:
        """Optional ambient temperature: frost guard and fallback covariate."""
        return self.settings.get(CONF_AMBIENT_TEMPERATURE_ENTITY)

    @property
    def irrigation_entity(self) -> str | None:
        """Optional valve or switch that witnesses water being delivered."""
        return self.settings.get(CONF_IRRIGATION_ENTITY)

    @property
    def probe_temperature_entity(self) -> str | None:
        """The temperature channel acting as liveness sentinel, usually discovered."""
        return self.settings.get(CONF_PROBE_TEMPERATURE_ENTITY)

    @property
    def sheltered_from_rain(self) -> bool:
        """Whether rain must be kept away from this probe.

        Defaults to false, which is the right default for a probe in the ground:
        most of them are rained on, and the one under a roof is the exception its
        owner knows about.
        """
        return bool(self.settings.get(CONF_SHELTERED_FROM_RAIN, False))

    def entity_for(self, key: str) -> str | None:
        """Entity id configured under a source key, or ``None`` when unset."""
        value = self.settings.get(key)
        return value or None

    def soil_settings(self) -> dict[str, Any]:
        """Just the reservoir keys, for the soil profile builder."""
        return {key: self.settings[key] for key in SOIL_KEYS if key in self.settings}

    def with_settings(self, **overrides: Any) -> ProbeConfig:
        """A copy carrying changed settings, keeping the identifier untouched."""
        merged = {**self.settings, **overrides}
        return ProbeConfig(probe_id=self.probe_id, name=self.name, settings=merged)

    def renamed(self, name: str) -> ProbeConfig:
        """A copy under a new name. The identifier survives, and so does the history."""
        return ProbeConfig(probe_id=self.probe_id, name=name, settings=dict(self.settings))

    def to_dict(self) -> dict[str, Any]:
        """Serialize into the config entry list."""
        return {CONF_PROBE_ID: self.probe_id, CONF_PROBE_NAME: self.name, **self.settings}

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> ProbeConfig:
        """Rebuild from the config entry list, tolerating a record without an id.

        A missing identifier can only come from a hand-edited entry; deriving it
        from the name is better than refusing to load the probe, and it is stable
        as long as the name is.
        """
        settings = {key: value for key, value in record.items() if key not in (CONF_PROBE_ID, CONF_PROBE_NAME)}
        name = record.get(CONF_PROBE_NAME) or record.get(CONF_MOISTURE_ENTITY, "probe")
        return cls(
            probe_id=record.get(CONF_PROBE_ID) or slugify_name(name),
            name=name,
            settings=settings,
        )


def probes_of(entry_data: dict[str, Any]) -> list[ProbeConfig]:
    """Every probe configured in an entry, in the order the user added them."""
    return [ProbeConfig.from_dict(record) for record in entry_data.get(CONF_PROBES, [])]


def records_of(probes: list[ProbeConfig]) -> list[dict[str, Any]]:
    """Serialize a probe list back into the shape stored in the config entry."""
    return [probe.to_dict() for probe in probes]
