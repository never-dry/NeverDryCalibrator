"""Synthetic probe and irrigation cycles, shared by the domain tests.

The generator models what a cheap capacitive probe actually does: a monotone,
noisy, temperature-sensitive index over the soil water content. Tests then assert
that the calibration recovers the map that produced the data, which is the only
end-to-end property worth checking.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from datetime import datetime, timedelta

from model import CalibrationSession, Observation, SoilProfile, SoilTexture

RAW_AT_WILTING_POINT = 22.0
RAW_AT_FIELD_CAPACITY = 78.0


def loam(root_depth_m: float = 0.30) -> SoilProfile:
    """The reference soil used across the tests."""
    return SoilProfile.from_texture(SoilTexture.LOAM, root_depth_m=root_depth_m)


def probe_index(soil: SoilProfile, deficit_mm: float, temperature_c: float, noise: float) -> float:
    """Index a synthetic probe would publish for this soil state."""
    available = soil.available_fraction(deficit_mm)
    raw = RAW_AT_WILTING_POINT + (RAW_AT_FIELD_CAPACITY - RAW_AT_WILTING_POINT) * available
    raw += 0.05 * (temperature_c - 20.0)
    raw += noise
    return max(0.0, min(100.0, raw))


def true_slope(soil: SoilProfile) -> float:
    """Slope the calibration should recover: water content per index point."""
    return (soil.field_capacity - soil.wilting_point) / (RAW_AT_FIELD_CAPACITY - RAW_AT_WILTING_POINT)


def run_cycles(
    session: CalibrationSession,
    cycles: int,
    *,
    start: datetime | None = None,
    days_per_cycle: int = 8,
    et_mm_per_day: float = 4.0,
    noise_sigma: float = 0.8,
    seed: int = 7,
    drainage_hours: int = 4,
    index_fn: Callable[[SoilProfile, float, float, float], float] = probe_index,
) -> datetime:
    """Feed the session a number of irrigation-to-dry-down cycles, hour by hour.

    Returns the clock the run ended at, so a caller can continue the timeline.
    Note that ``cycles`` irrigations produce ``cycles - 1`` closed cycles: a cycle
    only completes when the next irrigation closes it.

    ``index_fn`` replaces the well-behaved synthetic probe with a misbehaving one,
    which is how the placement tests feed the same machinery a probe that never
    moves, or one that jumps while the soil stands still.
    """
    generator = random.Random(seed)
    now = start or datetime(2026, 4, 1, 6, 0)
    soil = session.soil

    for _ in range(cycles):
        deficit = 0.0
        session.note_irrigation(now)
        last_irrigation = now
        now += timedelta(hours=drainage_hours)
        for _hour in range(days_per_cycle * 24):
            temperature = 18.0 + 8.0 * math.sin((now.hour - 6) / 24.0 * 2 * math.pi)
            deficit += et_mm_per_day / 24.0
            session.observe(
                Observation(
                    taken_at=now,
                    raw_percent=index_fn(soil, deficit, temperature, generator.gauss(0.0, noise_sigma)),
                    deficit_mm=deficit,
                    deficit_age_s=60.0,
                    probe_temperature_c=temperature,
                    probe_temperature_age_s=120.0,
                    ambient_temperature_c=temperature + 3.0,
                    irrigation_active=False,
                    seconds_since_irrigation=(now - last_irrigation).total_seconds(),
                )
            )
            now += timedelta(hours=1)
    return now
