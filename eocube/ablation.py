"""
eocube.ablation
===============

Harness for measuring what each contract gate is worth.

The claim that data-cube contracts improve downstream outcomes is a hypothesis,
not a result. This module is the apparatus for testing it: disable one gate at a
time, rebuild the cube, train the same downstream model, and record what changes.

Three design decisions are deliberate, because each addresses the criticism a
reviewer makes first.

**Repeated seeds.** Every configuration runs several times. A single run cannot
separate a gate's effect from training variance, and a result reported without
that separation is not interpretable.

**Both error directions.** A gate can refuse a cube that would have trained
perfectly well. That is a cost, not a success, and over-rigidity is as much a
finding as a catch. The harness records refusals separately from metric changes
so that false rejection rate is visible.

**One variable at a time.** The model, the data split and the training budget are
held fixed; only the gate set varies. If the architecture changes between runs the
comparison means nothing.

Interface
---------
You supply two callables:

``build_cube(skip) -> (layers, gate_kwargs)``
    Assemble the cube with the named gates disabled. Returning different layers
    for different ``skip`` sets is the point: without the resolution gate you might
    upsample; without the quality gate you might retain contaminated pixels.

``train_eval(layers, seed) -> float``
    Train the downstream model on the cube and return a single scalar metric.
    Higher must mean better; negate RMSE if necessary.

Example
-------
>>> result = run_ablation(build_cube, train_eval, seeds=(0, 1, 2))
>>> print(result.summary())
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .contracts import GATES, ContractReport, run_all_gates

__all__ = ["RunRecord", "ArmResult", "AblationResult", "run_ablation"]

#: Label used for the arm in which every gate is active.
BASELINE = "<all gates active>"


@dataclass
class RunRecord:
    """One training run under one gate configuration."""

    arm: str
    seed: int
    accepted: bool
    metric: float | None
    n_decisions: int
    refusing_gates: list[str] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None


@dataclass
class ArmResult:
    """All runs for a single gate configuration."""

    arm: str
    gate_removed: str | None
    runs: list[RunRecord] = field(default_factory=list)

    @property
    def metrics(self) -> list[float]:
        return [r.metric for r in self.runs if r.metric is not None]

    @property
    def mean(self) -> float | None:
        m = self.metrics
        return statistics.fmean(m) if m else None

    @property
    def stdev(self) -> float | None:
        m = self.metrics
        return statistics.stdev(m) if len(m) > 1 else 0.0 if m else None

    @property
    def n_refused(self) -> int:
        return sum(1 for r in self.runs if not r.accepted)

    @property
    def refusal_rate(self) -> float:
        return self.n_refused / len(self.runs) if self.runs else 0.0

    @property
    def mean_decisions(self) -> float:
        return statistics.fmean([r.n_decisions for r in self.runs]) if self.runs else 0.0


@dataclass
class AblationResult:
    arms: dict[str, ArmResult] = field(default_factory=dict)

    @property
    def baseline(self) -> ArmResult | None:
        return self.arms.get(BASELINE)

    def effect_of(self, gate: str) -> float | None:
        """Change in the mean metric caused by *removing* ``gate``.

        Negative means removing the gate hurt — i.e. the gate was doing useful
        work. Positive means the cube scored better without it, which usually
        indicates the gate was over-rigid or the metric is being inflated by
        something the gate was suppressing. Both are results worth reporting.
        """
        base = self.baseline
        arm = self.arms.get(gate)
        if base is None or arm is None or base.mean is None or arm.mean is None:
            return None
        return arm.mean - base.mean

    def to_rows(self) -> list[dict[str, Any]]:
        base = self.baseline
        rows = []
        for name, arm in self.arms.items():
            rows.append(
                {
                    "arm": name,
                    "gate_removed": arm.gate_removed,
                    "n_runs": len(arm.runs),
                    "mean_metric": arm.mean,
                    "stdev": arm.stdev,
                    "refusal_rate": arm.refusal_rate,
                    "mean_decisions": arm.mean_decisions,
                    "delta_vs_baseline": (
                        None
                        if base is None or arm.mean is None or base.mean is None
                        else arm.mean - base.mean
                    ),
                }
            )
        return rows

    def summary(self) -> str:
        rows = self.to_rows()
        w = max(len(r["arm"]) for r in rows) + 2
        out = [
            f"{'configuration'.ljust(w)}{'n':>3}  {'mean':>8}  {'sd':>7}  "
            f"{'delta':>8}  {'refused':>8}  {'decisions':>10}",
            "-" * (w + 50),
        ]
        for r in sorted(rows, key=lambda x: (x["gate_removed"] is not None, x["arm"])):
            mean = "n/a" if r["mean_metric"] is None else f"{r['mean_metric']:.4f}"
            sd = "n/a" if r["stdev"] is None else f"{r['stdev']:.4f}"
            dl = "—" if r["delta_vs_baseline"] is None else f"{r['delta_vs_baseline']:+.4f}"
            out.append(
                f"{r['arm'].ljust(w)}{r['n_runs']:>3}  {mean:>8}  {sd:>7}  "
                f"{dl:>8}  {r['refusal_rate']:>7.0%}  {r['mean_decisions']:>10.1f}"
            )
        out.append("")
        out.append(
            "delta is the change caused by removing that gate. Negative means the "
            "gate was doing useful work."
        )
        return "\n".join(out)


def run_ablation(
    build_cube: Callable[[tuple[str, ...]], tuple[Sequence[dict[str, Any]], dict[str, Any]]],
    train_eval: Callable[[Sequence[dict[str, Any]], int], float],
    *,
    gates: Sequence[str] = GATES,
    seeds: Sequence[int] = (0, 1, 2),
    include_baseline: bool = True,
    train_on_refused: bool = False,
    verbose: bool = True,
) -> AblationResult:
    """Run the full leave-one-gate-out ablation.

    Parameters
    ----------
    build_cube
        ``build_cube(skip) -> (layers, gate_kwargs)``. ``skip`` is the tuple of
        gate names disabled for this arm.
    train_eval
        ``train_eval(layers, seed) -> float``. Higher is better.
    gates
        Gates to ablate. Defaults to all five.
    seeds
        Repeats per arm. Three is the practical minimum; five is better.
    include_baseline
        Run the all-gates-active arm. Required for ``delta`` to be meaningful.
    train_on_refused
        If True, train even when the contract report refuses the cube. This is
        how you measure what a gate was protecting against: the difference
        between a refused-but-trained cube and the baseline is the cost of
        ignoring that gate. If False, a refusal is recorded and no training runs.

    Returns
    -------
    AblationResult
    """
    result = AblationResult()
    arms: list[tuple[str, tuple[str, ...], str | None]] = []
    if include_baseline:
        arms.append((BASELINE, (), None))
    for g in gates:
        arms.append((f"without {g}", (g,), g))

    for arm_name, skip, removed in arms:
        arm = ArmResult(arm=arm_name, gate_removed=removed)
        for seed in seeds:
            t0 = time.perf_counter()
            rec = RunRecord(
                arm=arm_name, seed=seed, accepted=False, metric=None, n_decisions=0
            )
            try:
                layers, gate_kwargs = build_cube(skip)
                report: ContractReport = run_all_gates(layers, skip=skip, **gate_kwargs)
                rec.accepted = report.ok
                rec.n_decisions = len(report.provenance())
                rec.refusing_gates = [r.gate for r in report.refusals]
                if report.ok or train_on_refused:
                    rec.metric = float(train_eval(layers, seed))
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                rec.error = f"{type(exc).__name__}: {exc}"
            rec.seconds = round(time.perf_counter() - t0, 3)
            arm.runs.append(rec)
            if verbose:
                status = "ok " if rec.accepted else "REF"
                val = "—" if rec.metric is None else f"{rec.metric:.4f}"
                err = f"  !! {rec.error}" if rec.error else ""
                print(f"  [{status}] {arm_name:<32} seed={seed}  metric={val}{err}")
        result.arms[arm_name] = arm
    return result
