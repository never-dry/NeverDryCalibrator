"""The soil reservoir: its invariant, its conversions and its fingerprint."""

from __future__ import annotations

import pytest
from model import InvalidSoilProfile, SoilProfile, SoilTexture


def test_total_available_water_is_the_span_times_the_depth():
    """TAW is the reservoir the whole calibration is scaled against."""
    soil = SoilProfile.from_texture(SoilTexture.LOAM, root_depth_m=0.30)
    assert soil.total_available_water_mm == pytest.approx((0.25 - 0.12) * 0.30 * 1000)


def test_deficit_and_moisture_are_inverse():
    """Converting a deficit to moisture and back returns the same deficit."""
    soil = SoilProfile.from_texture(SoilTexture.CLAY, root_depth_m=0.45)
    assert soil.deficit_at_moisture(soil.moisture_at_deficit(17.0)) == pytest.approx(17.0)


def test_moisture_is_clamped_at_zero_for_an_overshooting_model():
    """A deficit larger than the reservoir cannot produce a negative water content."""
    soil = SoilProfile.from_texture(SoilTexture.SANDY, root_depth_m=0.15)
    assert soil.moisture_at_deficit(10_000.0) == 0.0


def test_available_fraction_spans_wilting_point_to_field_capacity():
    """Zero at the wilting point, one at field capacity, clamped outside."""
    soil = SoilProfile.from_texture(SoilTexture.LOAM)
    assert soil.available_fraction(0.0) == pytest.approx(1.0)
    assert soil.available_fraction(soil.total_available_water_mm) == pytest.approx(0.0)
    assert soil.available_fraction(soil.total_available_water_mm * 2) == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("field_capacity", "wilting_point", "saturation", "depth"),
    [
        (0.20, 0.30, 0.45, 0.3),  # wilting point above field capacity
        (0.50, 0.20, 0.40, 0.3),  # field capacity above saturation
        (0.25, 0.12, 0.45, 0.0),  # no root zone
    ],
)
def test_impossible_reservoirs_are_refused(field_capacity, wilting_point, saturation, depth):
    """A reservoir no ground could have is refused at construction."""
    with pytest.raises(InvalidSoilProfile):
        SoilProfile(
            texture=SoilTexture.CUSTOM,
            field_capacity=field_capacity,
            wilting_point=wilting_point,
            root_depth_m=depth,
            saturation=saturation,
        )


def test_fingerprint_changes_with_the_reservoir():
    """The fingerprint is what makes a stale fit detectable after a soil change."""
    shallow = SoilProfile.from_texture(SoilTexture.LOAM, root_depth_m=0.30)
    deep = SoilProfile.from_texture(SoilTexture.LOAM, root_depth_m=0.60)
    assert shallow.fingerprint() != deep.fingerprint()
    assert shallow.fingerprint() == SoilProfile.from_dict(shallow.to_dict()).fingerprint()
