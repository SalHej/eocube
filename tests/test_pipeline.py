import numpy as np
import pytest
from eocube import (extract_samples, spatial_block_split, make_train_eval,
                    r2_score, rmse, baselines)

def mk_layer(name, arr, **kw):
    d = dict(name=name, array=arr, crs="EPSG:32635", gsd=10.0,
             transform=(10, 0, 500000, 0, -10, 6100000),
             width=arr.shape[1], height=arr.shape[0], modality="optical",
             reflectance=True, invalid_fraction=0.0)
    d.update(kw); return d

@pytest.fixture
def scene():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1, (40, 40))
    b = rng.normal(0, 1, (40, 40))
    target = 2 * a - b + rng.normal(0, 0.05, (40, 40))
    return [mk_layer("A", a), mk_layer("B", b)], target

def test_extract_shapes(scene):
    layers, target = scene
    s = extract_samples(layers, target)
    assert s.X.shape == (1600, 2)
    assert s.y.shape == (1600,)
    assert s.coords.shape == (1600, 2)
    assert s.feature_names == ["A", "B"]

def test_nan_target_dropped(scene):
    layers, target = scene
    target = target.copy(); target[:5, :] = np.nan
    s = extract_samples(layers, target)
    assert len(s) == 1600 - 200

def test_nan_feature_dropped(scene):
    layers, target = scene
    layers[0]["array"] = layers[0]["array"].copy()
    layers[0]["array"][0, :] = np.nan
    s = extract_samples(layers, target)
    assert len(s) == 1600 - 40

def test_resample_to_target_shape():
    fine = np.ones((80, 80)); coarse = np.ones((40, 40)) * 2
    target = np.zeros((40, 40))
    s = extract_samples([mk_layer("fine", fine), mk_layer("coarse", coarse)], target)
    assert s.X.shape == (1600, 2)

def test_gap_filled_flagged(scene):
    layers, target = scene
    layers[0]["array"] = layers[0]["array"].copy()
    layers[0]["array"][0, 0] = np.nan
    s = extract_samples(layers, target, drop_invalid=False,
                        gap_filled_layers=["A"])
    assert s.synthetic_mask is not None and s.synthetic_mask.sum() == 1

def test_spatial_split_is_contiguous():
    coords = np.array([(r, c) for r in range(40) for c in range(40)])
    tr, te = spatial_block_split(coords, n_blocks=4, test_fraction=0.25, seed=0)
    assert len(tr) + len(te) == 1600
    assert len(te) > 0 and len(tr) > 0
    # test coords should form whole blocks, not scattered pixels
    te_rows = set(coords[te][:, 0] // 10)
    assert len(te_rows) < 4 or len(set(coords[te][:, 1] // 10)) < 4

def test_split_is_deterministic():
    coords = np.array([(r, c) for r in range(30) for c in range(30)])
    a = spatial_block_split(coords, seed=1)[1]
    b = spatial_block_split(coords, seed=1)[1]
    assert np.array_equal(a, b)

def test_different_seeds_differ():
    coords = np.array([(r, c) for r in range(40) for c in range(40)])
    a = spatial_block_split(coords, n_blocks=8, seed=0)[1]
    b = spatial_block_split(coords, n_blocks=8, seed=5)[1]
    assert not np.array_equal(a, b)

def test_ridge_learns(scene):
    layers, target = scene
    s = extract_samples(layers, target)
    tr, te = spatial_block_split(s.coords, seed=0)
    pred = baselines.ridge(s.X[tr], s.y[tr], s.X[te], 0)
    assert r2_score(s.y[te], pred) > 0.9

def test_standardise_uses_train_stats_only():
    Xtr = np.array([[0.0], [2.0]]); Xte = np.array([[4.0]])
    a, b = baselines.standardise(Xtr, Xte)
    assert np.isclose(a.mean(), 0.0)
    assert b[0, 0] > 1.0      # test point is outside the training range

def test_make_train_eval_returns_metric(scene):
    layers, target = scene
    te = make_train_eval(lambda L: extract_samples(L, target), baselines.ridge)
    v = te(layers, 0)
    assert isinstance(v, float) and v > 0.9

def test_train_eval_handles_tiny_input():
    tiny = np.ones((2, 2))
    te = make_train_eval(lambda L: extract_samples(L, np.zeros((2, 2))),
                         baselines.ridge)
    assert np.isnan(te([mk_layer("A", tiny)], 0))

def test_metrics():
    y = np.array([1.0, 2.0, 3.0])
    assert r2_score(y, y) == 1.0
    assert rmse(y, y) == 0.0
    assert rmse(y, y + 1) == pytest.approx(1.0)
