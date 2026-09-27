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
from .cycles import CycleClosure, CyclePolicy, CycleTracker, DryDownCycle, TrackerState, WaterSource
from .estimator import LineFit, TemperatureAwareFit, fit_with_temperature, theil_sen, two_point_line
from .liveness import (
    CADENCE_LEARNING_MIN_S,
    CADENCE_LEARNING_MULTIPLE,
    CADENCE_MEMORY_S,
    CADENCE_TOLERANCE,
    LIVENESS_CEILING_S,
    ProbeCadence,
    SensingWitness,
)
from .placement import (
    PlacementConfidence,
    PlacementPolicy,
    PlacementSuspicion,
    PlacementVerdict,
    assess_placement,
)
from .rain import RainPolicy, RainSensorKind, RainUpdate, RainWitness
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
from .spread import Excluded, GroupSpread, ProbeReading, spreads_by_group, worst_spread
from .units import deficit_to_mm, depth_to_m, raw_index_to_percent, temperature_to_celsius

__all__ = [
    "Admission",
    "AdmissionPolicy",
    "CADENCE_LEARNING_MIN_S",
    "CADENCE_LEARNING_MULTIPLE",
    "CADENCE_MEMORY_S",
    "CADENCE_TOLERANCE",
    "CalibratedReading",
    "CalibrationFit",
    "CalibrationSession",
    "CalibrationStatus",
    "CycleClosure",
    "CyclePolicy",
    "CycleTracker",
    "DryDownCycle",
    "Excluded",
    "GateVerdict",
    "GroupSpread",
    "InvalidSoilProfile",
    "InvalidationReason",
    "LIVENESS_CEILING_S",
    "LineFit",
    "Observation",
    "PlacementConfidence",
    "PlacementPolicy",
    "PlacementSuspicion",
    "PlacementVerdict",
    "ProbeCadence",
    "ProbeLiveness",
    "ProbeReading",
    "QualityGates",
    "RainPolicy",
    "RainSensorKind",
    "RainUpdate",
    "RainWitness",
    "RejectionReason",
    "SOIL_TEXTURE_DEFAULTS",
    "Sample",
    "SampleBuffer",
    "SensingWitness",
    "SoilProfile",
    "SoilTexture",
    "TemperatureAwareFit",
    "TrackerState",
    "WaterSource",
    "assess_placement",
    "deficit_to_mm",
    "depth_to_m",
    "fit_with_temperature",
    "line_summary",
    "probe_liveness",
    "raw_index_to_percent",
    "spreads_by_group",
    "temperature_to_celsius",
    "theil_sen",
    "two_point_line",
    "worst_spread",
]
