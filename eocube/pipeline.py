"""
eocube.pipeline
===============

Turning a contract-checked cube into training samples, without leaking.

This module sits between :mod:`eocube.contracts` and whatever model you train.
It does three things the contracts cannot do, because they concern pixels rather
than metadata:

1. **Extract samples** from a cube, honouring the invalid masks that the quality
   gate recorded. A pixel the gate flagged as invalid must not silently become a
   training sample.

2. **Split spatially, not randomly.** This is the single most consequential choice
   in the module. Earth Observation data is spatially autocorrelated: neighbouring
   pixels are nearly identical, so a random train/test split puts near-duplicates
   on both sides and inflates every metric. Published EO results are routinely
   optimistic for exactly this reason. :func:`spatial_block_split` partitions the
   scene into contiguous blocks and assigns whole blocks to folds, which is the
   standard correction.

3. **Carry provenance into training.** If the cube was gap-filled, the filled
   pixels are tracked so that metrics can be reported with and without them. A
   score computed partly on synthesised values is not the same score as one
   computed on observations, and the difference should be visible.

Layers may supply pixels either in memory (``layer["array"]``) or on disk
(``layer["path"]``, read with rasterio). In-memory arrays make the whole pipeline
testable without files or a geospatial stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import numpy as np

__all__ = [
    "SampleSet",
    "extract_samples",
    "spatial_block_split",
    "make_train_eval",
]


@dataclass
class SampleSet:
    """Pixels extracted from a cube, ready for a model."""

    X: np.ndarray                     # (n_samples, n_features)
    y: np.ndarray                     # (n_samples,)
    coords: np.ndarray                # (n_samples, 2) row, col in the target grid
    feature_names: list[str] = field(default_factory=list)
    synthetic_mask: np.ndarray | None = None   # True where a value was gap-filled

    def __len__(self) -> int:
        return int(self.X.shape[0])

    @property
    def n_synthetic(self) -> int:
        return 0 if self.synthetic_mask is None else int(self.synthetic_mask.sum())

    def summary(self) -> str:
        s = (f"{len(self)} samples, {self.X.shape[1]} features "
             f"({', '.join(self.feature_names)})")
        if self.synthetic_mask is not None:
            s += f"; {self.n_synthetic} ({self.n_synthetic/max(1,len(self)):.1%}) gap-filled"
        return s


def _load_array(layer: dict[str, Any]) -> np.ndarray:
    """Return a 2-D array for a layer, from memory or disk."""
    if "array" in layer:
        arr = np.asarray(layer["array"], dtype="float64")
    elif "path" in layer:
        try:
            import rasterio
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "reading layers from disk requires rasterio: "
                'pip install "eocube[raster]"'
            ) from exc
        with rasterio.open(layer["path"]) as src:
            arr = src.read(1).astype("float64")
            if src.nodata is not None:
                arr = np.where(arr == src.nodata, np.nan, arr)
    else:
        raise KeyError(f"layer '{layer.get('name')}' has neither 'array' nor 'path'")
    return arr


def _resample_to(arr: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resample onto the target grid.

    Deliberately crude. The resolution gate has already decided and recorded the
    resampling policy; this is only the mechanical step. If you need bilinear or
    area-weighted resampling, do it upstream where the decision is logged, not
    here where it would be invisible.
    """
    if arr.shape == shape:
        return arr
    rows = (np.linspace(0, arr.shape[0] - 1, shape[0])).round().astype(int)
    cols = (np.linspace(0, arr.shape[1] - 1, shape[1])).round().astype(int)
    return arr[np.ix_(rows, cols)]


def extract_samples(
    layers: Sequence[dict[str, Any]],
    target: np.ndarray,
    *,
    target_mask: np.ndarray | None = None,
    drop_invalid: bool = True,
    gap_filled_layers: Iterable[str] = (),
) -> SampleSet:
    """Build an (X, y) sample set from a cube and a target raster.

    Parameters
    ----------
    layers
        Cube layers, each carrying ``array`` or ``path``. All are resampled onto
        the shape of ``target``.
    target
        The variable being predicted — soil moisture, chlorophyll-a concentration,
        canopy height. NaN marks absence of ground truth.
    target_mask
        Optional boolean array; True marks pixels to keep. Combined with NaN
        checks on the target.
    drop_invalid
        Drop any sample where a feature or the target is non-finite. Set False
        only if the model handles NaN natively.
    gap_filled_layers
        Names of layers whose invalid pixels were filled rather than masked. Any
        sample drawing on a filled pixel is flagged in ``synthetic_mask`` so that
        metrics can be reported both with and without them.

    Returns
    -------
    SampleSet
    """
    target = np.asarray(target, dtype="float64")
    shape = target.shape
    gap_filled = set(gap_filled_layers)

    feats, names, synth = [], [], np.zeros(shape, dtype=bool)
    for layer in layers:
        arr = _resample_to(_load_array(layer), shape)
        feats.append(arr)
        names.append(layer["name"])
        if layer["name"] in gap_filled:
            synth |= ~np.isfinite(arr)

    stack = np.stack(feats, axis=-1)                   # (rows, cols, n_features)

    keep = np.isfinite(target)
    if target_mask is not None:
        keep &= np.asarray(target_mask, dtype=bool)
    if drop_invalid:
        keep &= np.isfinite(stack).all(axis=-1)

    rr, cc = np.nonzero(keep)
    return SampleSet(
        X=stack[rr, cc, :],
        y=target[rr, cc],
        coords=np.column_stack([rr, cc]),
        feature_names=names,
        synthetic_mask=synth[rr, cc],
    )


def spatial_block_split(
    coords: np.ndarray,
    *,
    n_blocks: int = 8,
    test_fraction: float = 0.25,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Split samples by contiguous spatial block, not at random.

    The scene is divided into an ``n_blocks`` x ``n_blocks`` grid and whole blocks
    are assigned to train or test. Neighbouring pixels therefore stay on the same
    side of the split.

    Why this matters more than it looks: with a random split, a pixel and its
    immediate neighbour — nearly identical in every band — land in train and test
    respectively. The model is then scored on data it has effectively seen, and
    R² can be inflated by a large margin. A reviewer who knows remote sensing will
    ask how the split was done, and "random" is the wrong answer.

    Returns
    -------
    (train_idx, test_idx)
    """
    coords = np.asarray(coords)
    rng = np.random.default_rng(seed)

    r, c = coords[:, 0], coords[:, 1]
    r_edges = np.linspace(r.min(), r.max() + 1, n_blocks + 1)
    c_edges = np.linspace(c.min(), c.max() + 1, n_blocks + 1)
    r_block = np.clip(np.digitize(r, r_edges) - 1, 0, n_blocks - 1)
    c_block = np.clip(np.digitize(c, c_edges) - 1, 0, n_blocks - 1)
    block_id = r_block * n_blocks + c_block

    unique = np.unique(block_id)
    rng.shuffle(unique)
    n_test = max(1, int(round(len(unique) * test_fraction)))
    test_blocks = set(unique[:n_test].tolist())

    is_test = np.array([b in test_blocks for b in block_id])
    return np.nonzero(~is_test)[0], np.nonzero(is_test)[0]


def make_train_eval(
    build_samples: Callable[[Sequence[dict[str, Any]]], SampleSet],
    fit_predict: Callable[[np.ndarray, np.ndarray, np.ndarray, int], np.ndarray],
    *,
    metric: Callable[[np.ndarray, np.ndarray], float] | None = None,
    n_blocks: int = 8,
    test_fraction: float = 0.25,
    exclude_synthetic_from_test: bool = True,
    on_empty: float = float("nan"),
) -> Callable[[Sequence[dict[str, Any]], int], float]:
    """Build the ``train_eval(layers, seed) -> metric`` callable the ablation needs.

    Parameters
    ----------
    build_samples
        ``build_samples(layers) -> SampleSet``. Usually a closure over the target
        raster, since the target does not change between ablation arms.
    fit_predict
        ``fit_predict(X_train, y_train, X_test, seed) -> y_pred``. Any model.
    metric
        ``metric(y_true, y_pred) -> float``, higher better. Defaults to R².
    exclude_synthetic_from_test
        Keep gap-filled samples out of the test set. Scoring on synthesised values
        measures the gap-filler as much as the model, and conflating the two is
        how generative preprocessing quietly inflates results.

    Returns
    -------
    A callable suitable for :func:`eocube.ablation.run_ablation`.
    """
    metric = metric or r2_score

    def train_eval(layers: Sequence[dict[str, Any]], seed: int) -> float:
        samples = build_samples(layers)
        if len(samples) < 10:
            return on_empty
        tr, te = spatial_block_split(
            samples.coords, n_blocks=n_blocks,
            test_fraction=test_fraction, seed=seed,
        )
        if exclude_synthetic_from_test and samples.synthetic_mask is not None:
            te = te[~samples.synthetic_mask[te]]
        if len(tr) < 5 or len(te) < 5:
            return on_empty
        y_pred = fit_predict(samples.X[tr], samples.y[tr], samples.X[te], seed)
        return float(metric(samples.y[te], y_pred))

    return train_eval


def r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Coefficient of determination. Higher is better."""
    y_true = np.asarray(y_true, dtype="float64")
    y_pred = np.asarray(y_pred, dtype="float64")
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Root mean squared error. Lower is better — negate it for the ablation."""
    return float(np.sqrt(np.mean((np.asarray(y_true) - np.asarray(y_pred)) ** 2)))
