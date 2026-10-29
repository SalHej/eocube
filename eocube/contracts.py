"""
eocube.contracts
================

Reference implementation of the five machine-checkable contract gates proposed in
Table 1 of:

    Al-Heejawi, S. M. A. (2026). Agentic large language model workflows for Earth
    Observation: tool orchestration, foundation-model adaptation, and the evaluation
    gap. EarthArXiv. https://doi.org/10.31223/X5TN59

Design principle
----------------
A contract gate is a deterministic, independently ablatable check on a multi-source
Earth Observation data cube. Each gate returns a ``GateResult`` carrying an explicit
verdict, the evidence behind that verdict, and any decision the gate made on the
caller's behalf.

Two behaviours distinguish this from ordinary validation:

1. **Nothing is coerced silently.** If a gate must resample, composite or gap-fill
   to proceed, it records that decision in ``GateResult.decisions``. Provenance is
   a first-class output, not a side effect.

2. **Failure produces refusal, not repair.** A gate that cannot satisfy its contract
   returns ``Verdict.REFUSE``, and downstream code is expected to stop. For a system
   whose outputs are scientific claims, proceeding under insufficient evidence is the
   most consequential failure mode, and it must be measurable.

The gates are dependency-light: they operate on plain metadata dictionaries, so they
can be exercised in CI without a geospatial stack. Use ``eocube.io.describe`` to build
those dictionaries from real rasters.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Sequence

__all__ = [
    "Verdict", "GateResult", "ContractReport",
    "check_geometry_crs", "check_resolution", "check_temporal_alignment",
    "check_quality_missingness", "check_sensor_physics",
    "run_all_gates", "GATES",
]


class Verdict(str, Enum):
    """Outcome of a single contract gate."""

    PASS = "pass"
    #: Contract satisfied, but the gate made a decision the caller should see.
    PASS_WITH_DECISION = "pass_with_decision"
    #: Contract cannot be satisfied. The cube must not be used for inference.
    REFUSE = "refuse"
    #: The gate had nothing to inspect: no acquisition times, no layer of a
    #: modality it has rules for. Not a pass. Silently omitting the gate made an
    #: idle check indistinguishable from one that was never configured.
    NOT_EVALUATED = "not_evaluated"


@dataclass
class GateResult:
    gate: str
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.verdict is not Verdict.REFUSE

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        head = f"[{self.verdict.value.upper():18}] {self.gate}"
        body = "".join(f"\n    reason:   {r}" for r in self.reasons)
        body += "".join(f"\n    decision: {d}" for d in self.decisions)
        return head + body


@dataclass
class ContractReport:
    """Aggregate result plus the full provenance log for a cube."""

    results: list[GateResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def refusals(self) -> list[GateResult]:
        return [r for r in self.results if r.verdict is Verdict.REFUSE]

    def provenance(self) -> list[str]:
        """Every decision made on the caller's behalf, in gate order."""
        return [f"{r.gate}: {d}" for r in self.results for d in r.decisions]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "gates": [
                {
                    "gate": r.gate,
                    "verdict": r.verdict.value,
                    "reasons": r.reasons,
                    "decisions": r.decisions,
                    "evidence": r.evidence,
                }
                for r in self.results
            ],
        }

    @property
    def not_evaluated(self) -> list[GateResult]:
        return [r for r in self.results if r.verdict is Verdict.NOT_EVALUATED]

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        lines = [str(r) for r in self.results]
        lines.append(
            f"\nCUBE VERDICT: {'ACCEPTED' if self.ok else 'REFUSED'} "
            f"({len(self.refusals)} refusing gate(s), "
            f"{len(self.not_evaluated)} not evaluated)"
        )
        return "\n".join(lines)


# --------------------------------------------------------------------------------
# Gate 1 — geometry and coordinate reference system
# --------------------------------------------------------------------------------
def check_geometry_crs(
    layers: Sequence[dict[str, Any]],
    *,
    require_identical_crs: bool = True,
    max_grid_offset_px: float = 0.1,
    mode: str = "grid",
    max_point_offset_m: float | None = None,
) -> GateResult:
    """Verify reprojection correctness and pixel-grid co-registration.

    Each layer is a mapping with at least ``name``, ``crs``, ``transform``
    (an affine as a 6-tuple), ``width`` and ``height``.

    ``max_grid_offset_px`` is the largest sub-pixel origin offset tolerated between
    layers, in reference pixels. The offset is wrapped to the nearest grid line, so
    its maximum possible value is 0.5; 0.1 is a reasonable default for fusion work,
    and tightening it is usually the right move.

    The failure prevented is sub-pixel misregistration, which silently degrades
    fusion and is then attributed to the model architecture rather than the data.

    ``mode="point"`` is for cubes that are not image stacks: each layer is read at
    its own pixel containing one ground point, in its own CRS, with no common grid
    and no resampling. Grid agreement is then the wrong question; coordinate
    agreement is the right one. See :func:`_check_point`. Grid mode remains the
    default; choosing point mode is a statement about the method, recorded in the
    provenance log every time.
    """
    res = GateResult(gate="geometry_crs", verdict=Verdict.PASS)
    if not layers:
        res.verdict = Verdict.REFUSE
        res.reasons.append("no layers supplied")
        return res
    if mode == "point":
        return _check_point(layers, res, max_point_offset_m)
    if mode != "grid":
        raise ValueError(f"geometry mode must be 'grid' or 'point', not {mode!r}")

    crs_set = {str(l.get("crs")) for l in layers}
    res.evidence["crs_observed"] = sorted(crs_set)
    if require_identical_crs and len(crs_set) > 1:
        res.verdict = Verdict.REFUSE
        res.reasons.append(
            f"layers declare {len(crs_set)} distinct CRS: {sorted(crs_set)}; "
            "reproject to a common CRS before cube assembly"
        )
        return res
    if None in {l.get("crs") for l in layers} or "None" in crs_set:
        res.verdict = Verdict.REFUSE
        res.reasons.append("at least one layer has an undeclared CRS")
        return res

    ref = layers[0]
    ra, _, rc, _, re_, rf = ref["transform"]
    px = abs(ra) or 1.0
    py = abs(re_) or 1.0
    offsets = {}
    for l in layers[1:]:
        a, _, c, _, e, f = l["transform"]
        dx = ((c - rc) / px) % 1.0
        dy = ((f - rf) / py) % 1.0
        dx = min(dx, 1.0 - dx)
        dy = min(dy, 1.0 - dy)
        offsets[l["name"]] = (round(dx, 4), round(dy, 4))
        if dx > max_grid_offset_px or dy > max_grid_offset_px:
            res.verdict = Verdict.REFUSE
            res.reasons.append(
                f"layer '{l['name']}' is offset from the reference grid by "
                f"({dx:.3f}, {dy:.3f}) px, exceeding the tolerance of "
                f"{max_grid_offset_px} px; co-registration is not verified"
            )
    res.evidence["subpixel_offsets"] = offsets
    return res


def _ground_m(a: Sequence[float], b: Sequence[float]) -> float:
    """Haversine distance in metres between two (lon, lat) points."""
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _check_point(layers, res: GateResult, max_point_offset_m: float | None) -> GateResult:
    """Point-mode geometry: every layer read at the same ground point, consistently.

    Each layer must carry a declared ``sample_point`` (lon, lat) and the observed
    ``sample_pixel`` that :func:`eocube.io.describe` records for it. The gate refuses
    if:
      - a layer has no CRS, or lacks either field;
      - the layers declare different sample points (more than 1 m apart), i.e. they
        were read for different locations;
      - the point lies outside a layer;
      - a layer's sampled pixel centre is further from the point than half its pixel
        diagonal. That is impossible if the CRS and transform were handled
        correctly, so it means they were not.
    Mixed CRS are allowed: that is the point of the mode. The largest distance
    between any two layers' sampled pixel centres is reported. If
    ``max_point_offset_m`` is given, a larger spread is refused: different-sized
    pixels at the same point average over different ground.
    """
    res.evidence["mode"] = "point"
    res.evidence["crs_observed"] = sorted({str(l.get("crs")) for l in layers})
    centres, offsets = {}, {}
    for l in layers:
        name, sp, px = l["name"], l.get("sample_point"), l.get("sample_pixel")
        if not l.get("crs"):
            res.verdict = Verdict.REFUSE
            res.reasons.append(f"layer '{name}' has an undeclared CRS")
            continue
        if sp is None or px is None:
            res.verdict = Verdict.REFUSE
            res.reasons.append(
                f"layer '{name}' lacks sample_point/sample_pixel; point mode needs "
                "describe(..., declared={'sample_point': (lon, lat)})")
            continue
        if not px.get("inside"):
            res.verdict = Verdict.REFUSE
            res.reasons.append(f"sample point {list(sp)} lies outside layer '{name}'")
            continue
        gsd = l.get("gsd_ground_m") or ([l["gsd"]] * 2 if l.get("gsd_units", "metre") == "metre"
                                        else None)
        if gsd:
            half_diag = 0.5 * math.hypot(*gsd) + 0.01
            if px["offset_m"] > half_diag:
                res.verdict = Verdict.REFUSE
                res.reasons.append(
                    f"layer '{name}': the pixel read is centred {px['offset_m']:.2f} m from the "
                    f"sample point, more than its half-diagonal ({half_diag:.2f} m); the CRS "
                    "or transform was not applied consistently")
        centres[name], offsets[name] = px["centre_lonlat"], px["offset_m"]
    points = {tuple(l["sample_point"]) for l in layers if l.get("sample_point") is not None}
    if len(points) > 1:
        pts = list(points)
        spread = max(_ground_m(a, b) for a in pts for b in pts)
        if spread > 1.0:
            res.verdict = Verdict.REFUSE
            res.reasons.append(
                f"layers declare {len(points)} different sample points, up to {spread:.1f} m "
                "apart; they were read for different locations")
    names = list(centres)
    pair = max((_ground_m(centres[a], centres[b]) for a in names for b in names), default=0.0)
    res.evidence["centre_offset_from_point_m"] = offsets
    res.evidence["max_pairwise_centre_m"] = round(pair, 3)
    if max_point_offset_m is not None and pair > max_point_offset_m:
        res.verdict = Verdict.REFUSE
        res.reasons.append(
            f"sampled pixel centres are up to {pair:.1f} m apart, above the "
            f"{max_point_offset_m} m tolerance")
    if res.verdict is Verdict.PASS:
        res.verdict = Verdict.PASS_WITH_DECISION
    res.decisions.append(
        f"point sampling: each of {len(layers)} layers read at its own pixel containing "
        f"the sample point, in {len(res.evidence['crs_observed'])} CRS, with no common grid "
        f"and no resampling; sampled pixel centres up to {pair:.1f} m apart")
    return res


# --------------------------------------------------------------------------------
# Gate 2 — resolution harmonisation
# --------------------------------------------------------------------------------
def check_resolution(
    layers: Sequence[dict[str, Any]],
    *,
    target_gsd: float | None = None,
    allow_upsampling: bool = False,
    resampling: str = "bilinear",
) -> GateResult:
    """Reconcile ground sample distance across bands and sensors.

    Upsampling a coarse band to a fine grid manufactures detail that was never
    observed. Where it is permitted it must be recorded, because effective
    resolution is otherwise overstated in every downstream result.

    GSDs are compared only within one unit. A layer's ``gsd_units`` (set by
    :func:`eocube.io.describe`) defaults to metres when absent; ``target_gsd`` is
    always metres. A geographic layer's GSD is in degrees, and comparing it
    with metres, or labelling it as metres, is meaningless.
    """
    res = GateResult(gate="resolution", verdict=Verdict.PASS)
    gsds = {l["name"]: float(l["gsd"]) for l in layers}
    res.evidence["gsd_by_layer"] = gsds
    if not gsds:
        res.verdict = Verdict.REFUSE
        res.reasons.append("no layers supplied")
        return res

    units = {l["name"]: l.get("gsd_units", "metre") or "unknown" for l in layers}
    res.evidence["gsd_units"] = units
    if len(set(units.values())) > 1:
        res.verdict = Verdict.REFUSE
        res.reasons.append(
            f"layers report GSD in different units {sorted(set(units.values()))}; "
            "reproject to a common CRS before comparing resolution"
        )
        return res
    unit = next(iter(units.values()))
    if target_gsd is not None and unit != "metre":
        res.verdict = Verdict.REFUSE
        res.reasons.append(
            f"target_gsd={target_gsd} is in metres but the layers are on a {unit} grid; "
            "reproject to a projected CRS first"
        )
        return res
    u = " m" if unit == "metre" else f" {unit}"
    if unit == "degree":
        ground = {l["name"]: l["gsd_ground_m"] for l in layers if l.get("gsd_ground_m")}
        if ground:
            res.evidence["gsd_ground_m"] = ground
        res.decisions.append(
            "GSD is in degrees (geographic CRS); ground resolution varies with latitude "
            "and pixels are not square on the ground"
        )

    target = float(target_gsd) if target_gsd is not None else max(gsds.values())
    res.evidence["target_gsd"] = target
    if target_gsd is None:
        res.decisions.append(
            f"no target GSD supplied; defaulted to the coarsest input ({target}{u}) "
            "so that no layer is upsampled"
        )

    for name, g in gsds.items():
        if g > target:
            if not allow_upsampling:
                res.verdict = Verdict.REFUSE
                res.reasons.append(
                    f"layer '{name}' at {g}{u} would be upsampled to {target}{u}; "
                    "set allow_upsampling=True and justify it, or coarsen the target"
                )
            else:
                res.decisions.append(
                    f"layer '{name}' upsampled {g}{u} -> {target}{u} using "
                    f"{resampling}; effective resolution remains {g}{u}"
                )
        elif g < target:
            res.decisions.append(
                f"layer '{name}' downsampled {g}{u} -> {target}{u} using {resampling}"
            )
    if res.decisions and res.verdict is Verdict.PASS:
        res.verdict = Verdict.PASS_WITH_DECISION
    return res


# --------------------------------------------------------------------------------
# Gate 3 — temporal alignment
# --------------------------------------------------------------------------------
def check_temporal_alignment(
    acquisitions: dict[str, Iterable[datetime]],
    target_time: datetime,
    *,
    window_days: float = 7.0,
    decay_alpha: float | None = None,
    forbid_future: bool = True,
) -> GateResult:
    """Verify compositing window, asynchrony and absence of temporal leakage.

    ``decay_alpha`` optionally reports the exponential weight ``exp(-alpha*|dt|)``
    applied to the nearest acquisition per source, following the formulation used
    for asynchronous multi-sensor fusion in peatland moisture retrieval. Setting it
    does not change the verdict; it records the weighting so that a downstream model
    cannot apply one silently.

    Temporal leakage is the failure of consequence: an observation acquired after
    ``target_time`` carries information about the target and will inflate validation
    scores in exactly the continuous-regression settings where fusion is most used.
    """
    res = GateResult(gate="temporal_alignment", verdict=Verdict.PASS)
    res.evidence["target_time"] = target_time.isoformat()
    res.evidence["window_days"] = window_days
    summary: dict[str, Any] = {}

    for source, times in acquisitions.items():
        times = sorted(times)
        if not times:
            res.verdict = Verdict.REFUSE
            res.reasons.append(f"source '{source}' has no acquisitions")
            continue

        future = [t for t in times if t > target_time]
        if forbid_future and future:
            res.verdict = Verdict.REFUSE
            res.reasons.append(
                f"source '{source}' contains {len(future)} acquisition(s) after the "
                "target time; this is temporal leakage. Truncate the conditioning "
                "window or set forbid_future=False and justify it."
            )

        usable = [t for t in times if (not forbid_future) or t <= target_time]
        if not usable:
            res.verdict = Verdict.REFUSE
            res.reasons.append(
                f"source '{source}' has no usable acquisition at or before the target time"
            )
            continue

        nearest = min(usable, key=lambda t: abs((t - target_time).total_seconds()))
        dt_days = abs((nearest - target_time).total_seconds()) / 86400.0
        entry: dict[str, Any] = {
            "n_acquisitions": len(times),
            "nearest": nearest.isoformat(),
            "delta_days": round(dt_days, 3),
        }
        if decay_alpha is not None:
            entry["weight"] = round(math.exp(-decay_alpha * dt_days), 6)
        summary[source] = entry

        if dt_days > window_days:
            if res.verdict is Verdict.PASS:
                res.verdict = Verdict.PASS_WITH_DECISION
            res.decisions.append(
                f"source '{source}' nearest acquisition is {dt_days:.1f} d from the "
                f"target, outside the declared {window_days} d window; retained and "
                "flagged rather than dropped"
            )

    res.evidence["per_source"] = summary
    if decay_alpha is not None:
        res.evidence["decay_alpha"] = decay_alpha
        res.decisions.append(
            f"exponential time-decay weighting exp(-{decay_alpha}*|dt|) recorded per source"
        )
    return res


# --------------------------------------------------------------------------------
# Gate 4 — quality flags and missingness
# --------------------------------------------------------------------------------
def check_quality_missingness(
    layers: Sequence[dict[str, Any]],
    *,
    max_invalid_fraction: float = 0.30,
    gap_fill: str | None = None,
    max_implausible_fraction: float = 0.01,
) -> GateResult:
    """Verify that quality masks are honoured and gap-filling is declared.

    Each layer should carry ``invalid_fraction`` in [0, 1] and, where relevant,
    ``qa_mask_applied``. The failure prevented is gap-filled or cloud-contaminated
    values entering the model as if they were observations.

    A layer with no ``invalid_fraction`` is reported as *not measured*, not as
    clean: reading an absent figure as zero would let an unexamined layer pass
    as a perfect one. If the figure came from a decimated read
    (``invalid_fraction_sampling``, set by :func:`eocube.io.describe`), the
    evidence carries the decimation step and the messages call it an estimate.

    A layer that declares no nodata value can only be measured for NaN, so a
    fill of zeros reads as 0% invalid. ``zero_fill_fraction`` (pixels exactly
    zero in every band) is the evidence against that. In a multi-band layer a
    pixel that is 0 in every band is almost never a measurement, so above the
    threshold it is treated like measured invalidity. In a single band, zero is
    often a legitimate value (a mask, a class, no rain), so it is only recorded.

    Missingness is not the only way a pixel can fail to be an observation. A
    fill value that is not flagged as nodata reads as data. The case that
    prompted this check: at a Sentinel-2 swath edge, L2A B02 read 1 DN (reflectance
    0.0001) with SCL claiming data. Where a layer declares a physical
    ``plausible_range``, :func:`eocube.io.describe` measures
    ``implausible_fraction``: pixels outside the range that are not nodata. Above
    ``max_implausible_fraction`` the layer is refused. A declared gap-fill policy does
    not excuse this, because these pixels are not flagged as gaps, so no fill would
    touch them. A layer that declares a range that was never measured is reported as
    unmeasured. A layer that declares no range is not checked: physical plausibility
    depends on the sensor, units and surface, which the gate cannot infer.
    """
    res = GateResult(gate="quality_missingness", verdict=Verdict.PASS)
    per_layer: dict[str, Any] = {}

    for l in layers:
        name = l["name"]
        applied = bool(l.get("qa_mask_applied", False))
        raw = l.get("invalid_fraction")
        frac = None if raw is None else float(raw)
        per_layer[name] = {
            "invalid_fraction": None if frac is None else round(frac, 4),
            "qa_mask_applied": applied,
        }
        sampling = l.get("invalid_fraction_sampling")
        estimated = bool(sampling and sampling.get("estimated"))
        if sampling:
            per_layer[name]["sampling"] = dict(sampling)
        how = (f" (estimated from a 1-in-{sampling['decimation_step']} decimated read)"
               if estimated else "")

        if l.get("qa_mask_available", False) and not applied:
            res.verdict = Verdict.REFUSE
            res.reasons.append(
                f"layer '{name}' has a quality mask available but not applied"
            )
        if frac is None:
            if res.verdict is Verdict.PASS:
                res.verdict = Verdict.PASS_WITH_DECISION
            res.decisions.append(
                f"layer '{name}' invalid fraction not measured; missingness is unknown, "
                "not zero"
            )
        elif frac > max_invalid_fraction:
            if gap_fill is None:
                res.verdict = Verdict.REFUSE
                res.reasons.append(
                    f"layer '{name}' is {frac:.1%} invalid{how}, above the "
                    f"{max_invalid_fraction:.0%} threshold, and no gap-filling policy "
                    "was declared"
                )
            else:
                if res.verdict is Verdict.PASS:
                    res.verdict = Verdict.PASS_WITH_DECISION
                res.decisions.append(
                    f"layer '{name}' {frac:.1%} invalid{how}; gap-filled using "
                    f"'{gap_fill}'. Filled pixels must be flagged in the cube and "
                    "reported separately in any downstream metric."
                )
        elif frac > 0:
            if res.verdict is Verdict.PASS:
                res.verdict = Verdict.PASS_WITH_DECISION
            res.decisions.append(
                f"layer '{name}' retains {frac:.1%} invalid pixels{how}, masked not filled"
            )

        zero = l.get("zero_fill_fraction")
        if zero:
            per_layer[name]["zero_fill_fraction"] = round(float(zero), 4)
            bands = int(l.get("band_count", 1) or 1)
            suspect = (f"layer '{name}' declares no nodata value, but {zero:.1%} of its "
                       f"pixels are exactly zero in all {bands} band(s){how}")
            if bands > 1 and zero > max_invalid_fraction and gap_fill is None:
                res.verdict = Verdict.REFUSE
                res.reasons.append(
                    f"{suspect}; that is almost certainly unflagged fill, above the "
                    f"{max_invalid_fraction:.0%} threshold. Declare the fill value "
                    "(declared={'nodata': 0}) or mask it"
                )
            else:
                if res.verdict is Verdict.PASS:
                    res.verdict = Verdict.PASS_WITH_DECISION
                res.decisions.append(
                    f"{suspect}; if that is fill, the true invalid fraction is at "
                    "least this high"
                )

        bounds = l.get("plausible_range")
        if bounds is not None:
            implausible = l.get("implausible_fraction")
            per_layer[name]["plausible_range"] = list(bounds)
            per_layer[name]["implausible_fraction"] = (
                None if implausible is None else round(float(implausible), 4))
            if implausible is None:
                if res.verdict is Verdict.PASS:
                    res.verdict = Verdict.PASS_WITH_DECISION
                res.decisions.append(
                    f"layer '{name}' declares a plausible range {list(bounds)} but it was "
                    "not measured; physically implausible values are unknown, not absent"
                )
            elif implausible > max_implausible_fraction:
                res.verdict = Verdict.REFUSE
                res.reasons.append(
                    f"layer '{name}': {implausible:.1%} of pixels are not nodata but lie "
                    f"outside the declared physical range {list(bounds)}{how}, above the "
                    f"{max_implausible_fraction:.0%} tolerance. These read as data and are "
                    "not; typically unflagged fill at a swath or tile edge"
                )
            elif implausible > 0:
                if res.verdict is Verdict.PASS:
                    res.verdict = Verdict.PASS_WITH_DECISION
                res.decisions.append(
                    f"layer '{name}' has {implausible:.2%} of pixels outside its declared "
                    f"physical range {list(bounds)}{how}, within the "
                    f"{max_implausible_fraction:.0%} tolerance; they must be masked, not used"
                )

    res.evidence["per_layer"] = per_layer
    res.evidence["max_implausible_fraction"] = max_implausible_fraction
    if gap_fill:
        res.evidence["gap_fill_policy"] = gap_fill
    return res


# --------------------------------------------------------------------------------
# Gate 5 — sensor physics
# --------------------------------------------------------------------------------
_OPTICAL = {"optical", "multispectral", "hyperspectral"}
_RADAR = {"sar", "radar"}
_IDENT = r"[A-Za-z0-9_]"


def _layers_in(expr: str, names: Iterable[str]) -> list[str]:
    """Layer names that occur in ``expr`` as whole tokens.

    Substring matching found ``B8`` inside ``B80`` and ``VV`` inside ``S1_VV``,
    so an expression could be refused for bands it never used, or passed for
    ones it did. A name matches only where it is not flanked by a letter, digit
    or underscore. Names containing operator characters (``S2-B08``) still
    match, but a layer named ``S2`` will then also match inside them: give
    layers identifier-style names if expressions are to be checked.
    """
    return [n for n in names
            if re.search(rf"(?<!{_IDENT}){re.escape(n)}(?!{_IDENT})", expr)]


def check_sensor_physics(
    layers: Sequence[dict[str, Any]],
    *,
    band_expressions: Sequence[str] = (),
    require_terrain_correction: bool = True,
) -> GateResult:
    """Verify calibration, units and modality-appropriate band arithmetic.

    SAR layers must declare units (``linear`` or ``db``) and, by default, terrain
    and incidence-angle correction. The failure prevented is arithmetic that is
    syntactically valid and physically meaningless: averaging decibels, or computing
    a normalised difference index across an optical and a radar band.

    A layer with no modality, or one this gate has no rules for (a learned
    embedding, say), is named in the decisions as unchecked rather than passed
    in silence. If no layer is checkable and there are no expressions, the
    verdict is ``NOT_EVALUATED``.
    """
    res = GateResult(gate="sensor_physics", verdict=Verdict.PASS)
    by_name = {l["name"]: l for l in layers}
    res.evidence["modalities"] = {n: l.get("modality") for n, l in by_name.items()}

    unchecked = [n for n, l in by_name.items()
                 if str(l.get("modality", "")).lower() not in _OPTICAL | _RADAR]
    res.evidence["unchecked_layers"] = unchecked
    for name in unchecked:
        modality = by_name[name].get("modality")
        res.decisions.append(
            f"layer '{name}' declares no modality; not checked" if not modality else
            f"layer '{name}' declares modality '{modality}', which this gate has no "
            "physical rules for; not checked"
        )
    if unchecked and len(unchecked) == len(by_name) and not band_expressions:
        res.verdict = Verdict.NOT_EVALUATED
        return res
    if unchecked:
        res.verdict = Verdict.PASS_WITH_DECISION

    for name, l in by_name.items():
        modality = str(l.get("modality", "")).lower()
        if modality in _RADAR:
            units = str(l.get("units", "")).lower()
            if units not in {"linear", "db"}:
                res.verdict = Verdict.REFUSE
                res.reasons.append(
                    f"SAR layer '{name}' does not declare units as 'linear' or 'db'"
                )
            if not l.get("calibrated", False):
                res.verdict = Verdict.REFUSE
                res.reasons.append(
                    f"SAR layer '{name}' is not declared radiometrically calibrated"
                )
            if require_terrain_correction and not l.get("terrain_corrected", False):
                res.verdict = Verdict.REFUSE
                res.reasons.append(
                    f"SAR layer '{name}' is not terrain/incidence-angle corrected; "
                    "backscatter will be topography-dependent"
                )
        elif modality in _OPTICAL:
            if not l.get("reflectance", False):
                if res.verdict is Verdict.PASS:
                    res.verdict = Verdict.PASS_WITH_DECISION
                res.decisions.append(
                    f"optical layer '{name}' is not declared as surface reflectance; "
                    "index arithmetic on raw DN is not comparable across scenes"
                )

    used_by_expr: dict[str, list[str]] = {}
    for expr in band_expressions:
        used = _layers_in(expr, by_name)
        used_by_expr[expr] = used
        mods = {str(by_name[n].get("modality", "")).lower() for n in used}
        if (mods & _OPTICAL) and (mods & _RADAR):
            res.verdict = Verdict.REFUSE
            res.reasons.append(
                f"expression '{expr}' combines optical and radar bands in a single "
                "arithmetic term; these measure different physical quantities and are "
                "not commensurable. Fuse at feature level instead."
            )
        db_used = [n for n in used if str(by_name[n].get("units", "")).lower() == "db"]
        if db_used and any(op in expr for op in ("+", "-", "mean", "avg")):
            res.verdict = Verdict.REFUSE
            res.reasons.append(
                f"expression '{expr}' applies additive arithmetic to decibel-scaled "
                f"layer(s) {db_used}; convert to linear power first"
            )
    res.evidence["expressions_checked"] = list(band_expressions)
    res.evidence["layers_by_expression"] = used_by_expr
    return res


GATES = (
    "geometry_crs",
    "resolution",
    "temporal_alignment",
    "quality_missingness",
    "sensor_physics",
)


def run_all_gates(
    layers: Sequence[dict[str, Any]],
    *,
    acquisitions: dict[str, Iterable[datetime]] | None = None,
    target_time: datetime | None = None,
    skip: Sequence[str] = (),
    **kwargs: Any,
) -> ContractReport:
    """Run every gate and return the aggregate report.

    ``skip`` exists so that each gate can be ablated individually, which is what
    makes "careful preprocessing" a measurable system property rather than a claim
    about researcher diligence. Ablation studies should record which gate was
    removed alongside the downstream metric.

    A gate that is not skipped but has no inputs (temporal alignment without
    ``acquisitions`` and ``target_time``) is reported as ``NOT_EVALUATED``
    rather than left out, so "skipped on purpose" and "had nothing to check"
    stay distinguishable.
    """
    report = ContractReport()

    if "geometry_crs" not in skip:
        report.results.append(
            check_geometry_crs(
                layers,
                require_identical_crs=kwargs.get("require_identical_crs", True),
                max_grid_offset_px=kwargs.get("max_grid_offset_px", 0.1),
                mode=kwargs.get("geometry_mode", "grid"),
                max_point_offset_m=kwargs.get("max_point_offset_m"),
            )
        )
    if "resolution" not in skip:
        report.results.append(
            check_resolution(
                layers,
                target_gsd=kwargs.get("target_gsd"),
                allow_upsampling=kwargs.get("allow_upsampling", False),
                resampling=kwargs.get("resampling", "bilinear"),
            )
        )
    if "temporal_alignment" not in skip and acquisitions and target_time:
        report.results.append(
            check_temporal_alignment(
                acquisitions,
                target_time,
                window_days=kwargs.get("window_days", 7.0),
                decay_alpha=kwargs.get("decay_alpha"),
                forbid_future=kwargs.get("forbid_future", True),
            )
        )
    elif "temporal_alignment" not in skip:
        missing = [k for k, v in (("acquisitions", acquisitions),
                                  ("target_time", target_time)) if not v]
        report.results.append(GateResult(
            gate="temporal_alignment", verdict=Verdict.NOT_EVALUATED,
            decisions=[f"no {' or '.join(missing)} supplied; compositing window, "
                       "asynchrony and temporal leakage were not checked"],
            evidence={"missing_inputs": missing},
        ))
    if "quality_missingness" not in skip:
        report.results.append(
            check_quality_missingness(
                layers,
                max_invalid_fraction=kwargs.get("max_invalid_fraction", 0.30),
                gap_fill=kwargs.get("gap_fill"),
                max_implausible_fraction=kwargs.get("max_implausible_fraction", 0.01),
            )
        )
    if "sensor_physics" not in skip:
        report.results.append(
            check_sensor_physics(
                layers,
                band_expressions=kwargs.get("band_expressions", ()),
                require_terrain_correction=kwargs.get("require_terrain_correction", True),
            )
        )
    return report
