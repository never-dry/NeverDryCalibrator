"""Unit normalization: the conversions, and the refusal to guess."""

from __future__ import annotations

import pytest
from model import deficit_to_mm, depth_to_m, raw_index_to_percent, temperature_to_celsius


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (12.0, "mm", 12.0),
        (12.0, None, 12.0),
        (1.2, "cm", 12.0),
        (0.012, "m", 12.0),
        (1.0, "in", 25.4),
        (1.0, "INCH", 25.4),
        (3.0, "l/m2", 3.0),
    ],
)
def test_deficit_conversions(value, unit, expected):
    """Every water depth unit a deficit sensor may publish reaches millimetres."""
    assert deficit_to_mm(value, unit) == pytest.approx(expected)


def test_unknown_deficit_unit_is_refused_not_guessed():
    """An unrecognised unit returns None: a wrong factor here would be invisible."""
    assert deficit_to_mm(12.0, "furlongs") is None


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [(30.0, "cm", 0.30), (12.0, "in", 0.3048), (1.0, "m", 1.0), (1.0, "ft", 0.3048)],
)
def test_depth_conversions(value, unit, expected):
    """Root depth reaches metres from both metric and imperial units."""
    assert depth_to_m(value, unit) == pytest.approx(expected)


def test_depth_without_unit_is_refused():
    """A bare number is not a depth: the caller must say which unit it typed."""
    assert depth_to_m(30.0, None) is None


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [(20.0, "C", 20.0), (20.0, None, 20.0), (68.0, "F", 20.0), (293.15, "K", 20.0)],
)
def test_temperature_conversions(value, unit, expected):
    """Celsius, Fahrenheit and Kelvin all reach Celsius."""
    assert temperature_to_celsius(value, unit) == pytest.approx(expected)


def test_unknown_temperature_unit_is_refused():
    """An unknown temperature unit is refused rather than assumed."""
    assert temperature_to_celsius(20.0, "rankine") is None


def test_low_probe_values_stay_percent():
    """A probe reading of 0.4 is a very dry soil, not a fraction to rescale."""
    assert raw_index_to_percent(0.4) == pytest.approx(0.4)


@pytest.mark.parametrize("value", [-1.0, 101.0])
def test_out_of_range_probe_values_are_refused(value):
    """The probe contract is 0 to 100; anything else is not a probe reading."""
    assert raw_index_to_percent(value) is None
