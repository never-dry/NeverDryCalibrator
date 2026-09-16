"""The soil reservoir: the only bridge between a deficit in mm and a moisture in m3/m3.

A water-balance model counts *missing millimetres*; a probe answers a question
about *water content*. The two are the same physical fact seen from opposite
ends of the same reservoir, and :class:`SoilProfile` is that reservoir: field
capacity, wilting point and root depth. Nothing else in this package is allowed
to convert between the two quantities, so a site that changes its soil changes
exactly one object and every derived number follows.

The numbers in :data:`SOIL_TEXTURE_DEFAULTS` mirror the soil table of the
NeverDry water-balance model on purpose. When the deficit comes from that
integration, taking a different field capacity here would calibrate the probe
against a reservoir the deficit was never measured against.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class SoilTexture(StrEnum):
    """Soil texture presets offered in the config flow."""

    AUTO = "auto"
    SANDY = "sandy"
    LOAM = "loam"
    CLAY = "clay"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class TextureDefaults:
    """Field capacity, wilting point and porosity of one texture preset [m3/m3]."""

    field_capacity: float
    wilting_point: float
    saturation: float


#: Texture presets. ``AUTO`` is the middle soil used when the site has no idea,
#: and is deliberately identical to ``LOAM`` rather than an average of extremes:
#: a reservoir whose ends come from different soils describes no real ground.
SOIL_TEXTURE_DEFAULTS: dict[SoilTexture, TextureDefaults] = {
    SoilTexture.AUTO: TextureDefaults(field_capacity=0.25, wilting_point=0.12, saturation=0.45),
    SoilTexture.SANDY: TextureDefaults(field_capacity=0.15, wilting_point=0.06, saturation=0.40),
    SoilTexture.LOAM: TextureDefaults(field_capacity=0.25, wilting_point=0.12, saturation=0.45),
    SoilTexture.CLAY: TextureDefaults(field_capacity=0.36, wilting_point=0.22, saturation=0.50),
}

#: Default root-zone depth [m]. Turf and small shrubs, the common case for a
#: cheap probe pushed into a garden bed.
DEFAULT_ROOT_DEPTH_M: float = 0.30


class InvalidSoilProfile(ValueError):
    """Raised when the reservoir parameters cannot describe a real soil."""


@dataclass(frozen=True, slots=True)
class SoilProfile:
    """The water reservoir of one measuring point, and the deficit/moisture map.

    Invariant: ``0 < wilting_point < field_capacity <= saturation < 1`` and
    ``root_depth_m > 0``. The constructor refuses anything else, so every
    conversion downstream can assume a non-degenerate reservoir and a positive
    total available water.
    """

    texture: SoilTexture
    field_capacity: float
    wilting_point: float
    root_depth_m: float
    saturation: float

    def __post_init__(self) -> None:
        """Reject reservoirs that no ground could have."""
        if not 0.0 < self.wilting_point < self.field_capacity <= self.saturation < 1.0:
            raise InvalidSoilProfile(
                "expected 0 < wilting_point < field_capacity <= saturation < 1, got "
                f"{self.wilting_point}, {self.field_capacity}, {self.saturation}"
            )
        if self.root_depth_m <= 0.0:
            raise InvalidSoilProfile(f"root depth must be positive, got {self.root_depth_m}")

    @classmethod
    def from_texture(
        cls,
        texture: SoilTexture,
        root_depth_m: float = DEFAULT_ROOT_DEPTH_M,
    ) -> SoilProfile:
        """Build a profile from a preset texture. ``CUSTOM`` has no preset and is refused."""
        defaults = SOIL_TEXTURE_DEFAULTS.get(texture)
        if defaults is None:
            raise InvalidSoilProfile(f"texture {texture} has no preset; supply the values explicitly")
        return cls(
            texture=texture,
            field_capacity=defaults.field_capacity,
            wilting_point=defaults.wilting_point,
            root_depth_m=root_depth_m,
            saturation=defaults.saturation,
        )

    @property
    def total_available_water_mm(self) -> float:
        """Total available water (TAW) of the root zone [mm].

        The full swing the probe can ever see: from field capacity down to the
        wilting point. It is the natural scale for every deficit threshold in
        this package, which is why thresholds are expressed as fractions of it
        rather than as absolute millimetres a user would have to guess.
        """
        return (self.field_capacity - self.wilting_point) * self.root_depth_m * 1000.0

    @property
    def deficit_at_saturation_mm(self) -> float:
        """Deficit of a saturated profile [mm]: negative, because it holds more than FC."""
        return (self.field_capacity - self.saturation) * self.root_depth_m * 1000.0

    def moisture_at_deficit(self, deficit_mm: float) -> float:
        """Volumetric water content [m3/m3] implied by a deficit, clamped to the reservoir.

        This is the reference value the whole calibration is fitted against:
        ``theta = FC - D / (1000 * Zr)``. The clamp keeps a model that overshoots
        (a deficit larger than TAW after a long drought) from producing a
        negative water content, which no probe could ever agree with.
        """
        raw = self.field_capacity - deficit_mm / (1000.0 * self.root_depth_m)
        return min(self.saturation, max(0.0, raw))

    def deficit_at_moisture(self, moisture: float) -> float:
        """Deficit [mm] implied by a volumetric water content: the inverse map."""
        return (self.field_capacity - moisture) * 1000.0 * self.root_depth_m

    def available_fraction(self, deficit_mm: float) -> float:
        """Share of the available water still in the soil: 0 at wilting point, 1 at FC."""
        taw = self.total_available_water_mm
        return min(1.0, max(0.0, 1.0 - deficit_mm / taw))

    def available_fraction_at_moisture(self, moisture: float) -> float:
        """Share of available water implied by a water content, clamped to ``[0, 1]``."""
        span = self.field_capacity - self.wilting_point
        return min(1.0, max(0.0, (moisture - self.wilting_point) / span))

    def fingerprint(self) -> str:
        """Short digest of the reservoir, used to detect that a stored fit predates a change.

        A fit is a statement about *this* reservoir. When the user edits the soil
        or the root depth, every stored reference moisture was derived from the
        old one, so the fit must be invalidated rather than reused. Comparing a
        digest is cheaper and less error-prone than comparing four floats with
        their rounding history.
        """
        payload = f"{self.field_capacity:.6f}|{self.wilting_point:.6f}|{self.root_depth_m:.6f}|{self.saturation:.6f}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict[str, float | str]:
        """Serialize for the config entry and the sample store."""
        return {
            "texture": str(self.texture),
            "field_capacity": self.field_capacity,
            "wilting_point": self.wilting_point,
            "root_depth_m": self.root_depth_m,
            "saturation": self.saturation,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SoilProfile:
        """Rebuild a profile from its serialized form, applying preset fallbacks."""
        texture = SoilTexture(data.get("texture", SoilTexture.AUTO))
        defaults = SOIL_TEXTURE_DEFAULTS.get(texture, SOIL_TEXTURE_DEFAULTS[SoilTexture.AUTO])
        return cls(
            texture=texture,
            field_capacity=float(data.get("field_capacity", defaults.field_capacity)),
            wilting_point=float(data.get("wilting_point", defaults.wilting_point)),
            root_depth_m=float(data.get("root_depth_m", DEFAULT_ROOT_DEPTH_M)),
            saturation=float(data.get("saturation", defaults.saturation)),
        )
