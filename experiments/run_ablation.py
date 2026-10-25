"""Config-driven leave-one-gate-out ablation.

Launch a study from a config file rather than by editing Python:

    python experiments/run_ablation.py --config experiments/configs/example.yaml
    python experiments/run_ablation.py --config my_study.json --out results/x.json
    python experiments/run_ablation.py --config my_study.yaml --dry-run

The config names the layers and their declared properties, the target raster, the
model, the seeds, the spatial-split parameters, the gate thresholds and the output
path. YAML needs PyYAML; JSON configs with the same structure need nothing beyond
the standard library. See ``experiments/configs/example.yaml`` for every key.

The output is a JSON document whose ``rows`` are exactly what
:meth:`eocube.AblationResult.to_rows` produces, which is what the dashboard's
Results panel renders. Everything else in the document — per-run records, the
contract report for each arm, warnings, the resolved config — is there so that a
result can be audited without rerunning it.

Two things this script will not do, on purpose:

* **Split at random.** Only :func:`eocube.spatial_block_split` is available. A
  config that asks for a random split is rejected, not honoured.
* **Pretend an arm differs when it does not.** On real data, removing a gate only
  changes which checks run; the pixels are the same files. Unless the config
  supplies a *variant* — the cube you would actually have built without that gate,
  e.g. the unmasked optical scene — the arm trains on identical data and its delta
  is zero by construction. The output says which arms had a variant.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import tempfile
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    import eocube
except ImportError:  # running from a checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import eocube

from eocube import (BASELINE, GATES, baselines, extract_samples, make_train_eval,
                    r2_score, rmse, run_ablation, run_all_gates)

RESULT_SCHEMA = "eocube-ablation-result/1"
MIN_SEEDS = 3

#: Keyword arguments run_all_gates understands. Anything else under ``gates`` is
#: a typo, and a typo silently falling back to a default threshold is how a study
#: ends up reporting a configuration nobody chose.
GATE_KWARGS = {
    "require_identical_crs", "max_grid_offset_px",
    "target_gsd", "allow_upsampling", "resampling",
    "window_days", "decay_alpha", "forbid_future",
    "max_invalid_fraction", "gap_fill",
    "band_expressions", "require_terrain_correction",
}
TOP_LEVEL = {"study", "synthetic", "data_root", "layers", "target", "target_time",
             "model", "metric", "seeds", "split", "samples", "gates", "ablation", "output"}
SPLIT_KEYS = {"n_blocks", "test_fraction", "exclude_synthetic_from_test"}
LAYER_KEYS = {"name", "path", "band", "source", "acquisitions", "declared"}
VARIANT_KEYS = {"path", "band", "acquisitions", "declared"}
MODELS = ("ridge", "rf", "gb", "torch")


class ConfigError(ValueError):
    """The config is wrong. Raised before any training starts."""


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
def load_config(path: Path) -> dict[str, Any]:
    """Read a YAML or JSON config. JSON needs only the standard library."""
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        cfg = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise ConfigError(
                f"{path} is YAML but PyYAML is not installed. Either install it or "
                "write the same structure as a .json config, which needs nothing extra."
            ) from exc
        cfg = yaml.safe_load(text)
    else:
        raise ConfigError(f"config must be .yaml, .yml or .json, got {path.name}")
    if not isinstance(cfg, dict):
        raise ConfigError("config must be a mapping at the top level")
    return cfg


def _as_datetime(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        return datetime.fromisoformat(v)
    raise ConfigError(f"cannot read {v!r} as a date")


def _unknown(keys: Any, allowed: set[str], where: str) -> None:
    extra = sorted(set(keys) - allowed)
    if extra:
        raise ConfigError(f"unknown key(s) in {where}: {extra}; allowed: {sorted(allowed)}")


def validate(cfg: dict[str, Any], *, seeds_override: int | None,
             allow_few_seeds: bool) -> dict[str, Any]:
    """Check the config and return a normalised copy. Fails loudly, never guesses."""
    cfg = copy.deepcopy(cfg)
    _unknown(cfg, TOP_LEVEL, "config")
    for key in ("layers", "target", "model"):
        if key not in cfg:
            raise ConfigError(f"config is missing '{key}'")

    if not cfg["layers"]:
        raise ConfigError("'layers' is empty")
    names = []
    for i, layer in enumerate(cfg["layers"]):
        _unknown(layer, LAYER_KEYS, f"layers[{i}]")
        if "name" not in layer or "path" not in layer:
            raise ConfigError(f"layers[{i}] needs 'name' and 'path'")
        names.append(layer["name"])
    if len(set(names)) != len(names):
        raise ConfigError(f"layer names must be unique: {names}")

    model = cfg["model"]
    if isinstance(model, str):
        model = {"name": model}
    if model.get("name") not in MODELS:
        raise ConfigError(f"model.name must be one of {MODELS}, got {model.get('name')!r}")
    model.setdefault("params", {})
    cfg["model"] = model

    cfg.setdefault("metric", "r2")
    if cfg["metric"] not in ("r2", "neg_rmse"):
        raise ConfigError("metric must be 'r2' or 'neg_rmse' (higher must mean better)")

    seeds = cfg.get("seeds", [0, 1, 2])
    if seeds_override is not None:
        seeds = seeds_override
    if isinstance(seeds, int):
        seeds = list(range(seeds))
    seeds = [int(s) for s in seeds]
    if len(set(seeds)) != len(seeds):
        raise ConfigError(f"seeds must be distinct: {seeds}")
    if len(seeds) < MIN_SEEDS and not allow_few_seeds:
        raise ConfigError(
            f"{len(seeds)} seed(s) given; at least {MIN_SEEDS} are needed for a reportable "
            "result. Pass --allow-few-seeds for a smoke test; the output is then marked "
            "not reportable."
        )
    cfg["seeds"] = seeds

    split = dict(cfg.get("split") or {})
    if any(k in split for k in ("random", "method", "shuffle", "kind")):
        raise ConfigError(
            "split accepts only spatial-block parameters "
            f"{sorted(SPLIT_KEYS)}. EO pixels are spatially autocorrelated; a random "
            "split puts near-duplicates on both sides and is not offered."
        )
    _unknown(split, SPLIT_KEYS, "split")
    split.setdefault("n_blocks", 8)
    split.setdefault("test_fraction", 0.25)
    split.setdefault("exclude_synthetic_from_test", True)
    cfg["split"] = split

    gates = dict(cfg.get("gates") or {})
    _unknown(gates, GATE_KWARGS, "gates")
    cfg["gates"] = gates

    samples = dict(cfg.get("samples") or {})
    _unknown(samples, {"gap_filled_layers"}, "samples")
    cfg["samples"] = samples

    abl = dict(cfg.get("ablation") or {})
    _unknown(abl, {"gates", "train_on_refused", "variants"}, "ablation")
    abl.setdefault("gates", list(GATES))
    bad = sorted(set(abl["gates"]) - set(GATES))
    if bad:
        raise ConfigError(f"ablation.gates has unknown gate(s) {bad}; known: {list(GATES)}")
    abl.setdefault("train_on_refused", False)
    variants = dict(abl.get("variants") or {})
    for gate, overrides in variants.items():
        if gate not in GATES:
            raise ConfigError(f"ablation.variants has unknown gate '{gate}'")
        for lname, ov in overrides.items():
            if lname not in names:
                raise ConfigError(f"variant for '{gate}' names unknown layer '{lname}'")
            _unknown(ov, VARIANT_KEYS, f"ablation.variants.{gate}.{lname}")
    abl["variants"] = variants
    cfg["ablation"] = abl

    if isinstance(cfg["target"], str):
        cfg["target"] = {"path": cfg["target"]}
    _unknown(cfg["target"], {"path", "band"}, "target")
    return cfg


def _substitute(obj: Any, mapping: dict[str, str]) -> Any:
    if isinstance(obj, str):
        for k, v in mapping.items():
            obj = obj.replace("{" + k + "}", v)
        return obj
    if isinstance(obj, dict):
        return {k: _substitute(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, mapping) for v in obj]
    return obj


# --------------------------------------------------------------------------
# rasters
# --------------------------------------------------------------------------
def _rasterio():
    try:
        import rasterio
    except ImportError as exc:
        raise ConfigError('reading rasters needs rasterio: pip install "eocube[raster]"') from exc
    return rasterio


def _read_band(path: Path, band: int) -> np.ndarray:
    """One band as float64, nodata and non-finite values as NaN."""
    with _rasterio().open(path) as src:
        if not 1 <= band <= src.count:
            raise ConfigError(f"{path} has {src.count} band(s); band {band} requested")
        arr = src.read(band).astype("float64")
        nd = src.nodatavals[band - 1]
        if nd is not None and not (isinstance(nd, float) and math.isnan(nd)):
            arr[arr == nd] = np.nan
    arr[~np.isfinite(arr)] = np.nan
    return arr


def _bounds(layer: dict[str, Any]) -> tuple[float, float, float, float]:
    a, _, c, _, e, f = layer["transform"]
    xs = sorted((c, c + a * layer["width"]))
    ys = sorted((f, f + e * layer["height"]))
    return xs[0], ys[0], xs[1], ys[1]


def _resolve(path: str, root: Path | None) -> Path:
    p = Path(path)
    return p if p.is_absolute() or root is None else root / p


def build_layer(spec: dict[str, Any], root: Path | None) -> dict[str, Any]:
    """Describe a raster from the file, then attach its pixels.

    Multi-band files must name a ``band``: :func:`eocube.extract_samples` uses one
    band per layer, and quietly taking band 1 of a 200-band EnMAP cube is the kind
    of default this package exists to refuse.
    """
    from eocube.io import describe

    path = _resolve(spec["path"], root)
    if not path.exists():
        raise ConfigError(f"layer '{spec['name']}': {path} does not exist")
    layer = describe(path, spec["name"], declared=dict(spec.get("declared") or {}))
    band = spec.get("band")
    if band is None:
        if layer["band_count"] > 1:
            raise ConfigError(
                f"layer '{spec['name']}': {path} has {layer['band_count']} bands; "
                "say which one with 'band'"
            )
        band = 1
    band = int(band)
    if layer["band_count"] > 1:
        # the gate should judge the band the model will see, not the union of all
        layer["invalid_fraction_all_bands"] = layer["invalid_fraction"]
        layer["invalid_fraction"] = layer["invalid_fraction_by_band"][band - 1]
    layer["feature_band"] = band
    layer["array"] = _read_band(path, band)
    return layer


# --------------------------------------------------------------------------
# synthetic study
# --------------------------------------------------------------------------
def write_synthetic(out_dir: Path, size: int = 96, seed: int = 0) -> dict[str, Path]:
    """Write a small synthetic study as GeoTIFFs, defect variants included.

    Spatially autocorrelated on purpose: without autocorrelation a spatial split
    and a random one score the same and the exercise proves nothing. Each variant
    carries the defect its gate exists to catch, and each defect is one that gate
    does catch — a whole-pixel shift, for instance, would pass the geometry gate's
    sub-pixel test and so is not used.
    """
    rasterio = _rasterio()
    from rasterio.transform import from_origin

    rng = np.random.default_rng(seed)
    nodata = -9999.0

    def smooth(scale: float) -> np.ndarray:
        n = int(size / scale) + 2
        coarse = rng.normal(0, 1, (n, n))
        idx = np.linspace(0, n - 1, size).round().astype(int)
        return coarse[np.ix_(idx, idx)]

    wet, veg = smooth(12.0), smooth(8.0)
    noise = lambda s: rng.normal(0, s, (size, size))  # noqa: E731
    s1 = 0.08 + 0.05 * wet + noise(0.006)
    s2 = 0.22 + 0.09 * veg - 0.03 * wet + noise(0.01)
    tsx = 0.06 + 0.04 * wet + 0.01 * veg + noise(0.005)
    era5 = 12.0 + 3.0 * smooth(24.0)
    target = 0.30 + 0.12 * wet + 0.03 * veg + 0.002 * (era5 - 12.0) + noise(0.012)

    cloud = smooth(6.0) > 0.7                         # contiguous, like real cloud
    s2_masked = np.where(cloud, nodata, s2)           # quality gate active: masked
    s2_cloudy = np.where(cloud, s2 + 0.35, s2)        # gate removed: contamination kept
    s2_halfpx = 0.5 * (s2 + np.roll(s2, -1, axis=1))  # resampled onto a grid 0.5 px off
    s1_db = 10 * np.log10(np.clip(s1, 1e-6, None))
    s1_future = 0.08 + 0.4 * (target - 0.30) + noise(0.004)   # acquired after target time

    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    def write(name: str, arr: np.ndarray, dx: float = 0.0) -> None:
        p = out_dir / f"{name}.tif"
        with rasterio.open(
            p, "w", driver="GTiff", height=size, width=size, count=1, dtype="float32",
            crs="EPSG:32635", nodata=nodata,
            transform=from_origin(500000.0 + dx, 6100000.0, 10.0, 10.0),
        ) as dst:
            dst.write(arr.astype("float32"), 1)
        files[name] = p

    write("s1_vv", s1)
    write("s2_b08", s2_masked)
    write("tsx_hh", tsx)
    write("era5_t", era5)
    write("target_sm", target)
    write("s2_b08_cloudy", s2_cloudy)
    write("s2_b08_halfpx", s2_halfpx, dx=5.0)
    write("s1_vv_db", s1_db)
    write("s1_vv_future", s1_future)
    return files


# --------------------------------------------------------------------------
# study
# --------------------------------------------------------------------------
def _model(spec: dict[str, Any]):
    name, params = spec["name"], dict(spec.get("params") or {})
    if name == "ridge":
        return baselines.make_ridge(**params)
    if name == "rf":
        return baselines.make_random_forest(**params)
    if name == "gb":
        if params:
            raise ConfigError("model 'gb' takes no params in eocube.baselines")
        return baselines.gradient_boosting
    if "hidden" in params:
        params["hidden"] = tuple(params["hidden"])
    return baselines.make_torch_mlp(**params)


def _metric(name: str):
    return r2_score if name == "r2" else (lambda t, p: -rmse(t, p))


def _strict(obj: Any) -> Any:
    """NaN and infinity become null: strict JSON, and 'not available' is the truth."""
    if isinstance(obj, dict):
        return {str(k): _strict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_strict(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    return obj


def run_study(cfg: dict[str, Any], *, config_path: Path | None = None,
              dry_run: bool = False, verbose: bool = True) -> dict[str, Any]:
    """Run a validated config and return the result document."""
    warnings: list[str] = []
    not_reportable: list[str] = []
    if len(cfg["seeds"]) < MIN_SEEDS:
        not_reportable.append(f"only {len(cfg['seeds'])} seed(s); minimum is {MIN_SEEDS}")

    root = Path(cfg["data_root"]) if cfg.get("data_root") else None
    target_time = _as_datetime(cfg["target_time"]) if cfg.get("target_time") else None
    gate_kwargs = dict(cfg["gates"])
    abl = cfg["ablation"]

    # --- target and pre-flight ------------------------------------------
    from eocube.io import describe
    tpath = _resolve(cfg["target"]["path"], root)
    if not tpath.exists():
        raise ConfigError(f"target {tpath} does not exist")
    tdesc = describe(tpath, "target")
    target = _read_band(tpath, int(cfg["target"].get("band", 1)))

    specs = {s["name"]: s for s in cfg["layers"]}
    cache: dict[str, list[dict[str, Any]]] = {}

    def layers_for(arm_gate: str | None) -> list[dict[str, Any]]:
        key = arm_gate or BASELINE
        if key not in cache:
            overrides = abl["variants"].get(arm_gate, {}) if arm_gate else {}
            out = []
            for name, spec in specs.items():
                spec = copy.deepcopy(spec)
                ov = overrides.get(name, {})
                for k in ("path", "band", "acquisitions"):
                    if k in ov:
                        spec[k] = ov[k]
                if "declared" in ov:
                    spec["declared"] = {**(spec.get("declared") or {}), **ov["declared"]}
                layer = build_layer(spec, root)
                layer["_source"] = spec.get("source", name)
                # None, not []: an absent list means "not time-checked", whereas an
                # explicitly empty one is a source with no acquisitions, which refuses
                acq = spec.get("acquisitions")
                layer["_acquisitions"] = None if acq is None else [_as_datetime(t) for t in acq]
                out.append(layer)
            cache[key] = out
        return cache[key]

    def kwargs_for(layers: list[dict[str, Any]]) -> dict[str, Any]:
        acq: dict[str, list[datetime]] = {}
        for l in layers:
            if l["_acquisitions"] is not None:
                acq.setdefault(l["_source"], []).extend(l["_acquisitions"])
        kw = dict(gate_kwargs)
        if acq and target_time is not None:
            kw["acquisitions"] = acq
            kw["target_time"] = target_time
        return kw

    base_layers = layers_for(None)
    preflight: dict[str, Any] = {"target": {k: tdesc[k] for k in
                                            ("crs", "transform", "width", "height", "gsd")}}
    crs_bad = [l["name"] for l in base_layers if l["crs"] != tdesc["crs"]]
    tb = _bounds(tdesc)
    ext_bad = [l["name"] for l in base_layers
               if max(abs(x - y) for x, y in zip(_bounds(l), tb)) > tdesc["gsd"]]
    preflight.update(crs_mismatch=crs_bad, extent_mismatch=ext_bad)
    if crs_bad or ext_bad:
        # extract_samples maps every layer onto the target's pixel array by index,
        # ignoring georeferencing. Different extents would pair the wrong pixels.
        raise ConfigError(
            f"target grid does not match the baseline layers (CRS: {crs_bad}, "
            f"extent: {ext_bad}); reproject or crop before running"
        )

    if "temporal_alignment" in abl["gates"] and "acquisitions" not in kwargs_for(base_layers):
        warnings.append(
            "temporal_alignment does not run: no layer lists acquisitions, or no "
            "target_time is set. Its arm is identical to the baseline."
        )
    untimed = [l["name"] for l in base_layers if l["_acquisitions"] is None]
    if untimed and "acquisitions" in kwargs_for(base_layers):
        warnings.append(
            f"layer(s) {untimed} list no acquisitions; the temporal gate does not check "
            "them for leakage"
        )
    if cfg["samples"].get("gap_filled_layers"):
        warnings.append(
            "gap_filled_layers only flags pixels that are non-finite in the loaded "
            "array, and those are dropped before splitting. A raster already filled on "
            "disk has no such pixels, so filled samples cannot be kept out of the test "
            "set by this mechanism."
        )
    no_variant = [g for g in abl["gates"] if g not in abl["variants"]]
    if no_variant:
        warnings.append(
            f"no variant for {no_variant}: those arms train on the baseline files, so any "
            "metric difference reflects only whether the cube was refused."
        )

    # what the full gate set says about each variant: does the removed gate catch it?
    variant_check = {}
    for g in abl["variants"]:
        rep = run_all_gates(layers_for(g), **kwargs_for(layers_for(g)))
        refusing = [r.gate for r in rep.refusals]
        variant_check[f"without {g}"] = {"refused_by": refusing, "caught_by_removed_gate":
                                         g in refusing}
        if g not in refusing:
            warnings.append(
                f"the variant for '{g}' is not refused by that gate, so its arm does not "
                "measure what the gate protects against"
            )

    reports = {BASELINE: run_all_gates(base_layers, **kwargs_for(base_layers)).to_dict()}
    for g in abl["gates"]:
        ls = layers_for(g)
        reports[f"without {g}"] = run_all_gates(ls, skip=(g,), **kwargs_for(ls)).to_dict()

    doc: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "study": cfg.get("study", "unnamed-study"),
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "eocube_version": eocube.__version__,
        "config_path": str(config_path) if config_path else None,
        "synthetic": bool(cfg.get("synthetic")),
        "model": cfg["model"],
        "metric": cfg["metric"],
        "seeds": cfg["seeds"],
        "split": cfg["split"],
        "gates": gate_kwargs,
        "train_on_refused": abl["train_on_refused"],
        "variants": {f"without {g}": sorted(v) for g, v in abl["variants"].items()},
        "variant_check": variant_check,
        "preflight": preflight,
        "contract_reports": reports,
        "warnings": warnings,
    }
    if doc["synthetic"]:
        not_reportable.append("synthetic data")
    if dry_run:
        doc.update(rows=[], runs=[], dry_run=True)
        doc["reportable"] = False
        doc["not_reportable_because"] = not_reportable + ["dry run, nothing trained"]
        return doc

    # --- the ablation ----------------------------------------------------
    gap_filled = list(cfg["samples"].get("gap_filled_layers", []))
    train_eval = make_train_eval(
        lambda layers: extract_samples(layers, target, gap_filled_layers=gap_filled),
        _model(cfg["model"]),
        metric=_metric(cfg["metric"]),
        n_blocks=int(cfg["split"]["n_blocks"]),
        test_fraction=float(cfg["split"]["test_fraction"]),
        exclude_synthetic_from_test=bool(cfg["split"]["exclude_synthetic_from_test"]),
    )

    def build_cube(skip: tuple[str, ...]):
        ls = layers_for(skip[0] if skip else None)
        return ls, kwargs_for(ls)

    result = run_ablation(build_cube, train_eval, gates=abl["gates"], seeds=cfg["seeds"],
                          train_on_refused=abl["train_on_refused"], verbose=verbose)

    runs = [asdict(r) for arm in result.arms.values() for r in arm.runs]
    errors = [r for r in runs if r["error"]]
    if errors:
        warnings.append(f"{len(errors)} of {len(runs)} run(s) raised; see runs[].error")
        not_reportable.append("runs raised errors")
    doc["rows"] = result.to_rows()
    doc["runs"] = runs
    doc["summary"] = result.summary()
    doc["reportable"] = not not_reportable
    doc["not_reportable_because"] = not_reportable
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--out", type=Path, help="overrides 'output' in the config")
    ap.add_argument("--seeds", type=int, help="use seeds 0..N-1, overriding the config")
    ap.add_argument("--allow-few-seeds", action="store_true",
                    help=f"permit fewer than {MIN_SEEDS} seeds; output marked not reportable")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the pre-flight and the gates for every arm, train nothing")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    try:
        cfg = validate(load_config(a.config), seeds_override=a.seeds,
                       allow_few_seeds=a.allow_few_seeds)
        out = a.out or (Path(cfg["output"]) if cfg.get("output") else None)
        if out is None:
            raise ConfigError("no output path: set 'output' in the config or pass --out")
        with tempfile.TemporaryDirectory(prefix="eocube-synth-") as tmp:
            if cfg.get("synthetic"):
                syn = dict(cfg["synthetic"]) if isinstance(cfg["synthetic"], dict) else {}
                _unknown(syn, {"size", "seed"}, "synthetic")
                write_synthetic(Path(tmp), **syn)
                cfg = _substitute(cfg, {"synthetic": tmp})
            doc = run_study(cfg, config_path=a.config, dry_run=a.dry_run,
                            verbose=not a.quiet)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    doc["config"] = _strict(load_config(a.config))   # as written; seeds as resolved above
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_strict(doc), indent=2, allow_nan=False), encoding="utf-8")

    if not a.quiet:
        if doc.get("summary"):
            print("\n" + doc["summary"])
        for w in doc["warnings"]:
            print(f"warning: {w}")
        if not doc["reportable"]:
            print(f"NOT REPORTABLE: {'; '.join(doc['not_reportable_because'])}")
        print(f"\nwrote {out}")
    runs = doc.get("runs", [])
    return 2 if runs and all(r["error"] for r in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
