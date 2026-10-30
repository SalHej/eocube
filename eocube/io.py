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
#: 0.4: gsd_units and ground GSD for geographic CRS, zero_fill_fraction, value_range,
#:      declared nodata honoured when the file has none.
#: 0.5: implausible_fraction against a declared plausible_range.
#: 0.6: sample_pixel for a declared sample_point (point-sampled cubes).
#: 0.7: the value at the sample pixel, its nodata and plausibility, and its QA class.
DESCRIPTOR_VERSION = "eocube-cube-descriptor/0.7"

#: Fields that can be read from a raster, and therefore verified.
OBSERVABLE = (
    "crs", "transform", "width", "height", "gsd", "gsd_units", "gsd_ground_m", "dtype",
    "nodata", "invalid_fraction", "invalid_fraction_by_band", "invalid_fraction_sampling",
    "zero_fill_fraction", "band_count", "value_range", "implausible_fraction",
    "implausible_fraction_by_band", "sample_pixel",
)

#: Fields that cannot be derived from pixels and must be declared by the
#: caller. Each is recorded with its source so that a reader can tell the
#: difference between a measurement and an assertion.
DECLARED_ONLY = (
    "modality", "units", "calibrated", "terrain_corrected", "reflectance",
    "qa_mask_available", "qa_mask_applied", "acquisition_time", "sensor",
    "processing_level", "plausible_range", "sample_point", "qa_mask_path", "qa_valid_classes",
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


def _gsd_units(crs) -> str | None:
    """Unit of the transform's pixel size: ``"metre"``, ``"degree"``, or the CRS's own.

    The first real Sentinel-2 file this package read was in EPSG:4326, and its
    pixel size of 0.0000898 was reported, and gated, as metres. A GSD is only a
    number in some unit; without the unit, two layers cannot be compared.
    """
    if crs is None:
        return None
    if crs.is_geographic:
        return "degree"
    units = str(crs.linear_units or "unknown").lower()
    return "metre" if units in {"metre", "meter", "m", "metres", "meters"} else units


def _ground_gsd_m(transform, height: int) -> tuple[float, float]:
    """Approximate ground pixel size in metres for a geographic grid.

    Uses WGS84 metres-per-degree at the grid's centre latitude. At 54.7 N a
    "10 m" 0.0000898-degree pixel is about 5.8 m east-west by 10 m north-south:
    square in degrees, not on the ground.
    """
    lat = math.radians(transform.f + transform.e * height / 2.0)
    m_lat = 111132.954 - 559.822 * math.cos(2 * lat) + 1.175 * math.cos(4 * lat)
    m_lon = (111412.84 * math.cos(lat) - 93.5 * math.cos(3 * lat)
             + 0.118 * math.cos(5 * lat))
    return round(abs(transform.a) * m_lon, 3), round(abs(transform.e) * m_lat, 3)


def _sample_pixel(src, lon: float, lat: float, *, assumed_nodata: float | None = None,
                  plausible_range: Sequence[float | None] | None = None) -> dict[str, Any]:
    """Where a declared sample point falls in this raster, and what is there.

    A point-sampled cube reads each layer at its own pixel containing the point, in
    its own CRS, with no common grid. What must then agree is the ground location
    each layer was read at. This records it: the pixel, whether the point is inside
    the raster, and the pixel centre in lon/lat with its distance from the point, so
    that :func:`eocube.check_geometry_crs` can compare layers without a geospatial
    stack of its own.

    It also records the value at that pixel and whether it is an observation: nodata
    or non-finite in any band, or outside the declared ``plausible_range``. In a
    point-sampled cube that pixel is the sample. Layer-level fractions describe the
    rest of the chip, which the sample never uses: on the 2019 Twente cube, 1,123
    samples read through cloud, shadow, water or fill at the station passed every
    layer-level gate (docs/open_ablation.md).
    """
    from rasterio.warp import transform as _tr
    x, y = _tr("EPSG:4326", src.crs, [lon], [lat])
    row, col = src.index(x[0], y[0])
    inside = 0 <= row < src.height and 0 <= col < src.width
    cx, cy = src.xy(row, col)                  # pixel centre, in the raster's CRS
    clon, clat = _tr(src.crs, "EPSG:4326", [cx], [cy])
    out = {"point_lonlat": [lon, lat], "row": int(row), "col": int(col), "inside": bool(inside),
           "centre_lonlat": [float(clon[0]), float(clat[0])],
           "offset_m": round(_ground_distance_m(lon, lat, clon[0], clat[0]), 3)}
    if not inside:
        return out
    values = src.read(window=((row, row + 1), (col, col + 1)))[:, 0, 0]
    nodata = [v if v is not None else assumed_nodata for v in src.nodatavals]
    missing = any((nd is not None and not (isinstance(nd, float) and math.isnan(nd)) and v == nd)
                  or (np.issubdtype(values.dtype, np.floating) and not np.isfinite(v))
                  for v, nd in zip(values, nodata))
    out["values"] = [_json_safe(v.item()) for v in values]
    out["nodata"] = bool(missing)
    if plausible_range is not None and not missing:
        lo, hi = plausible_range
        out["implausible"] = bool(any((lo is not None and v < lo) or (hi is not None and v > hi)
                                      for v in values))
    return out


def _qa_at(path: str | Path, lon: float, lat: float, valid: Sequence[int]) -> dict[str, Any]:
    """The QA class at a point, read from a quality raster on its own grid.

    A quality mask is often a separate file at another resolution (Sentinel-2 SCL is
    20 m; B02 is 10 m). Reading it at the same ground point, rather than at the same
    row and column, keeps the two aligned whatever their grids.
    """
    from rasterio.warp import transform as _tr
    with rasterio.open(path) as q:
        x, y = _tr("EPSG:4326", q.crs, [lon], [lat])
        row, col = q.index(x[0], y[0])
        if not (0 <= row < q.height and 0 <= col < q.width):
            return {"qa_inside": False, "qa_valid": False}
        cls = q.read(1, window=((row, row + 1), (col, col + 1)))[0, 0].item()
    return {"qa_inside": True, "qa_class": cls, "qa_valid": cls in set(valid)}


def _ground_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Haversine distance in metres (spherical Earth; ample for pixel-scale offsets)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def _invalid_fraction(
    src, max_pixels: int = 4_000_000, assumed_nodata: float | None = None,
    plausible_range: Sequence[float] | None = None,
) -> dict[str, Any]:
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

    ``assumed_nodata`` is a caller-declared fill value, used only for bands whose
    file declares none; ``nodata_source`` in the sampling record says which was
    used. When no band has a nodata value at all, ``zero_fill_fraction`` reports
    the pixels that are exactly zero in every band. The real Sentinel-2
    embeddings this was written against left 40% of a tile as zero vectors with
    no nodata tag, and were measured as 0% invalid. Zero is not always fill, so
    this is evidence for the quality gate to weigh, not a count of invalid pixels.
    ``value_range`` is the (min, max) over valid pixels of all bands.

    ``plausible_range`` is a caller-declared physical range ``(lo, hi)``. Pixels
    that are not nodata, and so read as data, but fall outside it are counted in
    ``implausible_fraction`` (positions implausible in any band) and
    ``implausible_fraction_by_band``. This catches fill that does not look like
    fill: at a Sentinel-2 swath edge, L2A B02 read 1 DN (a reflectance of 0.0001)
    where SCL claimed data and nodata (0) was not set. Nodata checks cannot see
    such a pixel; a physical bound can. The range is declared because only the
    caller knows the sensor, units and surface; the pixels alone cannot say.
    Either bound may be ``None`` (unbounded on that side). Fill is usually small, so a
    lower bound alone is often the right declaration: an upper bound guessed for SAR
    (10, linear) flagged real strong scatterers (VV up to 1043) in 1,128 of 3,505
    Twente samples.
    """
    h, w = src.height, src.width
    step = max(1, int(math.sqrt((h * w) / max_pixels)))
    shape = (max(1, h // step), max(1, w // step))
    total = shape[0] * shape[1]
    file_nodata = [v for v in src.nodatavals if v is not None]
    sampling = {
        "decimation_step": step,
        "pixels_read": total,
        "pixels_total": h * w,
        "estimated": step > 1,
        "nodata_source": "file" if file_nodata else (
            "declared" if assumed_nodata is not None else None),
    }
    if total == 0 or src.count == 0:
        return {"invalid_fraction": 1.0, "invalid_fraction_by_band": [],
                "value_range": None, **sampling}

    any_bad = np.zeros(shape, dtype=bool)
    any_implausible = np.zeros(shape, dtype=bool)
    all_zero = np.ones(shape, dtype=bool)
    by_band: list[float] = []
    implausible_by_band: list[float] = []
    lo, hi = math.inf, -math.inf
    for b in range(1, src.count + 1):
        data = src.read(b, out_shape=shape)
        nodata = src.nodatavals[b - 1]
        if nodata is None:
            nodata = assumed_nodata
        bad = np.zeros(shape, dtype=bool)
        if nodata is not None and not (isinstance(nodata, float) and math.isnan(nodata)):
            bad |= data == nodata
        if np.issubdtype(data.dtype, np.floating):
            bad |= ~np.isfinite(data)     # a mask, so non-finite nodata is not counted twice
        any_bad |= bad
        all_zero &= data == 0
        by_band.append(round(float(bad.mean()), 6))
        valid = data[~bad]
        if valid.size:
            lo, hi = min(lo, float(valid.min())), max(hi, float(valid.max()))
        if plausible_range is not None:
            plo, phi = plausible_range               # either bound may be None: unbounded
            implausible = np.zeros(shape, dtype=bool)
            if plo is not None:
                implausible |= data < plo
            if phi is not None:
                implausible |= data > phi
            implausible &= ~bad
            any_implausible |= implausible
            implausible_by_band.append(round(float(implausible.mean()), 6))
    out = {
        "invalid_fraction": round(float(any_bad.mean()), 6),
        "invalid_fraction_by_band": by_band,
        "value_range": [lo, hi] if lo <= hi else None,
        **sampling,
    }
    if plausible_range is not None:
        out["implausible_fraction"] = round(float(any_implausible.mean()), 6)
        out["implausible_fraction_by_band"] = implausible_by_band
    if sampling["nodata_source"] is None:
        out["zero_fill_fraction"] = round(float(all_zero.mean()), 6)
    return out


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
    prevent. The one exception is ``nodata`` when the file declares none: a
    declared fill value then fills a gap rather than contradicting a
    measurement, so it is used for the invalid-fraction pass and recorded as
    declared, while ``nodata`` itself stays the observed ``None``.

    ``gsd`` is in the CRS's units, named in ``gsd_units``. For a geographic CRS
    that is degrees, and ``gsd_ground_m`` gives the approximate ground size.

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
        units = _gsd_units(src.crs)
        obs: dict[str, Any] = {
            "crs": str(src.crs) if src.crs else None,
            "transform": tuple(src.transform)[:6],
            "width": src.width,
            "height": src.height,
            "gsd": float(abs(src.transform.a)),
            "gsd_units": units,
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
        if units == "degree":
            gx, gy = _ground_gsd_m(src.transform, src.height)
            obs["gsd_ground_m"] = [gx, gy]
            if not math.isclose(gx, gy, rel_tol=0.01):
                prov.conflicts.append(
                    f"{layer['name']}: geographic CRS; gsd is in degrees, and pixels are "
                    f"about {gx:g} m x {gy:g} m on the ground at this latitude, not square"
                )
        # A declared fill value is used only where the file declares none:
        # that fills a gap in the file's metadata rather than contradicting it.
        assumed = declared.get("nodata") if src.nodata is None else None
        point = declared.get("sample_point")
        if point is not None:
            obs["sample_pixel"] = _sample_pixel(src, *point, assumed_nodata=assumed,
                                                plausible_range=declared.get("plausible_range"))
            qa_path, qa_valid = declared.get("qa_mask_path"), declared.get("qa_valid_classes")
            if qa_path is not None and qa_valid is not None and obs["sample_pixel"]["inside"]:
                obs["sample_pixel"].update(_qa_at(qa_path, *point, qa_valid))
        if read_stats:
            stats = _invalid_fraction(src, max_pixels=max_stat_pixels,
                                      assumed_nodata=assumed,
                                      plausible_range=declared.get("plausible_range"))
            obs["invalid_fraction"] = stats.pop("invalid_fraction")
            obs["invalid_fraction_by_band"] = stats.pop("invalid_fraction_by_band")
            obs["value_range"] = stats.pop("value_range")
            for key in ("zero_fill_fraction", "implausible_fraction",
                        "implausible_fraction_by_band"):
                if key in stats:
                    obs[key] = stats.pop(key)
            obs["invalid_fraction_sampling"] = stats
        layer.update(obs)
        prov.observed = obs

    # declared fields: record, and flag anything that contradicts the file
    for key, value in declared.items():
        if key == "nodata" and prov.observed.get("nodata") is None:
            prov.declared[key] = value       # file is silent; recorded as an assertion
            continue
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
