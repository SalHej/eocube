"""The dataset configs in experiments/configs/ stay valid for the runner.

These configs point at data that is not on this machine, so they cannot be run in
CI. What can be checked is that the runner accepts their structure, and that the
peatland config cannot name a path outside the cluster project directory: it reads
licensed TerraSAR-X and EnMAP scenes, and the runner itself uses an absolute path
as given.
"""

import importlib.util
from pathlib import Path, PurePosixPath

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "experiments" / "configs"
PROJECT = "/scratch/lustre/projects/hpc_project_a1fb2511d81f42fba1f872930bb56828"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("run_ablation", ROOT / "experiments" /
                                                  "run_ablation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load(name):
    return yaml.safe_load((CONFIGS / name).read_text(encoding="utf-8"))


def _all_paths(cfg):
    paths = [l["path"] for l in cfg["layers"]] + [cfg["target"]["path"]]
    for overrides in (cfg.get("ablation", {}).get("variants") or {}).values():
        paths += [ov["path"] for ov in overrides.values() if "path" in ov]
    return paths


@pytest.mark.parametrize("name", ["peatland.yaml", "chlorophyll.yaml"])
def test_regression_configs_validate(runner, name):
    cfg = runner.validate(_load(name), seeds_override=None, allow_few_seeds=False)
    assert len(cfg["seeds"]) >= runner.MIN_SEEDS


def test_solar_panel_refuses_regression_metric(runner):
    """Segmentation is not supported yet; the config must not train a regressor."""
    with pytest.raises(runner.ConfigError, match="metric"):
        runner.validate(_load("solar_panel.yaml"), seeds_override=None,
                        allow_few_seeds=False)


def test_solar_panel_validates_apart_from_metric(runner):
    cfg = _load("solar_panel.yaml")
    cfg["metric"] = "r2"
    runner.validate(cfg, seeds_override=None, allow_few_seeds=False)
    assert "temporal_alignment" not in cfg["ablation"]["gates"]


def test_peatland_paths_stay_inside_project():
    cfg = _load("peatland.yaml")
    root = PurePosixPath(cfg["data_root"])
    assert root == PurePosixPath(PROJECT) or PurePosixPath(PROJECT) in root.parents
    for p in _all_paths(cfg):
        pp = PurePosixPath(p)
        assert not pp.is_absolute() and not Path(p).is_absolute(), p
        assert ".." not in pp.parts, p
        assert "~" not in p and "\\" not in p, p


def test_peatland_does_not_gap_fill():
    cfg = _load("peatland.yaml")
    assert cfg["gates"]["gap_fill"] is None
    assert not cfg["samples"]["gap_filled_layers"]
