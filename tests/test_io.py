"""Tests for eocube.io against small synthetic GeoTIFFs written per test.

No real imagery is used or needed. The whole module is skipped when rasterio is
absent, so CI without the raster extra still passes.
"""

import json
import math

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # noqa: E402

from eocube import Verdict, run_all_gates  # noqa: E402
from eocube.io import _invalid_fraction, describe, describe_many, to_descriptor  # noqa: E402

CRS = "EPSG:32635"
ORIGIN = (500000.0, 6100000.0)


@pytest.fixture
def make_tif(tmp_path):
    """Write a GeoTIFF and return its path.

    ``data`` is (rows, cols) or (bands, rows, cols). Pixel size defaults to 10 m
    square; pass ``res=(x, y)`` for non-square pixels and ``origin`` for a
    CRS other than the default UTM zone.
    """

    def _make(name="layer", data=None, *, crs=CRS, res=(10.0, 10.0), nodata=None,
              dtype="float32", origin=ORIGIN):
        if data is None:
            data = np.ones((20, 20), dtype=dtype)
        data = np.asarray(data, dtype=dtype)
        if data.ndim == 2:
            data = data[np.newaxis]
        path = tmp_path / f"{name}.tif"
        with rasterio.open(
            path, "w", driver="GTiff",
            height=data.shape[1], width=data.shape[2], count=data.shape[0],
            dtype=dtype, crs=crs, nodata=nodata,
            transform=from_origin(origin[0], origin[1], res[0], res[1]),
        ) as dst:
            dst.write(data)
        return path

    return _make


# --------------------------------------------------------------------------
# observed properties
# --------------------------------------------------------------------------
def test_observed_properties_read_from_file(make_tif):
    p = make_tif("s1", np.zeros((30, 40)), nodata=-9999.0)
    layer = describe(p)
    assert layer["name"] == "s1"
    assert layer["crs"] == CRS
    assert layer["transform"] == (10.0, 0.0, ORIGIN[0], 0.0, -10.0, ORIGIN[1])
    assert layer["gsd"] == 10.0
    assert layer["dtype"] == "float32"
    assert layer["nodata"] == -9999.0
    assert (layer["width"], layer["height"], layer["band_count"]) == (40, 30, 1)
    assert layer["provenance"]["conflicts"] == []
    assert layer["provenance"]["observed"]["crs"] == CRS


def test_integer_dtype_and_no_nodata(make_tif):
    p = make_tif("dn", np.ones((10, 10)), dtype="uint16")
    layer = describe(p, "B08")
    assert layer["name"] == "B08"
    assert layer["dtype"] == "uint16"
    assert layer["nodata"] is None
    assert layer["invalid_fraction"] == 0.0


# --------------------------------------------------------------------------
# declared versus observed
# --------------------------------------------------------------------------
def test_declared_crs_conflict_recorded_and_observed_wins(make_tif):
    layer = describe(make_tif(), declared={"crs": "EPSG:4326", "modality": "sar"})
    assert layer["crs"] == CRS
    assert any("crs" in c and "EPSG:4326" in c for c in layer["provenance"]["conflicts"])
    assert "crs" not in layer["provenance"]["declared"]
    assert layer["modality"] == "sar"
    assert layer["provenance"]["declared"]["modality"] == "sar"


def test_declared_gsd_conflict_recorded_and_observed_wins(make_tif):
    layer = describe(make_tif(), declared={"gsd": 20.0})
    assert layer["gsd"] == 10.0
    assert any("gsd" in c for c in layer["provenance"]["conflicts"])


def test_agreeing_declaration_is_not_a_conflict(make_tif):
    """An int 10 and a float 10.0, or a differently cased CRS string, agree.

    Comparing their string forms reported spurious conflicts, which trains a
    reader to ignore the conflict list — the opposite of its purpose.
    """
    layer = describe(make_tif(), declared={"gsd": 10, "crs": "epsg:32635",
                                           "transform": [10, 0, 500000, 0, -10, 6100000]})
    assert layer["provenance"]["conflicts"] == []


def test_declared_only_fields_marked_unverifiable(make_tif):
    layer = describe(make_tif(), declared={"terrain_corrected": True, "units": "db"})
    assert set(layer["provenance"]["declared"]["_unverifiable_from_pixels"]) == {
        "terrain_corrected", "units"}


# --------------------------------------------------------------------------
# invalid fraction
# --------------------------------------------------------------------------
def test_invalid_fraction_counts_nodata(make_tif):
    data = np.ones((10, 10))
    data[0, :7] = -9999.0                       # 7 of 100 pixels
    layer = describe(make_tif(data=data, nodata=-9999.0))
    assert layer["invalid_fraction"] == pytest.approx(0.07)


def test_invalid_fraction_counts_nan_without_nodata(make_tif):
    data = np.ones((10, 10))
    data[2, :3] = np.nan
    data[5, 5] = np.inf
    layer = describe(make_tif(data=data))
    assert layer["invalid_fraction"] == pytest.approx(0.04)


def test_nonfinite_nodata_not_double_counted(make_tif):
    """A pixel that is both nodata and non-finite is one invalid pixel, not two."""
    data = np.ones((10, 10))
    data[0, :5] = -np.inf
    layer = describe(make_tif(data=data, nodata=float("-inf")))
    assert layer["invalid_fraction"] == pytest.approx(0.05)


def test_read_stats_false_omits_invalid_fraction(make_tif):
    data = np.ones((10, 10))
    data[0, 0] = -9999.0
    layer = describe(make_tif(data=data, nodata=-9999.0), read_stats=False)
    assert "invalid_fraction" not in layer
    assert "invalid_fraction" not in layer["provenance"]["observed"]


def test_unknown_invalid_fraction_is_not_treated_as_zero(make_tif):
    """The describe() docstring promises the quality gate will say so."""
    layer = describe(make_tif(), read_stats=False)
    rep = run_all_gates([layer], skip=["geometry_crs", "resolution", "temporal_alignment",
                                       "sensor_physics"])
    gate = rep.results[0]
    assert gate.gate == "quality_missingness"
    assert gate.evidence["per_layer"][layer["name"]]["invalid_fraction"] is None
    assert any("not measured" in d for d in gate.decisions)


# --------------------------------------------------------------------------
# non-square pixels
# --------------------------------------------------------------------------
def test_non_square_pixels_produce_conflict(make_tif):
    layer = describe(make_tif(res=(10.0, 20.0)))
    assert layer["gsd"] == 10.0
    assert layer["gsd_y"] == 20.0
    assert any("non-square" in c for c in layer["provenance"]["conflicts"])


def test_square_pixels_have_no_gsd_y(make_tif):
    layer = describe(make_tif())
    assert "gsd_y" not in layer


# --------------------------------------------------------------------------
# multi-band and decimation (Task 2a, 2b)
# --------------------------------------------------------------------------
def test_invalid_fraction_checks_every_band(make_tif):
    """Band 1 is clean, band 2 is 30% nodata. Reading band 1 only reported 0%."""
    data = np.ones((3, 10, 10))
    data[1, :3, :] = -9999.0
    data[2, 0, :5] = -9999.0
    layer = describe(make_tif(data=data, nodata=-9999.0))
    assert layer["invalid_fraction_by_band"] == pytest.approx([0.0, 0.30, 0.05])
    # a pixel is invalid if any band is invalid there; rows 0-2 cover band 3's gap
    assert layer["invalid_fraction"] == pytest.approx(0.30)


def test_invalid_fraction_union_across_bands(make_tif):
    data = np.ones((2, 10, 10))
    data[0, 0, :] = -9999.0                     # row 0 in band 1
    data[1, 9, :] = -9999.0                     # row 9 in band 2
    layer = describe(make_tif(data=data, nodata=-9999.0))
    assert layer["invalid_fraction"] == pytest.approx(0.20)


def test_full_read_is_recorded_as_a_count(make_tif):
    layer = describe(make_tif(data=np.ones((20, 20))))
    s = layer["invalid_fraction_sampling"]
    assert s["decimation_step"] == 1
    assert s["estimated"] is False
    assert s["pixels_read"] == s["pixels_total"] == 400


def test_decimated_read_is_recorded_as_an_estimate(make_tif):
    p = make_tif(data=np.ones((100, 100)))
    layer = describe(p, max_stat_pixels=400)
    s = layer["invalid_fraction_sampling"]
    assert s["decimation_step"] == 5
    assert s["estimated"] is True
    assert s["pixels_read"] == 400 and s["pixels_total"] == 10_000
    assert layer["provenance"]["observed"]["invalid_fraction_sampling"] == s


def test_decimation_appears_in_quality_gate_evidence(make_tif):
    data = np.ones((100, 100))
    data[:50, :] = np.nan
    layer = describe(make_tif(data=data), max_stat_pixels=400)
    rep = run_all_gates([layer], skip=["geometry_crs", "resolution", "temporal_alignment",
                                       "sensor_physics"])
    gate = rep.results[0]
    entry = gate.evidence["per_layer"][layer["name"]]
    assert entry["sampling"]["decimation_step"] == 5
    assert any("estimated" in r for r in gate.reasons)


def test_invalid_fraction_helper_returns_sampling(make_tif):
    with rasterio.open(make_tif(data=np.ones((10, 10)))) as src:
        out = _invalid_fraction(src)
    assert out["invalid_fraction"] == 0.0
    assert out["decimation_step"] == 1


# --------------------------------------------------------------------------
# descriptor
# --------------------------------------------------------------------------
def test_descriptor_survives_json_and_round_trips(make_tif):
    a = make_tif("a", np.ones((10, 10)), nodata=-9999.0)
    b = make_tif("b", np.ones((10, 10)), res=(10.0, 20.0))
    layers = describe_many([
        (a, "S1_VV", {"modality": "sar", "units": "linear", "calibrated": True,
                      "terrain_corrected": True, "crs": "EPSG:4326"}),
        (b, "S2_B08", {"modality": "optical", "reflectance": True}),
    ])
    report = run_all_gates(layers)
    doc = to_descriptor(layers, report, cube_id="test-cube")

    text = json.dumps(doc, allow_nan=False)      # strict JSON, no default=str
    back = json.loads(text)
    assert back == json.loads(json.dumps(back, allow_nan=False))

    assert back["cube_id"] == "test-cube"
    assert back["verdict"] == ("accepted" if report.ok else "refused")
    assert back["contract_report"] == json.loads(json.dumps(report.to_dict()))
    assert [l["name"] for l in back["layers"]] == ["S1_VV", "S2_B08"]
    assert back["layers"][0]["transform"] == list(layers[0]["transform"])
    assert back["layers"][0]["provenance"]["observed"]["crs"] == CRS
    assert len(back["metadata_conflicts"]) == 2  # declared CRS + non-square pixels

    # the deserialised layers drive the gates to the same verdicts
    again = run_all_gates(back["layers"])
    assert [r.verdict for r in again.results] == [r.verdict for r in report.results]


def test_descriptor_with_nan_nodata_is_strict_json(make_tif):
    """NaN is not valid JSON; a browser's JSON.parse rejects the whole document."""
    layer = describe(make_tif(nodata=float("nan")))
    assert math.isnan(layer["nodata"])          # the layer itself keeps the float
    doc = to_descriptor([layer])
    back = json.loads(json.dumps(doc, allow_nan=False))
    assert back["layers"][0]["nodata"] == "nan"
    assert back["verdict"] == "unchecked"


# --------------------------------------------------------------------------
# regressions from the first real-data run (Sentinel-2, docs/real_data_run.md)
# --------------------------------------------------------------------------
S2_DEG = 8.983152841195215e-05      # "10 m" in degrees, as in the Vilnius tiles
VILNIUS = (25.22038126, 54.72177385)


def test_projected_gsd_is_in_metres(make_tif):
    layer = describe(make_tif())
    assert layer["gsd_units"] == "metre"
    assert "gsd_ground_m" not in layer


def test_geographic_gsd_is_labelled_degrees_with_ground_size(make_tif):
    p = make_tif(data=np.ones((20, 20)), crs="EPSG:4326", res=(S2_DEG, S2_DEG),
                 origin=VILNIUS)
    layer = describe(p)
    assert layer["gsd_units"] == "degree"
    assert layer["gsd"] == pytest.approx(S2_DEG)
    gx, gy = layer["gsd_ground_m"]
    assert gx == pytest.approx(5.78, abs=0.05)   # east-west shrinks with cos(latitude)
    assert gy == pytest.approx(10.0, abs=0.05)
    assert any("not square" in c for c in layer["provenance"]["conflicts"])


def test_zero_fill_without_nodata_is_reported(make_tif):
    data = np.ones((4, 10, 10))
    data[:, :4, :] = 0.0                          # top 40% zero in every band
    layer = describe(make_tif(data=data))
    assert layer["nodata"] is None
    assert layer["invalid_fraction"] == 0.0       # nothing flagged, so nothing counted...
    assert layer["zero_fill_fraction"] == pytest.approx(0.4)   # ...but the evidence is kept
    assert layer["invalid_fraction_sampling"]["nodata_source"] is None


def test_zero_in_some_bands_is_not_zero_fill(make_tif):
    data = np.ones((3, 10, 10))
    data[0] = 0.0
    assert describe(make_tif(data=data))["zero_fill_fraction"] == 0.0


def test_zero_fill_fraction_absent_when_file_declares_nodata(make_tif):
    layer = describe(make_tif(data=np.zeros((2, 5, 5)), nodata=-1.0))
    assert "zero_fill_fraction" not in layer


def test_declared_nodata_used_when_file_has_none(make_tif):
    data = np.ones((2, 10, 10))
    data[:, :4, :] = 0.0
    layer = describe(make_tif(data=data), declared={"nodata": 0})
    assert layer["invalid_fraction"] == pytest.approx(0.4)
    assert layer["nodata"] is None                # the observed value is unchanged
    assert layer["provenance"]["declared"]["nodata"] == 0
    assert layer["provenance"]["conflicts"] == []
    assert layer["invalid_fraction_sampling"]["nodata_source"] == "declared"


def test_declared_nodata_contradicting_file_still_conflicts(make_tif):
    layer = describe(make_tif(nodata=-9999.0), declared={"nodata": 0})
    assert layer["nodata"] == -9999.0
    assert any("declared nodata" in c for c in layer["provenance"]["conflicts"])


def test_value_range_excludes_nodata(make_tif):
    data = np.array([[0.1, 0.5], [-9999.0, 0.3]])
    layer = describe(make_tif(data=data, nodata=-9999.0))
    assert layer["value_range"] == pytest.approx([0.1, 0.5])


def test_float64_multiband_reads(make_tif):
    """The real embeddings are 64-band float64; the fixtures had been float32."""
    layer = describe(make_tif(data=np.full((64, 6, 6), 0.125), dtype="float64"))
    assert layer["dtype"] == "float64"
    assert layer["band_count"] == 64
    assert len(layer["invalid_fraction_by_band"]) == 64
    json.dumps(to_descriptor([layer]), allow_nan=False)


def test_real_shaped_tile_is_refused_by_quality_gate(make_tif):
    """End to end on a miniature of the Vilnius embedding tile: EPSG:4326, no
    nodata tag, top 40% zero vectors. It was accepted with 0% invalid."""
    data = np.full((4, 10, 10), 0.5)
    data[:, :4, :] = 0.0
    layer = describe(make_tif(data=data, crs="EPSG:4326", res=(S2_DEG, S2_DEG),
                              origin=VILNIUS), "emb")
    rep = run_all_gates([layer])
    by_gate = {r.gate: r for r in rep.results}
    assert by_gate["quality_missingness"].verdict is Verdict.REFUSE
    assert by_gate["temporal_alignment"].verdict is Verdict.NOT_EVALUATED
    assert by_gate["sensor_physics"].verdict is Verdict.NOT_EVALUATED
    assert not rep.ok


# --------------------------------------------------------------------------
# physically implausible values that read as data (Sentinel-2 swath edge)
# --------------------------------------------------------------------------
S2_B02_RANGE = (50, 12000)    # L2A DN over land; below ~50 is not a credible reflectance


def _swath_edge_b02():
    """A 10 x 10 B02 chip: plausible reflectance, with the left 3 columns set to 1 DN,
    as seen at ITCSM_08 on 2019-06-25 (32ULC). Nodata is 0, so nothing is flagged."""
    data = np.full((10, 10), 1200, dtype="uint16")
    data[:, :3] = 1
    return data


def test_implausible_fraction_measured_against_declared_range(make_tif):
    p = make_tif("b02", _swath_edge_b02(), dtype="uint16", nodata=0)
    layer = describe(p, declared={"plausible_range": S2_B02_RANGE})
    assert layer["invalid_fraction"] == 0.0              # nodata cannot see it...
    assert layer["implausible_fraction"] == pytest.approx(0.3)   # ...the physical bound can
    assert layer["implausible_fraction_by_band"] == [pytest.approx(0.3)]
    assert layer["provenance"]["declared"]["plausible_range"] == S2_B02_RANGE


def test_swath_edge_fill_is_refused_by_quality_gate(make_tif):
    p = make_tif("b02", _swath_edge_b02(), dtype="uint16", nodata=0)
    layer = describe(p, declared={"plausible_range": S2_B02_RANGE})
    rep = run_all_gates([layer], skip=["geometry_crs", "resolution", "temporal_alignment",
                                       "sensor_physics"])
    gate = rep.results[0]
    assert gate.verdict is Verdict.REFUSE
    assert "outside the declared physical range" in gate.reasons[0]


def test_gap_fill_policy_does_not_excuse_implausible_values(make_tif):
    p = make_tif("b02", _swath_edge_b02(), dtype="uint16", nodata=0)
    layer = describe(p, declared={"plausible_range": S2_B02_RANGE})
    rep = run_all_gates([layer], skip=["geometry_crs", "resolution", "temporal_alignment",
                                       "sensor_physics"], gap_fill="nearest")
    assert rep.results[0].verdict is Verdict.REFUSE


def test_nodata_pixels_are_not_counted_as_implausible(make_tif):
    data = np.full((10, 10), 1200, dtype="uint16")
    data[:, :3] = 0                                      # flagged fill, not implausible data
    layer = describe(make_tif("b02", data, dtype="uint16", nodata=0),
                     declared={"plausible_range": S2_B02_RANGE})
    assert layer["invalid_fraction"] == pytest.approx(0.3)
    assert layer["implausible_fraction"] == 0.0


def test_no_declared_range_means_no_plausibility_check(make_tif):
    layer = describe(make_tif("b02", _swath_edge_b02(), dtype="uint16", nodata=0))
    assert "implausible_fraction" not in layer


def test_declared_range_without_stats_is_unmeasured_not_clean(make_tif):
    p = make_tif("b02", _swath_edge_b02(), dtype="uint16", nodata=0)
    layer = describe(p, declared={"plausible_range": S2_B02_RANGE}, read_stats=False)
    rep = run_all_gates([layer], skip=["geometry_crs", "resolution", "temporal_alignment",
                                       "sensor_physics"])
    gate = rep.results[0]
    assert gate.verdict is Verdict.PASS_WITH_DECISION
    assert any("plausible range" in d and "not measured" in d for d in gate.decisions)
