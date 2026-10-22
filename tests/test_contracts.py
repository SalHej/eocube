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

def test_gate_ablation():
    layers = [L("a"), L("b", crs="EPSG:4326")]
    assert not run_all_gates(layers).ok
    assert run_all_gates(layers, skip=["geometry_crs"]).ok

def test_report_serialises():
    rep = run_all_gates([L("a"), L("b")])
    d = rep.to_dict()
    assert "gates" in d and isinstance(d["ok"], bool)
    assert isinstance(rep.provenance(), list)
