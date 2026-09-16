"""Unit normalization for the values this integration reads from other entities.

The calibrator never owns a sensor: it reads a raw probe, a water deficit and
one or two temperatures that other integrations publish, each with whatever unit
its author chose. Everything below converts those foreign units into the three
canonical units the domain speaks: millimetres for the deficit, percent for the
raw probe index, degrees Celsius for temperatures.

The module is deliberately total: an unrecognised unit returns ``None`` rather
than a guess. A silently mis-scaled deficit would not raise anywhere downstream,
it would simply calibrate the probe against a soil that does not exist.
"""

from __future__ import annotations

#: Every length unit Home Assistant is known to publish a water depth in,
#: expressed as millimetres per unit. Inches carry the imperial installs.
DEFICIT_UNIT_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "l/m2": 1.0,
    "l/m²": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    '"': 25.4,
}

#: Depth units accepted for the root zone, expressed as metres per unit.
DEPTH_UNIT_TO_M: dict[str, float] = {
    "m": 1.0,
    "cm": 0.01,
    "mm": 0.001,
    "in": 0.0254,
    "inch": 0.0254,
    "inches": 0.0254,
    "ft": 0.3048,
    "feet": 0.3048,
}

#: Temperature units, resolved by name because the conversion is not a factor.
CELSIUS_UNITS: frozenset[str] = frozenset({"c", "°c", "degc", "celsius"})
FAHRENHEIT_UNITS: frozenset[str] = frozenset({"f", "°f", "degf", "fahrenheit"})
KELVIN_UNITS: frozenset[str] = frozenset({"k", "°k", "kelvin"})


def _normalize_unit(unit: str | None) -> str | None:
    """Fold a unit string to the lowercase, space-free form used as a table key."""
    if unit is None:
        return None
    folded = unit.strip().lower().replace(" ", "")
    return folded or None


def deficit_to_mm(value: float, unit: str | None) -> float | None:
    """Convert a water deficit to millimetres, or ``None`` if the unit is foreign.

    A missing unit is read as millimetres: it is the Home Assistant convention
    for precipitation-like quantities and the unit every deficit model in this
    ecosystem publishes. An unknown unit is refused instead of assumed, because
    a wrong factor here is invisible in the output.
    """
    folded = _normalize_unit(unit)
    if folded is None:
        return float(value)
    factor = DEFICIT_UNIT_TO_MM.get(folded)
    if factor is None:
        return None
    return float(value) * factor


def depth_to_m(value: float, unit: str | None) -> float | None:
    """Convert a root-zone depth to metres, or ``None`` if the unit is foreign."""
    folded = _normalize_unit(unit)
    if folded is None:
        return None
    factor = DEPTH_UNIT_TO_M.get(folded)
    if factor is None:
        return None
    return float(value) * factor


def temperature_to_celsius(value: float, unit: str | None) -> float | None:
    """Convert a temperature to degrees Celsius, or ``None`` if the unit is foreign.

    A missing unit is read as Celsius, which is what Home Assistant hands over
    once it has converted a sensor to the system unit.
    """
    folded = _normalize_unit(unit)
    if folded is None or folded in CELSIUS_UNITS:
        return float(value)
    if folded in FAHRENHEIT_UNITS:
        return (float(value) - 32.0) * 5.0 / 9.0
    if folded in KELVIN_UNITS:
        return float(value) - 273.15
    return None


def raw_index_to_percent(value: float) -> float | None:
    """Return the probe index on its declared 0-100 scale, or ``None`` if outside it.

    The contract with the probe is fixed by the hardware class this integration
    exists for: a cheap capacitive probe always publishes 0 to 100, whatever that
    number means physically. Values in ``[0, 1]`` are therefore percent, not a
    fraction to be rescaled. Guessing the scale from the magnitude would silently
    multiply a genuinely very dry reading by one hundred.
    """
    numeric = float(value)
    if numeric < 0.0 or numeric > 100.0:
        return None
    return numeric
