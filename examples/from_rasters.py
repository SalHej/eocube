"""Build a cube description from real rasters and emit a machine-readable descriptor.

Requires rasterio:  pip install "eocube[raster]"

Point the paths at real files. Properties that cannot be read from pixels —
modality, calibration, terrain correction — are supplied as `declared` and are
recorded as assertions, not measurements. A declaration that contradicts the file
is logged as a conflict and the observed value wins.

Run:  python examples/from_rasters.py
"""
from datetime import datetime
from eocube import run_all_gates
from eocube.io import describe_many, to_descriptor, write_descriptor

TARGET = datetime(2025, 7, 15)

SOURCES = [
    ("data/s1_vv.tif", "S1_VV", dict(
        modality="sar", units="linear", calibrated=True, terrain_corrected=True,
        sensor="Sentinel-1", processing_level="GRD", acquisition_time="2025-07-12")),
    ("data/s2_b08.tif", "S2_B08", dict(
        modality="optical", reflectance=True, qa_mask_available=True,
        qa_mask_applied=True, sensor="Sentinel-2", processing_level="L2A",
        acquisition_time="2025-07-14")),
]

if __name__ == "__main__":
    layers = describe_many(SOURCES)

    for l in layers:
        print(f"{l['name']}: {l['gsd']} m, {l['crs']}, "
              f"{l.get('invalid_fraction', 0):.1%} invalid")
        for c in l["provenance"]["conflicts"]:
            print("  CONFLICT:", c)

    report = run_all_gates(
        layers,
        acquisitions={"S1": [datetime(2025, 7, 12)], "S2": [datetime(2025, 7, 14)]},
        target_time=TARGET, decay_alpha=0.05,
    )
    print("\n" + str(report))

    doc = to_descriptor(layers, report, cube_id="example-2025-07-15",
                        target_time=TARGET)
    path = write_descriptor(doc, "cube_descriptor.json")
    print(f"\nMachine-readable descriptor written to {path}")
