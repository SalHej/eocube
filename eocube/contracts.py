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

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        lines = [str(r) for r in self.results]
        lines.append(
            f"\nCUBE VERDICT: {'ACCEPTED' if self.ok else 'REFUSED'} "
            f"({len(self.refusals)} refusing gate(s))"
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
    """
    res = GateResult(gate="geometry_crs", verdict=Verdict.PASS)
    if not layers:
        res.verdict = Verdict.REFUSE
        res.reasons.append("no layers supplied")
        return res

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
    """
    res = GateResult(gate="resolution", verdict=Verdict.PASS)
    gsds = {l["name"]: float(l["gsd"]) for l in layers}
    res.evidence["gsd_by_layer"] = gsds
    if not gsds:
        res.verdict = Verdict.REFUSE
        res.reasons.append("no layers supplied")
        return res

    target = float(target_gsd) if target_gsd is not None else max(gsds.values())
    res.evidence["target_gsd"] = target
    if target_gsd is None:
        res.verdict = Verdict.PASS_WITH_DECISION
        res.decisions.append(
            f"no target GSD supplied; defaulted to the coarsest input ({target} m) "
            "so that no layer is upsampled"
        )

    for name, g in gsds.items():
        if g > target:
            if not allow_upsampling:
                res.verdict = Verdict.REFUSE
                res.reasons.append(
                    f"layer '{name}' at {g} m would be upsampled to {target} m; "
                    "set allow_upsampling=True and justify it, or coarsen the target"
                )
            else:
                if res.verdict is Verdict.PASS:
                    res.verdict = Verdict.PASS_WITH_DECISION
                res.decisions.append(
                    f"layer '{name}' upsampled {g} m -> {target} m using "
                    f"{resampling}; effective resolution remains {g} m"
                )
        elif g < target:
            if res.verdict is Verdict.PASS:
                res.verdict = Verdict.PASS_WITH_DECISION
            res.decisions.append(
                f"layer '{name}' downsampled {g} m -> {target} m using {resampling}"
            )
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

    res.evidence["per_layer"] = per_layer
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
    """
    res = GateResult(gate="sensor_physics", verdict=Verdict.PASS)
    by_name = {l["name"]: l for l in layers}
    res.evidence["modalities"] = {n: l.get("modality") for n, l in by_name.items()}

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
    """
    report = ContractReport()

    if "geometry_crs" not in skip:
        report.results.append(
            check_geometry_crs(
                layers,
                require_identical_crs=kwargs.get("require_identical_crs", True),
                max_grid_offset_px=kwargs.get("max_grid_offset_px", 0.1),
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
    if "quality_missingness" not in skip:
        report.results.append(
            check_quality_missingness(
                layers,
                max_invalid_fraction=kwargs.get("max_invalid_fraction", 0.30),
                gap_fill=kwargs.get("gap_fill"),
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
