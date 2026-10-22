"""Worked example: a four-source peatland moisture cube.

Reproduces the motivating case from Section 6 of the preprint — Sentinel-1 C-band
SAR, TerraSAR-X X-band SAR, EnMAP hyperspectral and a meteorological series, all
acquired asynchronously — and shows the contract report both refusing and accepting.

Run:  python examples/peatland_cube.py
"""
from datetime import datetime
from eocube import run_all_gates

TARGET = datetime(2025, 7, 15)

broken = [
    dict(name="S1_VV", modality="sar", units="db", calibrated=True,
         terrain_corrected=False, gsd=10.0, crs="EPSG:32635",
         transform=(10, 0, 500000, 0, -10, 6100000), width=512, height=512,
         invalid_fraction=0.02),
    dict(name="S2_B08", modality="optical", reflectance=True, gsd=10.0,
         crs="EPSG:4326",
         transform=(10, 0, 500003, 0, -10, 6100004), width=512, height=512,
         invalid_fraction=0.41, qa_mask_available=True, qa_mask_applied=False),
    dict(name="TSX_HH", modality="sar", units="linear", calibrated=True,
         terrain_corrected=True, gsd=3.0, crs="EPSG:32635",
         transform=(3, 0, 500000, 0, -3, 6100000), width=1706, height=1706,
         invalid_fraction=0.0),
]
acq_broken = {
    "S1": [datetime(2025, 7, 12), datetime(2025, 7, 24)],
    "TSX": [datetime(2025, 6, 30)],
    "EnMAP": [datetime(2025, 5, 2)],
}

print("=" * 78)
print("CUBE A -- as delivered")
print("=" * 78)
print(run_all_gates(broken, acquisitions=acq_broken, target_time=TARGET,
                    band_expressions=["(S2_B08 - S1_VV) / (S2_B08 + S1_VV)"]))

fixed = [
    dict(name="S1_VV", modality="sar", units="linear", calibrated=True,
         terrain_corrected=True, gsd=10.0, crs="EPSG:32635",
         transform=(10, 0, 500000, 0, -10, 6100000), width=512, height=512,
         invalid_fraction=0.02),
    dict(name="S2_B08", modality="optical", reflectance=True, gsd=10.0,
         crs="EPSG:32635",
         transform=(10, 0, 500000, 0, -10, 6100000), width=512, height=512,
         invalid_fraction=0.18, qa_mask_available=True, qa_mask_applied=True),
    dict(name="TSX_HH", modality="sar", units="linear", calibrated=True,
         terrain_corrected=True, gsd=3.0, crs="EPSG:32635",
         transform=(3, 0, 500000, 0, -3, 6100000), width=1706, height=1706,
         invalid_fraction=0.0),
]
acq_fixed = {
    "S1": [datetime(2025, 7, 12), datetime(2025, 7, 6)],
    "TSX": [datetime(2025, 6, 30)],
    "EnMAP": [datetime(2025, 5, 2)],
}

print("\n" + "=" * 78)
print("CUBE B -- after remediation")
print("=" * 78)
report = run_all_gates(fixed, acquisitions=acq_fixed, target_time=TARGET,
                       decay_alpha=0.05, window_days=7.0)
print(report)
print("\nPROVENANCE LOG")
for line in report.provenance():
    print("  -", line)
