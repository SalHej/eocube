"""End-to-end tests for experiments/run_ablation.py on synthetic GeoTIFFs."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("rasterio")
yaml = pytest.importorskip("yaml")

from eocube import BASELINE, GATES, AblationResult  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "experiments" / "configs" / "example.yaml"
ROW_KEYS = {"arm", "gate_removed", "n_runs", "mean_metric", "stdev", "refusal_rate",
            "mean_decisions", "delta_vs_baseline"}


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("run_ablation", ROOT / "experiments" /
                                                  "run_ablation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def small_config(tmp_path):
    """The example config, smaller, written as JSON so the stdlib path is exercised."""
    cfg = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    cfg["synthetic"]["size"] = 48
    cfg["split"]["n_blocks"] = 4

    def write(c=None):
        p = tmp_path / "study.json"
        p.write_text(json.dumps(c or cfg, default=str), encoding="utf-8")
        return p

    write.cfg = cfg
    return write


def test_row_keys_match_ablation_result():
    """Guards the dashboard contract: if to_rows() changes, this test says so."""
    from eocube import ArmResult
    r = AblationResult(arms={BASELINE: ArmResult(arm=BASELINE, gate_removed=None)})
    assert set(r.to_rows()[0]) == ROW_KEYS


def test_example_yaml_runs_end_to_end(runner, tmp_path):
    out = tmp_path / "result.json"
    assert runner.main(["--config", str(EXAMPLE), "--out", str(out), "--quiet"]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))      # strict JSON parses
    json.dumps(doc, allow_nan=False)

    rows = {r["arm"]: r for r in doc["rows"]}
    assert set(rows) == {BASELINE} | {f"without {g}" for g in GATES}
    assert all(set(r) == ROW_KEYS for r in doc["rows"])
    assert all(r["n_runs"] == 3 for r in doc["rows"])
    assert rows[BASELINE]["refusal_rate"] == 0.0
    assert rows[BASELINE]["delta_vs_baseline"] == 0.0
    # each variant is a defect the removed gate actually catches
    assert all(v["caught_by_removed_gate"] for v in doc["variant_check"].values())
    # no variant for resolution: same files, same seeds, identical metric
    assert rows["without resolution"]["delta_vs_baseline"] == 0.0
    # leakage inflates the score; that is the failure the temporal gate prevents
    assert rows["without temporal_alignment"]["delta_vs_baseline"] > 0
    assert doc["synthetic"] is True and doc["reportable"] is False
    assert doc["seeds"] == [0, 1, 2]


def test_json_config_and_seed_override(runner, small_config, tmp_path):
    out = tmp_path / "r.json"
    rc = runner.main(["--config", str(small_config()), "--out", str(out), "--quiet",
                      "--seeds", "4"])
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["seeds"] == [0, 1, 2, 3]
    assert all(r["n_runs"] == 4 for r in doc["rows"])


def test_too_few_seeds_refused_unless_allowed(runner, small_config, tmp_path):
    out = tmp_path / "r.json"
    args = ["--config", str(small_config()), "--out", str(out), "--quiet", "--seeds", "2"]
    assert runner.main(args) == 1 and not out.exists()
    assert runner.main(args + ["--allow-few-seeds"]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["reportable"] is False
    assert any("seed" in why for why in doc["not_reportable_because"])


def test_random_split_is_rejected(runner, small_config, tmp_path):
    cfg = copy.deepcopy(small_config.cfg)
    cfg["split"]["method"] = "random"
    out = tmp_path / "r.json"
    assert runner.main(["--config", str(small_config(cfg)), "--out", str(out)]) == 1
    assert not out.exists()


def test_misspelt_gate_threshold_is_rejected(runner, small_config, tmp_path):
    cfg = copy.deepcopy(small_config.cfg)
    cfg["gates"]["max_invalid_fracton"] = 0.5
    with pytest.raises(runner.ConfigError, match="max_invalid_fracton"):
        runner.validate(cfg, seeds_override=None, allow_few_seeds=False)


def test_dry_run_trains_nothing(runner, small_config, tmp_path):
    out = tmp_path / "r.json"
    assert runner.main(["--config", str(small_config()), "--out", str(out), "--quiet",
                        "--dry-run"]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["rows"] == [] and doc["dry_run"] is True
    assert BASELINE in doc["contract_reports"]


def test_target_on_a_different_grid_is_refused(runner, small_config, tmp_path):
    """extract_samples pairs pixels by array index, so the grids must agree."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    tgt = tmp_path / "offset_target.tif"
    with rasterio.open(tgt, "w", driver="GTiff", height=48, width=48, count=1,
                       dtype="float32", crs="EPSG:32635",
                       transform=from_origin(501000.0, 6100000.0, 10.0, 10.0)) as dst:
        dst.write(np.ones((1, 48, 48), dtype="float32"))
    cfg = copy.deepcopy(small_config.cfg)
    cfg["target"]["path"] = str(tgt)
    out = tmp_path / "r.json"
    assert runner.main(["--config", str(small_config(cfg)), "--out", str(out),
                        "--quiet"]) == 1
    assert not out.exists()
