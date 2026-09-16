"""The estimator: robustness to outliers, refusal without information, temperature term."""

from __future__ import annotations

import pytest
from model import fit_with_temperature, theil_sen, two_point_line


def _line(slope: float, intercept: float, count: int = 30):
    """A clean straight line, used as the signal an estimator must recover."""
    return [(float(x), slope * x + intercept) for x in range(10, 10 + count * 2, 2)]


def test_theil_sen_recovers_a_clean_line():
    """With no noise the median of slopes is the slope."""
    fit = theil_sen(_line(0.002, 0.07))
    assert fit.slope == pytest.approx(0.002, abs=1e-9)
    assert fit.intercept == pytest.approx(0.07, abs=1e-9)
    assert fit.r_squared == pytest.approx(1.0)


def test_theil_sen_survives_a_quarter_of_corrupted_points():
    """The reason this estimator was chosen: outliers do not rotate the line."""
    points = _line(0.002, 0.07, count=40)
    for index in range(0, 40, 4):
        raw, _ = points[index]
        points[index] = (raw, 0.45)  # a watering can nobody reported
    fit = theil_sen(points)
    assert fit.slope == pytest.approx(0.002, abs=2e-4)


def test_no_slope_information_returns_none():
    """Points stacked at one probe value carry no slope: the caller must be told."""
    assert theil_sen([(50.0, 0.2), (50.0, 0.3), (50.1, 0.25)]) is None
    assert theil_sen([(50.0, 0.2)]) is None


def test_two_point_line_needs_two_separated_anchors():
    """Anchors that sit on top of each other cannot define a line."""
    assert two_point_line((50.0, 0.25), (50.2, 0.12)) is None
    line = two_point_line((78.0, 0.25), (22.0, 0.12))
    assert line.slope == pytest.approx((0.25 - 0.12) / (78.0 - 22.0))


def test_temperature_term_is_dropped_when_it_explains_nothing():
    """A parameter that does not reduce the residual is not kept."""
    observations = [(raw, value, 20.0) for raw, value in _line(0.002, 0.07, count=40)]
    fit = fit_with_temperature(observations, reference_temperature_c=20.0)
    assert fit.temperature_coefficient == 0.0


def test_temperature_term_is_recovered_when_it_is_real():
    """A genuine thermal drift is absorbed rather than left in the residual."""
    coefficient = 0.002
    observations = []
    for index, (raw, value) in enumerate(_line(0.002, 0.07, count=60)):
        temperature = 10.0 + (index % 20)
        observations.append((raw, value + coefficient * (temperature - 20.0), temperature))
    fit = fit_with_temperature(observations, reference_temperature_c=20.0)
    assert fit.temperature_coefficient == pytest.approx(coefficient, abs=5e-4)
    assert fit.predict(50.0, 20.0) == pytest.approx(0.002 * 50.0 + 0.07, abs=2e-3)


def test_an_implausible_temperature_coefficient_is_refused():
    """A huge thermal term means the fit is absorbing something else through it."""
    observations = []
    for index, (raw, value) in enumerate(_line(0.002, 0.07, count=60)):
        temperature = 10.0 + (index % 20)
        observations.append((raw, value + 0.5 * (temperature - 20.0), temperature))
    fit = fit_with_temperature(observations, reference_temperature_c=20.0)
    assert fit.temperature_coefficient == 0.0
