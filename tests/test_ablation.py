from datetime import datetime
from eocube import run_ablation, BASELINE, GATES

def L(name, **kw):
    base = dict(name=name, crs="EPSG:32635", transform=(10, 0, 500000, 0, -10, 6100000),
                width=100, height=100, gsd=10.0, modality="optical", reflectance=True,
                invalid_fraction=0.0)
    base.update(kw); return base

def build(skip):
    layers = [L("a"), L("b")]
    if "geometry_crs" in skip:
        layers[1]["crs"] = "EPSG:4326"
    return layers, {}

def train(layers, seed):
    return 0.8 - 0.1 * sum(1 for l in layers if l["crs"] != "EPSG:32635") + seed * 0.001

def test_runs_all_arms():
    r = run_ablation(build, train, seeds=(0, 1), verbose=False, train_on_refused=True)
    assert BASELINE in r.arms
    for g in GATES:
        assert f"without {g}" in r.arms

def test_baseline_and_delta():
    r = run_ablation(build, train, seeds=(0, 1, 2), verbose=False, train_on_refused=True)
    assert r.baseline.mean is not None
    assert r.effect_of("without geometry_crs") is not None
    assert r.effect_of("without geometry_crs") < 0

def test_refusal_recorded_when_not_training():
    r = run_ablation(build, train, seeds=(0,), verbose=False, train_on_refused=False)
    arm = r.arms["without geometry_crs"]
    assert arm.runs[0].metric is None or arm.runs[0].accepted

def test_errors_are_captured_not_raised():
    def bad_train(layers, seed):
        raise ValueError("model blew up")
    r = run_ablation(build, bad_train, seeds=(0,), verbose=False, train_on_refused=True)
    assert any(run.error for arm in r.arms.values() for run in arm.runs)

def test_summary_renders():
    r = run_ablation(build, train, seeds=(0, 1), verbose=False, train_on_refused=True)
    s = r.summary()
    assert "configuration" in s and "delta" in s
    assert len(r.to_rows()) == len(GATES) + 1

def test_stdev_present_with_multiple_seeds():
    r = run_ablation(build, train, seeds=(0, 1, 2), verbose=False, train_on_refused=True)
    assert r.baseline.stdev is not None and r.baseline.stdev >= 0
