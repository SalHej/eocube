"""eocube — contract-checked multi-source Earth Observation data cubes.

Reference implementation accompanying:
    Al-Heejawi, S. M. A. (2026). Agentic large language model workflows for Earth
    Observation. EarthArXiv. https://doi.org/10.31223/X5TN59
"""

__version__ = "0.2.0"

from .contracts import (  # noqa: F401
    Verdict, GateResult, ContractReport, GATES, run_all_gates,
    check_geometry_crs, check_resolution, check_temporal_alignment,
    check_quality_missingness, check_sensor_physics,
)
from .ablation import (  # noqa: F401
    RunRecord, ArmResult, AblationResult, run_ablation, BASELINE,
)

__all__ = [
    "Verdict", "GateResult", "ContractReport", "GATES", "run_all_gates",
    "check_geometry_crs", "check_resolution", "check_temporal_alignment",
    "check_quality_missingness", "check_sensor_physics",
    "RunRecord", "ArmResult", "AblationResult", "run_ablation", "BASELINE",
]

# eocube.io needs rasterio; import it explicitly when you have it installed:
#     from eocube.io import describe, to_descriptor
