"""Robust line fitting, with no dependency beyond the standard library.

The estimator is deliberately the smallest part of this package. Two choices
are worth stating, because both are about the data rather than about statistics.

**Theil-Sen instead of least squares.** The sample stream contains outliers that
no admission rule can catch: a watering can nobody told the integration about, a
dog that knocked the probe, a rain shower the deficit model credited an hour
late. Ordinary least squares moves its line towards each of them, and a single
bad point at the wet end can rotate the whole calibration. Theil-Sen takes the
median of the pairwise slopes, so it keeps the line the majority of the data
agrees with until almost a third of the points are corrupted.

**No external package.** Home Assistant custom integrations pay for every
requirement in install time and in breakage, and a median of slopes is twenty
lines. Nothing here needs numpy or scipy.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

#: Two readings whose raw values differ by less than this carry no slope
#: information; their pairwise slope would be a division by nearly zero.
MIN_RAW_SEPARATION: float = 0.5

#: Above this many points the pairwise slope set costs more than it adds, so the
#: input is thinned by even strides. Deterministic on purpose: the same samples
#: must always produce the same calibration, which rules out random subsampling.
MAX_POINTS_FOR_PAIRWISE: int = 300


@dataclass(frozen=True, slots=True)
class LineFit:
    """A straight line with the diagnostics needed to decide whether to trust it."""

    slope: float
    intercept: float
    r_squared: float
    rmse: float
    point_count: int

    def predict(self, x: float) -> float:
        """Value of the line at ``x``."""
        return self.slope * x + self.intercept


def _thin(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Reduce a point set to at most :data:`MAX_POINTS_FOR_PAIRWISE` by even strides."""
    if len(points) <= MAX_POINTS_FOR_PAIRWISE:
        return list(points)
    stride = len(points) / MAX_POINTS_FOR_PAIRWISE
    return [points[int(index * stride)] for index in range(MAX_POINTS_FOR_PAIRWISE)]


def residual_rmse(points: Sequence[tuple[float, float]], slope: float, intercept: float) -> float:
    """Root mean square residual of a line against the points it was fitted on."""
    if not points:
        return 0.0
    squares = [(y - (slope * x + intercept)) ** 2 for x, y in points]
    return (sum(squares) / len(squares)) ** 0.5


def coefficient_of_determination(points: Sequence[tuple[float, float]], slope: float, intercept: float) -> float:
    """R squared of a line against its points, clamped at zero for worse-than-mean fits.

    Returned as zero, not as a negative number, when the line explains less than
    the mean would: the value is shown to a user as a quality score and a
    negative score communicates nothing useful.
    """
    if len(points) < 2:
        return 0.0
    ys = [y for _, y in points]
    mean_y = sum(ys) / len(ys)
    total = sum((y - mean_y) ** 2 for y in ys)
    if total <= 0.0:
        return 0.0
    residual = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    return max(0.0, 1.0 - residual / total)


def theil_sen(points: Sequence[tuple[float, float]]) -> LineFit | None:
    """Fit ``y = slope * x + intercept`` by the median of pairwise slopes.

    Returns ``None`` when the input carries no slope information at all: fewer
    than two points, or every point stacked at the same ``x``. A caller that
    receives ``None`` must not fall back to a default line, it must report that
    the probe has not yet been asked a varied enough question.
    """
    thinned = _thin(points)
    if len(thinned) < 2:
        return None

    slopes: list[float] = []
    for first in range(len(thinned) - 1):
        x1, y1 = thinned[first]
        for second in range(first + 1, len(thinned)):
            x2, y2 = thinned[second]
            if abs(x2 - x1) < MIN_RAW_SEPARATION:
                continue
            slopes.append((y2 - y1) / (x2 - x1))

    if not slopes:
        return None

    slope = statistics.median(slopes)
    intercept = statistics.median([y - slope * x for x, y in thinned])
    return LineFit(
        slope=slope,
        intercept=intercept,
        r_squared=coefficient_of_determination(points, slope, intercept),
        rmse=residual_rmse(points, slope, intercept),
        point_count=len(points),
    )


def two_point_line(wet: tuple[float, float], dry: tuple[float, float]) -> LineFit | None:
    """Fit the line through the wet and dry anchors alone.

    This is the fallback the physics gives for free: two states whose water
    content is known without believing any regression. It is used to sanity
    check the robust fit and to report a provisional calibration while cycles
    are still being collected.
    """
    x1, y1 = wet
    x2, y2 = dry
    if abs(x2 - x1) < MIN_RAW_SEPARATION:
        return None
    slope = (y2 - y1) / (x2 - x1)
    intercept = y1 - slope * x1
    points = [wet, dry]
    return LineFit(
        slope=slope,
        intercept=intercept,
        r_squared=coefficient_of_determination(points, slope, intercept),
        rmse=0.0,
        point_count=2,
    )


@dataclass(frozen=True, slots=True)
class TemperatureAwareFit:
    """A line in the probe index plus an additive temperature term.

    The model is ``theta = slope * raw + intercept + coefficient * (T - T_ref)``.
    The temperature term is additive and small by construction: it exists to
    absorb the drift of a capacitive reading with soil temperature, not to model
    the physics of permittivity, and it is dropped whenever it fails to reduce
    the residual.
    """

    line: LineFit
    temperature_coefficient: float
    reference_temperature_c: float

    def predict(self, raw_percent: float, temperature_c: float | None = None) -> float:
        """Estimated water content for a raw reading, corrected when a temperature is known."""
        value = self.line.predict(raw_percent)
        if temperature_c is not None and self.temperature_coefficient:
            value += self.temperature_coefficient * (temperature_c - self.reference_temperature_c)
        return value


#: A temperature term larger than this is not a probe artefact, it is the fit
#: absorbing something else (a seasonal trend, a failing battery) through the
#: only free parameter it has. Expressed in m3/m3 per degree Celsius.
MAX_TEMPERATURE_COEFFICIENT: float = 0.01

#: Fewer temperature-carrying points than this cannot support a second parameter.
MIN_POINTS_FOR_TEMPERATURE: int = 30


def _line_only(line: LineFit, reference_temperature_c: float) -> TemperatureAwareFit:
    """Wrap a plain line as a fit whose temperature term was not kept."""
    return TemperatureAwareFit(
        line=line,
        temperature_coefficient=0.0,
        reference_temperature_c=reference_temperature_c,
    )


def fit_with_temperature(
    observations: Sequence[tuple[float, float, float | None]],
    reference_temperature_c: float,
) -> TemperatureAwareFit | None:
    """Fit the probe line, adding a temperature term only when it earns its place.

    ``observations`` are ``(raw_percent, reference_moisture, temperature_c)``
    triples. The procedure is a single backfitting pass: fit the line, fit the
    residuals against temperature, refit the line on the corrected values, and
    keep the two-parameter model only if it lowers the residual on the same data.
    Adding a parameter always fits the sample better in least-squares land, but
    with a median estimator and this guard it must actually reduce the spread.
    """
    base_points = [(raw, moisture) for raw, moisture, _ in observations]
    base = theil_sen(base_points)
    if base is None:
        return None

    with_temperature = [
        (temperature - reference_temperature_c, moisture - base.predict(raw))
        for raw, moisture, temperature in observations
        if temperature is not None
    ]
    if len(with_temperature) < MIN_POINTS_FOR_TEMPERATURE:
        return _line_only(base, reference_temperature_c)

    temperature_line = theil_sen(with_temperature)
    if temperature_line is None or abs(temperature_line.slope) > MAX_TEMPERATURE_COEFFICIENT:
        return _line_only(base, reference_temperature_c)

    coefficient = temperature_line.slope
    corrected_points = [
        (
            raw,
            moisture - (coefficient * (temperature - reference_temperature_c) if temperature is not None else 0.0),
        )
        for raw, moisture, temperature in observations
    ]
    corrected = theil_sen(corrected_points)
    if corrected is None or corrected.rmse >= base.rmse:
        return _line_only(base, reference_temperature_c)

    return TemperatureAwareFit(
        line=corrected,
        temperature_coefficient=coefficient,
        reference_temperature_c=reference_temperature_c,
    )
