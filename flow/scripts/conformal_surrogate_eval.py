#!/usr/bin/env python3
import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Point:
    run_id: str
    pred: float
    actual: float
    features: Dict[str, Any]


@dataclass(frozen=True)
class Run:
    run_id: str
    points: List[Point]


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _ceil_quantile(scores: Sequence[float], alpha: float) -> float:
    if not scores:
        raise ValueError("Empty scores")
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0,1), got {alpha}")
    n = len(scores)
    k = math.ceil((n + 1) * (1.0 - alpha))
    k = max(1, min(k, n))
    return sorted(scores)[k - 1]


def _find_result_files(results_root: Path) -> List[Path]:
    return sorted(results_root.glob("**/surrogate_autotune.json"))


def _infer_sdc_units_ps(calib: Dict[str, Any]) -> float:
    direct = _as_float(calib.get("sdc_units_ps"))
    if direct is not None and direct > 0:
        return direct

    base_ps = _as_float(calib.get("baseline_ecp_ps"))
    base_sdc = _as_float(calib.get("baseline_ecp_sdc_units"))
    if base_ps is not None and base_sdc is not None and base_ps > 0 and base_sdc > 0:
        return base_ps / base_sdc

    return 1000.0


def _extract_runs(
    *,
    results_root: Path,
    objective: str,
) -> List[Run]:
    runs: List[Run] = []

    for path in _find_result_files(results_root):
        try:
            data = _load_json(path)
        except Exception:
            continue

        tuning = data.get("tuning") or {}
        if tuning.get("objective") != objective:
            continue

        calib = data.get("calibration") or {}
        sdc_units_ps = _infer_sdc_units_ps(calib)

        validation = data.get("validation") or {}
        candidates = validation.get("candidates") or []
        if not isinstance(candidates, list):
            continue

        points: List[Point] = []
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            if cand.get("error"):
                continue

            pred = _as_float(cand.get("predicted_objective"))
            if pred is None:
                continue

            actual: Optional[float]
            if objective == "effective_clock_period":
                ecp_ps = _as_float(cand.get("ecp_ps"))
                if ecp_ps is None:
                    continue
                pred = pred * sdc_units_ps
                actual = ecp_ps
            elif objective == "routed_wirelength":
                actual = _as_float(cand.get("routed_wl"))
            elif objective == "power":
                actual = _as_float(cand.get("total_power"))
            elif objective == "instance_area":
                actual = _as_float(cand.get("instance_area"))
            else:
                raise ValueError(f"Unsupported objective: {objective}")

            if actual is None:
                continue

            features = cand.get("predicted_features") or {}
            if not isinstance(features, dict):
                features = {}

            points.append(Point(run_id=str(path), pred=pred, actual=actual, features=features))

        if points:
            runs.append(Run(run_id=str(path), points=points))

    return runs


def _sigma_fn(name: str, fail_risk_c0: float) -> Callable[[Dict[str, Any]], float]:
    eps = 1e-9

    if name == "constant":
        return lambda _f: 1.0

    if name == "fail_risk":
        def sigma(features: Dict[str, Any]) -> float:
            fr = _as_float(features.get("surrogate_fail_risk")) or 0.0
            return max(eps, fr + fail_risk_c0)

        return sigma

    if name == "1+surrogate_power":
        def sigma(features: Dict[str, Any]) -> float:
            sp = _as_float(features.get("surrogate_power")) or 0.0
            return max(eps, 1.0 + sp)

        return sigma

    if name == "log1p_hpwl_est":
        def sigma(features: Dict[str, Any]) -> float:
            hpwl = _as_float(features.get("surrogate_hpwl_est")) or 0.0
            return max(eps, math.log1p(max(0.0, hpwl)))

        return sigma

    raise ValueError(f"Unknown sigma scheme: {name}")


def _iter_points(runs: Iterable[Run]) -> Iterable[Point]:
    for run in runs:
        yield from run.points


def _compute_conformal_q(
    *,
    calibration_runs: Sequence[Run],
    alpha: float,
    sigma: Callable[[Dict[str, Any]], float],
) -> float:
    scores: List[float] = []
    for p in _iter_points(calibration_runs):
        s = sigma(p.features)
        scores.append(abs(p.actual - p.pred) / s)
    return _ceil_quantile(scores, alpha)


def _coverage_and_width(
    *,
    test_runs: Sequence[Run],
    alpha: float,
    sigma: Callable[[Dict[str, Any]], float],
) -> Tuple[float, float, float]:
    covers: List[float] = []
    widths: List[float] = []

    for i,run in enumerate(test_runs):
        calib = [r for j,r in enumerate(test_runs) if j != i]
        if not calib:
            continue
        q = _compute_conformal_q(calibration_runs=calib, alpha=alpha, sigma=sigma)
        for p in run.points:
            s = sigma(p.features)
            lo = p.pred - q * s
            hi = p.pred + q * s
            covers.append(1.0 if (p.actual >= lo and p.actual <= hi) else 0.0)
            widths.append(2.0 * q * s)

    if not covers:
        raise RuntimeError("No test points for coverage evaluation")

    widths_sorted = sorted(widths)
    mid = widths_sorted[len(widths_sorted) // 2]
    p90 = widths_sorted[math.floor(0.9 * (len(widths_sorted) - 1))]
    return (sum(covers) / len(covers), mid, p90)


def _selection_regret_delta(
    *,
    runs: Sequence[Run],
    alpha: float,
    sigma: Callable[[Dict[str, Any]], float],
) -> Tuple[int, int, int, float, float]:
    wins = 0
    losses = 0
    same = 0
    deltas: List[float] = []

    for i,run in enumerate(runs):
        if len(run.points) < 2:
            continue
        calib = [r for j,r in enumerate(runs) if j != i]
        if not calib:
            continue
        q = _compute_conformal_q(calibration_runs=calib, alpha=alpha, sigma=sigma)

        base = min(run.points, key=lambda p: p.pred)
        conf = min(run.points, key=lambda p: p.pred + q * sigma(p.features))

        best = min(p.actual for p in run.points)
        reg_base = base.actual - best
        reg_conf = conf.actual - best
        delta = reg_base - reg_conf
        deltas.append(delta)

        if delta > 0:
            wins += 1
        elif delta < 0:
            losses += 1
        else:
            same += 1

    avg = sum(deltas) / len(deltas) if deltas else 0.0
    med = sorted(deltas)[len(deltas) // 2] if deltas else 0.0
    return wins, losses, same, avg, med


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate conformal prediction on ORFS surrogate autotuner logs.")
    ap.add_argument("--results-root", default="flow/results", help="Root directory containing flow/results/**")
    ap.add_argument(
        "--objective",
        default="effective_clock_period",
        choices=["effective_clock_period", "routed_wirelength", "power", "instance_area"],
        help="Objective to evaluate (matches surrogate objective names).",
    )
    ap.add_argument("--alpha", type=float, default=0.1, help="Miscoverage level (e.g., 0.1 for 90%% intervals).")
    ap.add_argument(
        "--sigma",
        default="constant",
        choices=["constant", "fail_risk", "1+surrogate_power", "log1p_hpwl_est"],
        help="Scaling for normalized conformal intervals.",
    )
    ap.add_argument(
        "--fail-risk-c0",
        type=float,
        default=0.001,
        help="Additive constant for sigma=fail_risk (avoids zero widths).",
    )
    args = ap.parse_args(list(argv) if argv is not None else None)

    results_root = Path(args.results_root)
    runs = _extract_runs(results_root=results_root, objective=args.objective)
    if not runs:
        raise RuntimeError(f"No runs found for objective={args.objective} under {results_root}")

    sigma = _sigma_fn(args.sigma, args.fail_risk_c0)
    coverage, width_med, width_p90 = _coverage_and_width(test_runs=runs, alpha=args.alpha, sigma=sigma)
    wins, losses, same, avg_delta, med_delta = _selection_regret_delta(
        runs=runs,
        alpha=args.alpha,
        sigma=sigma,
    )

    n_runs = len(runs)
    n_points = sum(len(r.points) for r in runs)
    print(f"objective={args.objective} alpha={args.alpha} sigma={args.sigma}")
    print(f"runs={n_runs} points={n_points}")
    print(f"coverage={coverage:.4f} target={1.0-args.alpha:.4f}")
    print(f"width_median={width_med:.6g} width_p90={width_p90:.6g}")
    print(f"selection_regret_delta: wins={wins} losses={losses} same={same} avg={avg_delta:.6g} med={med_delta:.6g}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

