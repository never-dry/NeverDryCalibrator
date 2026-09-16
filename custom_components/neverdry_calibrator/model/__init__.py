"""Pure calibration domain: no Home Assistant import lives below this package.

The split is enforced by ``tests/test_architecture.py``. Everything here is
plain Python over floats and datetimes, which is what makes the interesting part
of this integration, deciding whether a cheap probe may be believed, testable
without a Home Assistant runtime and reusable outside one.
"""

from .calibration import (
    CalibratedReading,
    CalibrationFit,
    CalibrationSession,
    CalibrationStatus,
    GateVerdict,
    InvalidationReason,
    QualityGates,
    line_summary,
)
from .cycles import CycleClosure, CyclePolicy, CycleTracker, DryDownCycle, TrackerState
from .estimator import LineFit, TemperatureAwareFit, fit_with_temperature, theil_sen, two_point_line
from .placement import (
    PlacementConfidence,
    PlacementPolicy,
    PlacementSuspicion,
    PlacementVerdict,
    assess_placement,
)
from .samples import (
    Admission,
    AdmissionPolicy,
    Observation,
    ProbeLiveness,
    RejectionReason,
    Sample,
    SampleBuffer,
    probe_liveness,
)
from .soil import SOIL_TEXTURE_DEFAULTS, InvalidSoilProfile, SoilProfile, SoilTexture
from .units import deficit_to_mm, depth_to_m, raw_index_to_percent, temperature_to_celsius

__all__ = [
    "SOIL_TEXTURE_DEFAULTS",
    "Admission",
    "AdmissionPolicy",
    "CalibratedReading",
    "CalibrationFit",
    "CalibrationSession",
    "CalibrationStatus",
    "CycleClosure",
    "CyclePolicy",
    "CycleTracker",
    "DryDownCycle",
    "GateVerdict",
    "InvalidSoilProfile",
    "InvalidationReason",
    "LineFit",
    "Observation",
    "PlacementConfidence",
    "PlacementPolicy",
    "PlacementSuspicion",
    "PlacementVerdict",
    "ProbeLiveness",
    "QualityGates",
    "RejectionReason",
    "Sample",
    "SampleBuffer",
    "SoilProfile",
    "SoilTexture",
    "TemperatureAwareFit",
    "TrackerState",
    "assess_placement",
    "deficit_to_mm",
    "depth_to_m",
    "fit_with_temperature",
    "line_summary",
    "probe_liveness",
    "raw_index_to_percent",
    "temperature_to_celsius",
    "theil_sen",
    "two_point_line",
]
