from datetime import datetime
from eocube import (Verdict, run_all_gates, check_geometry_crs, check_resolution,
                    check_temporal_alignment, check_quality_missingness,
                    check_sensor_physics)

def L(name, **kw):
    base = dict(name=name, crs="EPSG:32635", transform=(10, 0, 500000, 0, -10, 6100000),
                width=100, height=100, gsd=10.0, modality="optical", reflectance=True,
                invalid_fraction=0.0)
    base.update(kw); return base

def test_mixed_crs_refuses():
    assert check_geometry_crs([L("a"), L("b", crs="EPSG:4326")]).verdict is Verdict.REFUSE

def test_subpixel_offset_refuses():
    r = check_geometry_crs([L("a"), L("b", transform=(10, 0, 500004, 0, -10, 6100000))])
    assert r.verdict is Verdict.REFUSE

def test_aligned_grid_passes():
    assert check_geometry_crs([L("a"), L("b")]).verdict is Verdict.PASS

def test_no_layers_refuses():
    assert check_geometry_crs([]).verdict is Verdict.REFUSE

def test_upsampling_blocked_by_default():
    r = check_resolution([L("fine", gsd=10.0), L("coarse", gsd=60.0)], target_gsd=10.0)
    assert r.verdict is Verdict.REFUSE

def test_upsampling_allowed_is_logged():
    r = check_resolution([L("fine", gsd=10.0), L("coarse", gsd=60.0)],
                         target_gsd=10.0, allow_upsampling=True)
    assert r.verdict is Verdict.PASS_WITH_DECISION
    assert any("upsampled" in d for d in r.decisions)

def test_default_target_avoids_upsampling():
    r = check_resolution([L("a", gsd=10.0), L("b", gsd=30.0)])
    assert r.evidence["target_gsd"] == 30.0

def test_future_acquisition_is_leakage():
    r = check_temporal_alignment({"S1": [datetime(2025, 7, 20)]}, datetime(2025, 7, 15))
    assert r.verdict is Verdict.REFUSE
    assert "leakage" in " ".join(r.reasons)

def test_future_allowed_when_justified():
    r = check_temporal_alignment({"S1": [datetime(2025, 7, 20)]}, datetime(2025, 7, 15),
                                 forbid_future=False, window_days=30)
    assert r.verdict is not Verdict.REFUSE

def test_decay_weight_recorded():
    r = check_temporal_alignment({"S1": [datetime(2025, 7, 5)]}, datetime(2025, 7, 15),
                                 window_days=30, decay_alpha=0.1)
    assert 0 < r.evidence["per_source"]["S1"]["weight"] < 1.0

def test_empty_acquisitions_refuses():
    r = check_temporal_alignment({"S1": []}, datetime(2025, 7, 15))
    assert r.verdict is Verdict.REFUSE

def test_unapplied_qa_mask_refuses():
    r = check_quality_missingness([L("a", qa_mask_available=True, qa_mask_applied=False)])
    assert r.verdict is Verdict.REFUSE

def test_gapfill_must_be_declared():
    assert check_quality_missingness([L("a", invalid_fraction=0.5)]).verdict is Verdict.REFUSE
    r = check_quality_missingness([L("a", invalid_fraction=0.5)], gap_fill="linear-interp")
    assert r.verdict is Verdict.PASS_WITH_DECISION

def test_minor_invalid_is_logged_not_refused():
    r = check_quality_missingness([L("a", invalid_fraction=0.05)])
    assert r.verdict is Verdict.PASS_WITH_DECISION

def test_uncorrected_sar_refuses():
    r = check_sensor_physics([L("s1", modality="sar", units="db", calibrated=True,
                                terrain_corrected=False)])
    assert r.verdict is Verdict.REFUSE

def test_uncalibrated_sar_refuses():
    r = check_sensor_physics([L("s1", modality="sar", units="db", calibrated=False,
                                terrain_corrected=True)])
    assert r.verdict is Verdict.REFUSE

def test_missing_sar_units_refuses():
    r = check_sensor_physics([L("s1", modality="sar", calibrated=True,
                                terrain_corrected=True)])
    assert r.verdict is Verdict.REFUSE

def test_cross_modality_index_refuses():
    layers = [L("B8"), L("VV", modality="sar", units="linear", calibrated=True,
                terrain_corrected=True)]
    r = check_sensor_physics(layers, band_expressions=["(B8 - VV)/(B8 + VV)"])
    assert r.verdict is Verdict.REFUSE

def test_db_addition_refuses():
    layers = [L("VV", modality="sar", units="db", calibrated=True, terrain_corrected=True),
              L("VH", modality="sar", units="db", calibrated=True, terrain_corrected=True)]
    assert check_sensor_physics(layers, band_expressions=["mean(VV, VH)"]).verdict is Verdict.REFUSE

def test_raw_dn_optical_is_flagged():
    r = check_sensor_physics([L("B8", reflectance=False)])
    assert r.verdict is Verdict.PASS_WITH_DECISION

def _sar(name, **kw):
    return L(name, modality="sar", units="linear", calibrated=True, terrain_corrected=True, **kw)

def test_band_name_not_matched_inside_longer_name():
    # B8 is optical, B80 is radar; the expression uses only B80
    r = check_sensor_physics([L("B8"), _sar("B80")], band_expressions=["B80 * 2"])
    assert r.verdict is Verdict.PASS
    assert r.evidence["layers_by_expression"]["B80 * 2"] == ["B80"]

def test_band_name_not_matched_as_suffix():
    # VV (radar) must not be found inside S2_VV (optical)
    r = check_sensor_physics([L("S2_VV"), _sar("VV")], band_expressions=["S2_VV / 3"])
    assert r.verdict is Verdict.PASS

def test_tokenised_match_still_refuses_real_mixing():
    r = check_sensor_physics([L("B8"), _sar("B80")], band_expressions=["(B8-B80)/(B8+B80)"])
    assert r.verdict is Verdict.REFUSE
    assert r.evidence["layers_by_expression"]["(B8-B80)/(B8+B80)"] == ["B8", "B80"]

def test_missing_invalid_fraction_is_unknown_not_zero():
    layer = L("a"); del layer["invalid_fraction"]
    r = check_quality_missingness([layer])
    assert r.verdict is Verdict.PASS_WITH_DECISION
    assert r.evidence["per_layer"]["a"]["invalid_fraction"] is None

def test_gate_ablation():
    layers = [L("a"), L("b", crs="EPSG:4326")]
    assert not run_all_gates(layers).ok
    assert run_all_gates(layers, skip=["geometry_crs"]).ok

def test_report_serialises():
    rep = run_all_gates([L("a"), L("b")])
    d = rep.to_dict()
    assert "gates" in d and isinstance(d["ok"], bool)
    assert isinstance(rep.provenance(), list)

# regressions from the first real-data run (Sentinel-2, docs/real_data_run.md)
DEG = dict(crs="EPSG:4326", gsd=8.983e-05, gsd_units="degree")

def test_mixed_gsd_units_refuses():
    r = check_resolution([L("utm"), L("geo", **DEG)])
    assert r.verdict is Verdict.REFUSE
    assert "different units" in r.reasons[0]

def test_metre_target_on_degree_grid_refuses():
    r = check_resolution([L("geo", **DEG)], target_gsd=10.0)
    assert r.verdict is Verdict.REFUSE

def test_degree_gsd_not_labelled_as_metres():
    r = check_resolution([L("a", **DEG), L("b", **DEG)])
    assert r.verdict is Verdict.PASS_WITH_DECISION
    assert not any(" m)" in d or " m " in d for d in r.decisions)
    assert any("degree" in d for d in r.decisions)

def test_layers_without_units_still_treated_as_metres():
    assert check_resolution([L("a"), L("b")], target_gsd=10.0).verdict is Verdict.PASS

def test_multiband_zero_fill_above_threshold_refuses():
    r = check_quality_missingness([L("emb", nodata=None, band_count=64,
                                    zero_fill_fraction=0.404)])
    assert r.verdict is Verdict.REFUSE
    assert "unflagged fill" in r.reasons[0]

def test_multiband_zero_fill_with_gapfill_is_a_decision():
    r = check_quality_missingness([L("emb", band_count=64, zero_fill_fraction=0.404)],
                                  gap_fill="nearest")
    assert r.verdict is Verdict.PASS_WITH_DECISION

def test_single_band_zeros_are_logged_not_refused():
    r = check_quality_missingness([L("mask", band_count=1, zero_fill_fraction=0.9)])
    assert r.verdict is Verdict.PASS_WITH_DECISION

def test_unknown_modality_is_named_not_passed():
    r = check_sensor_physics([L("rgb"), L("emb", modality="embedding")])
    assert r.verdict is Verdict.PASS_WITH_DECISION
    assert r.evidence["unchecked_layers"] == ["emb"]

def test_no_checkable_layer_is_not_evaluated():
    r = check_sensor_physics([L("emb", modality="embedding"), L("x", modality=None)])
    assert r.verdict is Verdict.NOT_EVALUATED
    assert r.ok

def test_temporal_gate_without_times_is_reported_not_omitted():
    rep = run_all_gates([L("a")])
    gate = {r.gate: r for r in rep.results}["temporal_alignment"]
    assert gate.verdict is Verdict.NOT_EVALUATED
    assert rep.ok and rep.not_evaluated == [gate]

def test_skipped_temporal_gate_is_absent():
    rep = run_all_gates([L("a")], skip=["temporal_alignment"])
    assert "temporal_alignment" not in [r.gate for r in rep.results]

# physically implausible values that read as data (Sentinel-2 swath edge, 2019-06-25)
def test_implausible_above_tolerance_refuses():
    r = check_quality_missingness([L("b02", plausible_range=(50, 12000), implausible_fraction=0.3)])
    assert r.verdict is Verdict.REFUSE

def test_implausible_within_tolerance_is_a_decision():
    r = check_quality_missingness([L("b02", plausible_range=(50, 12000), implausible_fraction=0.004)])
    assert r.verdict is Verdict.PASS_WITH_DECISION
    assert r.evidence["per_layer"]["b02"]["implausible_fraction"] == 0.004

def test_implausible_tolerance_is_configurable_through_run_all_gates():
    layer = L("b02", plausible_range=(50, 12000), implausible_fraction=0.03)
    assert not run_all_gates([layer]).ok
    assert run_all_gates([layer], max_implausible_fraction=0.05).ok

def test_layer_without_plausible_range_is_unaffected():
    assert check_quality_missingness([L("a")]).verdict is Verdict.PASS

# point-mode geometry (dict level)
def P(name, crs, gsd, offset, centre=(6.745, 52.13583), point=(6.745, 52.13583), inside=True):
    return L(name, crs=crs, gsd=gsd, sample_point=point,
             sample_pixel={"inside": inside, "offset_m": offset, "centre_lonlat": list(centre)})

def test_point_mode_requires_sample_fields():
    r = check_geometry_crs([L("a"), L("b", crs="EPSG:32631")], mode="point")
    assert r.verdict is Verdict.REFUSE
    assert "lacks sample_point" in r.reasons[0]

def test_point_mode_refuses_inconsistent_crs_handling():
    r = check_geometry_crs([P("s1", "EPSG:32632", 10.0, 3.0),
                            P("s2", "EPSG:32631", 20.0, 40.0)], mode="point")
    assert r.verdict is Verdict.REFUSE                  # 40 m > half-diagonal of 20 m pixel
    assert "not applied consistently" in r.reasons[0]

def test_point_mode_optional_spread_tolerance():
    layers = [P("s1", "EPSG:32632", 10.0, 3.0, centre=(6.745, 52.13583)),
              P("s2", "EPSG:32631", 20.0, 9.0, centre=(6.74512, 52.13583))]   # ~8 m apart
    assert check_geometry_crs(layers, mode="point").verdict is Verdict.PASS_WITH_DECISION
    assert check_geometry_crs(layers, mode="point", max_point_offset_m=5).verdict is Verdict.REFUSE

def test_grid_mode_is_still_the_default():
    layers = [P("s1", "EPSG:32632", 10.0, 3.0), P("s2", "EPSG:32631", 20.0, 9.0)]
    assert check_geometry_crs(layers).verdict is Verdict.REFUSE

def test_unknown_geometry_mode_is_an_error():
    import pytest
    with pytest.raises(ValueError):
        check_geometry_crs([L("a")], mode="stack")
