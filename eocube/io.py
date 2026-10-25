"""
eocube.io
=========

Raster-backed layer description.

Version 0.1 of this package took declared metadata at its word: a layer that
said ``terrain_corrected=True`` was believed. That was the largest stated
limitation in the README, and it is the one that matters, because the failure
mode the contracts exist to prevent is precisely a declaration that does not
match the file.

This module closes that gap. :func:`describe` opens a raster, reads what is
actually true of it, and returns a layer dictionary in the shape the contract
gates expect — with every field marked as either ``observed`` (read from the
file) or ``declared`` (supplied by the caller and unverifiable from pixels
alone). :func:`describe_many` builds a whole cube description, and
:func:`to_descriptor` serialises it to a machine-readable JSON document so that
an automated system can consume a cube's provenance without parsing prose.

rasterio is an optional dependency. Import this module only if it is installed:

    pip install "eocube[raster]"
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

try:  # pragma: no cover - import guard
    import rasterio
    from rasterio.crs import CRS
    import numpy as np
    _HAVE_RASTERIO = True
except ImportError:  # pragma: no cover
    _HAVE_RASTERIO = False

__all__ = [
    "Provenance",
    "describe",
    "describe_many",
    "to_descriptor",
    "write_descriptor",
    "DESCRIPTOR_VERSION",
]

#: 0.3: per-band invalid fractions, decimation record, non-finite floats as strings.
DESCRIPTOR_VERSION = "eocube-cube-descriptor/0.3"

#: Fields that can be read from a raster, and therefore verified.
OBSERVABLE = (
    "crs", "transform", "width", "height", "gsd", "dtype", "nodata",
    "invalid_fraction", "invalid_fraction_by_band", "invalid_fraction_sampling",
    "band_count", "value_range",
)

#: Fields that cannot be derived from pixels and must be declared by the
#: caller. Each is recorded with its source so that a reader can tell the
#: difference between a measurement and an assertion.
DECLARED_ONLY = (
    "modality", "units", "calibrated", "terrain_corrected", "reflectance",
    "qa_mask_available", "qa_mask_applied", "acquisition_time", "sensor",
    "processing_level",
)


@dataclass
class Provenance:
    """Where each field in a layer description came from."""

    observed: dict[str, Any] = field(default_factory=dict)
    declared: dict[str, Any] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed": self.observed,
            "declared": self.declared,
            "conflicts": self.conflicts,
        }


def _require_rasterio() -> None:
    if not _HAVE_RASTERIO:
        raise ImportError(
            "eocube.io requires rasterio. Install with: pip install \"eocube[raster]\""
        )


def _invalid_fraction(src, max_pixels: int = 4_000_000) -> dict[str, Any]:
    """Fraction of pixels that are nodata or non-finite, across every band.

    Every band is inspected, one at a time so that a 200-band hyperspectral
    scene costs one band of memory rather than two hundred. Checking band 1
    alone under-reported multi-band rasters: a stack whose first band is clean
    and whose later bands are half empty passed the quality gate as 0% invalid.
    The headline ``invalid_fraction`` is the fraction of pixel positions invalid
    in *any* band, because a sample that draws on all bands is lost if one is
    missing; ``invalid_fraction_by_band`` keeps the per-band figures.

    Large rasters are decimated rather than read whole, so that describing a
    cube does not cost more than the analysis it precedes. A decimated figure is
    an estimate, not a count, and is returned with the decimation step and pixel
    counts so that the report can say which it is. Note that rasterio may serve
    a decimated read from internal overviews, whose resampling can blur nodata
    edges; treat estimates near a threshold with suspicion.
    """
    h, w = src.height, src.width
    step = max(1, int(math.sqrt((h * w) / max_pixels)))
    shape = (max(1, h // step), max(1, w // step))
    total = shape[0] * shape[1]
    sampling = {
        "decimation_step": step,
        "pixels_read": total,
        "pixels_total": h * w,
        "estimated": step > 1,
    }
    if total == 0 or src.count == 0:
        return {"invalid_fraction": 1.0, "invalid_fraction_by_band": [], **sampling}

    any_bad = np.zeros(shape, dtype=bool)
    by_band: list[float] = []
    for b in range(1, src.count + 1):
        data = src.read(b, out_shape=shape)
        nodata = src.nodatavals[b - 1]
        bad = np.zeros(shape, dtype=bool)
        if nodata is not None and not (isinstance(nodata, float) and math.isnan(nodata)):
            bad |= data == nodata
        if np.issubdtype(data.dtype, np.floating):
            bad |= ~np.isfinite(data)     # a mask, so non-finite nodata is not counted twice
        any_bad |= bad
        by_band.append(round(float(bad.mean()), 6))
    return {
        "invalid_fraction": round(float(any_bad.mean()), 6),
        "invalid_fraction_by_band": by_band,
        **sampling,
    }


def _same(key: str, observed: Any, declared: Any) -> bool:
    """Whether a declared value agrees with an observed one.

    Comparing string forms reported spurious conflicts: ``10`` against ``10.0``,
    ``epsg:32635`` against ``EPSG:32635``, a list transform against a tuple. A
    conflict list that cries wolf trains its reader to ignore it.
    """
    if key == "crs" and observed is not None and declared is not None:
        try:
            return CRS.from_user_input(declared) == CRS.from_user_input(observed)
        except Exception:  # noqa: BLE001 - an unparseable CRS is a conflict
            return False
    if isinstance(observed, (tuple, list)) and isinstance(declared, (tuple, list)):
        return len(observed) == len(declared) and all(
            _same(key, o, d) for o, d in zip(observed, declared))
    num = (int, float)
    if isinstance(observed, num) and isinstance(declared, num) \
            and not isinstance(observed, bool) and not isinstance(declared, bool):
        if math.isnan(observed) or math.isnan(declared):
            return math.isnan(observed) and math.isnan(declared)
        return math.isclose(observed, declared, rel_tol=1e-9, abs_tol=1e-9)
    return str(observed) == str(declared)


def describe(
    path: str | Path,
    name: str | None = None,
    *,
    declared: dict[str, Any] | None = None,
    read_stats: bool = True,
    max_stat_pixels: int = 4_000_000,
) -> dict[str, Any]:
    """Build a contract-gate layer description from a real raster.

    Parameters
    ----------
    path
        Raster file readable by rasterio.
    name
        Layer name used in gate reports. Defaults to the file stem.
    declared
        Properties that cannot be derived from the file — modality, units,
        calibration status, terrain correction, acquisition time and so on.
        These are recorded as declared, never as observed.
    read_stats
        If False, skip the pixel pass. Faster, but ``invalid_fraction`` is then
        unknown rather than zero, and the quality gate will say so.
    max_stat_pixels
        Pixel budget for the invalid-fraction pass. Rasters larger than this are
        read decimated, and ``invalid_fraction_sampling`` records the step and
        marks the figure as an estimate.

    Returns
    -------
    dict
        A layer mapping consumable by :func:`eocube.run_all_gates`, carrying an
        additional ``provenance`` key.

    Notes
    -----
    Where a declared value contradicts an observed one — a declared CRS that
    differs from the file's, for instance — the observed value wins and the
    disagreement is recorded in ``provenance.conflicts``. Silently preferring
    the declaration would reintroduce exactly the failure this module exists to
    prevent.

    ``invalid_fraction`` covers every band (a pixel counts as invalid if any
    band is invalid there), with per-band figures in
    ``invalid_fraction_by_band`` and the read strategy in
    ``invalid_fraction_sampling``. ``dtype`` reports band 1 only. Note that
    :func:`eocube.pipeline.extract_samples` reads band 1 of a file-backed
    layer, so a multi-band file contributes one feature, not one per band.
    """
    _require_rasterio()
    path = Path(path)
    declared = dict(declared or {})
    prov = Provenance()
    layer: dict[str, Any] = {"name": name or path.stem, "path": str(path)}

    with rasterio.open(path) as src:
        obs: dict[str, Any] = {
            "crs": str(src.crs) if src.crs else None,
            "transform": tuple(src.transform)[:6],
            "width": src.width,
            "height": src.height,
            "gsd": float(abs(src.transform.a)),
            "dtype": src.dtypes[0],
            "nodata": src.nodata,
            "band_count": src.count,
        }
        if abs(abs(src.transform.a) - abs(src.transform.e)) > 1e-6:
            obs["gsd_y"] = float(abs(src.transform.e))
            prov.conflicts.append(
                f"{layer['name']}: non-square pixels "
                f"({abs(src.transform.a)} x {abs(src.transform.e)}); "
                "gsd reports the x dimension only"
            )
        if read_stats:
            stats = _invalid_fraction(src, max_pixels=max_stat_pixels)
            obs["invalid_fraction"] = stats.pop("invalid_fraction")
            obs["invalid_fraction_by_band"] = stats.pop("invalid_fraction_by_band")
            obs["invalid_fraction_sampling"] = stats
        layer.update(obs)
        prov.observed = obs

    # declared fields: record, and flag anything that contradicts the file
    for key, value in declared.items():
        if key in OBSERVABLE and key in prov.observed:
            if not _same(key, prov.observed[key], value):
                prov.conflicts.append(
                    f"{layer['name']}: declared {key}={value!r} contradicts "
                    f"observed {key}={prov.observed[key]!r}; the observed value is used"
                )
            continue  # never let a declaration overwrite a measurement
        layer[key] = value
        prov.declared[key] = value

    unverifiable = [k for k in DECLARED_ONLY if k in prov.declared]
    if unverifiable:
        prov.declared["_unverifiable_from_pixels"] = unverifiable

    layer["provenance"] = prov.as_dict()
    return layer


def describe_many(
    sources: Sequence[tuple[str | Path, str, dict[str, Any]]],
    *,
    read_stats: bool = True,
) -> list[dict[str, Any]]:
    """Describe several rasters at once.

    ``sources`` is a sequence of ``(path, name, declared)`` triples.
    """
    return [
        describe(p, n, declared=d, read_stats=read_stats) for p, n, d in sources
    ]


def to_descriptor(
    layers: Sequence[dict[str, Any]],
    report: Any | None = None,
    *,
    cube_id: str | None = None,
    target_time: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialise a cube and its contract report to a machine-readable document.

    This is the artefact an automated system consumes. The motivation is the
    one set out by the AutoML-for-Earth-Observation community: an automated
    system can only reason about a dataset if the dataset describes itself in a
    consistent, machine-readable form. A cube that merely *is* valid is not
    enough; it has to be able to *state* in what respects it was checked, what
    was decided on its behalf, and what remains unverified.

    The document separates three things a consumer needs to distinguish:
    measurements read from the files, assertions made by whoever assembled the
    cube, and decisions taken by the gates during assembly.
    """
    doc: dict[str, Any] = {
        "descriptor_version": DESCRIPTOR_VERSION,
        "cube_id": cube_id or "unnamed-cube",
        "generated": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "target_time": target_time.isoformat() if target_time else None,
        "layers": [],
    }
    for l in layers:
        entry = {k: v for k, v in l.items() if k != "provenance"}
        entry["provenance"] = l.get("provenance", {"observed": {}, "declared": {}, "conflicts": []})
        doc["layers"].append(entry)

    if report is not None:
        doc["contract_report"] = report.to_dict()
        doc["provenance_log"] = report.provenance()
        doc["verdict"] = "accepted" if report.ok else "refused"
    else:
        doc["contract_report"] = None
        doc["verdict"] = "unchecked"

    conflicts = [c for l in layers for c in l.get("provenance", {}).get("conflicts", [])]
    doc["metadata_conflicts"] = conflicts

    if extra:
        doc["extra"] = extra
    return _json_safe(doc)


def _json_safe(obj: Any) -> Any:
    """Replace values strict JSON cannot carry.

    A float raster's nodata is commonly NaN, and ``json.dumps`` writes that as a
    bare ``NaN`` token, which is not JSON: a browser or any strict parser
    rejects the whole descriptor. Non-finite floats become the strings
    ``"nan"``, ``"inf"`` and ``"-inf"`` rather than ``null``, because ``null``
    already means "no nodata value" and the two must stay distinguishable.
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if _HAVE_RASTERIO and isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return "nan" if math.isnan(obj) else ("inf" if obj > 0 else "-inf")
    return obj


def write_descriptor(doc: dict[str, Any], path: str | Path) -> Path:
    """Write a cube descriptor to disk as indented JSON."""
    path = Path(path)
    path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    return path
