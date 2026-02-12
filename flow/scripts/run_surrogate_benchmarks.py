#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_PLATFORMS = ("asap7", "nangate45", "sky130hd")
DEFAULT_DESIGNS = ("aes", "ibex", "jpeg")


@dataclass(frozen=True)
class SuiteRun:
    flow_variant: str
    objective: str


DEFAULT_SUITE: Tuple[SuiteRun, ...] = (
    SuiteRun(flow_variant="matrix_20260104_ecp", objective="effective_clock_period"),
    SuiteRun(flow_variant="matrix_20260104_wl", objective="routed_wirelength"),
)


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


def _parse_baseline_objective(*, objective: str, ws_file: Path, wl_file: Path) -> Optional[float]:
    if objective == "effective_clock_period":
        try:
            data = _load_json(ws_file)
        except Exception:
            return None
        fmax = _as_float(data.get("finish__timing__fmax"))
        if fmax is not None and fmax > 0:
            return 1e12 / fmax
        clk = _as_float(data.get("constraint__clock__period"))
        ws = _as_float(data.get("finish__timing__setup__ws"))
        if clk is not None and ws is not None:
            return clk - ws
        return None

    if objective == "routed_wirelength":
        try:
            data = _load_json(wl_file)
        except Exception:
            return None
        wl = _as_float(data.get("detailedroute__route__wirelength"))
        if wl is None:
            wl = _as_float(data.get("globalroute__route__wirelength__estimated"))
        return wl

    return None


def _best_objective_from_summary(*, summary: Dict[str, Any], objective: str) -> Optional[float]:
    validation = summary.get("validation") if isinstance(summary, dict) else None
    if not isinstance(validation, dict):
        return None
    best = validation.get("best")
    if not isinstance(best, dict):
        return None
    obj = _as_float(best.get("objective"))
    if obj is not None:
        return obj
    # Backward-compat with older summaries.
    if objective == "effective_clock_period":
        return _as_float(best.get("ecp_ps"))
    if objective == "routed_wirelength":
        return _as_float(best.get("routed_wl"))
    return None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _split_csv(s: str) -> Tuple[str, ...]:
    items = [x.strip() for x in s.split(",")]
    return tuple(x for x in items if x)


def _ensure_exists(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")


def _run_one(
    *,
    repo_root: Path,
    flow_dir: Path,
    platform: str,
    design: str,
    run: SuiteRun,
    conformal_sigma: str,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    design_config = Path("designs") / platform / design / "config.mk"
    _ensure_exists(flow_dir / design_config, "design config")

    results_dir = flow_dir / "results" / platform / design / run.flow_variant
    summary_path = results_dir / "surrogate_autotune.json"

    backup_path: Optional[Path] = None
    if summary_path.exists():
        backup_path = summary_path.with_name(f"surrogate_autotune.prev.{_timestamp()}.json")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(summary_path, backup_path)

    env = os.environ.copy()
    env["DESIGN_CONFIG"] = str(design_config)
    env["FLOW_VARIANT"] = run.flow_variant
    env["SURROGATE_OBJECTIVE"] = run.objective
    env["SURROGATE_RESUME"] = "1" if args.resume else "0"
    env["NUM_CORES"] = str(args.num_cores)

    # Preserve the matrix baseline run settings if we need to (re)run tuning.
    env["SURROGATE_SAMPLES"] = str(args.samples)
    env["SURROGATE_TOP_N"] = str(args.top_n)
    env["SURROGATE_GLOBAL_TOP_N"] = str(args.global_top_n)
    env["SURROGATE_MULTI_FIDELITY"] = "1" if args.multi_fidelity else "0"
    env["SURROGATE_SHRINK"] = str(args.shrink)
    env["SURROGATE_PORTFOLIO"] = "1" if args.surrogate_portfolio else "0"
    env["SURROGATE_PORTFOLIO_SHRINK"] = str(args.surrogate_portfolio_shrink)
    env["SURROGATE_TIME_BUDGET_S"] = str(args.time_budget_s)

    # Validation / selection knobs.
    env["SURROGATE_VALIDATE"] = "1" if args.validate else "0"
    env["SURROGATE_VALIDATE_N"] = str(args.validate_n)
    env["SURROGATE_VALIDATE_JOBS"] = str(args.validate_jobs)
    env["SURROGATE_VALIDATE_SELECT"] = args.validate_select
    env["SURROGATE_VALIDATE_PORTFOLIO_MEAN_FRAC"] = str(args.portfolio_mean_frac)
    env["SURROGATE_VALIDATE_PORTFOLIO_UCB_FRAC"] = str(args.portfolio_ucb_frac)
    env["SURROGATE_VALIDATE_PORTFOLIO_LCB_FRAC"] = str(args.portfolio_lcb_frac)

    env["SURROGATE_ROUTE_VALIDATE"] = "1" if args.route_prefilter else "0"
    env["SURROGATE_ROUTE_VALIDATE_STAGE"] = args.route_prefilter_stage
    env["SURROGATE_ROUTE_VALIDATE_N"] = str(args.route_prefilter_n)
    env["SURROGATE_ROUTE_VALIDATE_JOBS"] = str(args.route_prefilter_jobs)

    env["SURROGATE_CONFORMAL_ALPHA"] = str(args.conformal_alpha)
    env["SURROGATE_CONFORMAL_SIGMA"] = conformal_sigma
    env["SURROGATE_CONFORMAL_FAIL_RISK_C0"] = str(args.conformal_fail_risk_c0)

    cmd = ["make", "-C", str(flow_dir), "surrogate_autotune"]
    if args.dry_run:
        print(
            f"[dry-run] {platform}/{design} {run.flow_variant} objective={run.objective} cmd={' '.join(cmd)}",
            flush=True,
        )
        return {
            "platform": platform,
            "design": design,
            "flow_variant": run.flow_variant,
            "objective": run.objective,
            "status": "dry_run",
            "summary": None,
            "backup": str(backup_path) if backup_path is not None else None,
        }

    print(f"== {platform}/{design} {run.flow_variant} ({run.objective}) ==", flush=True)
    error: Optional[str] = None
    try:
        subprocess.run(cmd, cwd=str(repo_root), env=env, check=True)
    except subprocess.CalledProcessError as e:
        error = str(e)

    summary = _load_json(summary_path) if summary_path.exists() else None

    ws_file = Path(str((summary or {}).get("calibration", {}).get("ws_file", "")))
    wl_file = Path(str((summary or {}).get("calibration", {}).get("wl_file", "")))
    baseline = _parse_baseline_objective(objective=run.objective, ws_file=ws_file, wl_file=wl_file)
    best = _best_objective_from_summary(summary=summary or {}, objective=run.objective)

    out: Dict[str, Any] = {
        "platform": platform,
        "design": design,
        "flow_variant": run.flow_variant,
        "objective": run.objective,
        "status": "error" if error is not None else "ok",
        "error": error,
        "summary": str(summary_path),
        "backup": str(backup_path) if backup_path is not None else None,
        "baseline": baseline,
        "best": best,
        "improve_pct": ((baseline - best) / baseline * 100.0) if (baseline is not None and best is not None and baseline > 0) else None,
    }
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Rerun ORFS surrogate_autotune benchmarks with a consistent setup.")
    ap.add_argument(
        "--platforms",
        default=",".join(DEFAULT_PLATFORMS),
        help="Comma-separated platforms (default: asap7,nangate45,sky130hd).",
    )
    ap.add_argument(
        "--designs",
        default=",".join(DEFAULT_DESIGNS),
        help="Comma-separated designs (default: aes,ibex,jpeg).",
    )
    ap.add_argument(
        "--suite",
        default="matrix_20260104",
        choices=["matrix_20260104"],
        help="Benchmark suite (default: matrix_20260104 => {ecp,wl}).",
    )
    ap.add_argument(
        "--objectives",
        default="ecp,wl",
        help="Comma-separated subset to run: ecp,wl (default: ecp,wl).",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print commands without running.")
    ap.add_argument(
        "--keep-going",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Continue running the suite even if one benchmark fails.",
    )
    ap.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse existing per-clock tuning outputs.",
    )

    ap.add_argument("--num-cores", type=int, default=8, help="OpenROAD threads per job (sets NUM_CORES).")
    ap.add_argument("--samples", type=int, default=1_000_000_000, help="SURROGATE_SAMPLES (for tuning stage).")
    ap.add_argument("--top-n", type=int, default=560, help="SURROGATE_TOP_N (for tuning stage).")
    ap.add_argument("--global-top-n", type=int, default=560, help="SURROGATE_GLOBAL_TOP_N (candidate pool cap).")
    ap.add_argument("--time-budget-s", type=int, default=600, help="SURROGATE_TIME_BUDGET_S (for tuning stage).")
    ap.add_argument(
        "--multi-fidelity",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable surrogate multi-fidelity.",
    )
    ap.add_argument("--shrink", type=float, default=0.15, help="SURROGATE_SHRINK (multi-fidelity).")
    ap.add_argument(
        "--surrogate-portfolio",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable surrogate -portfolio.",
    )
    ap.add_argument("--surrogate-portfolio-shrink", type=float, default=0.25, help="SURROGATE_PORTFOLIO_SHRINK.")

    ap.add_argument(
        "--validate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run full-ORFS validation.",
    )
    ap.add_argument("--validate-n", type=int, default=14, help="Final K finish runs per design/objective.")
    ap.add_argument("--validate-jobs", type=int, default=10, help="Parallel finish jobs.")
    ap.add_argument(
        "--validate-select",
        default="conformal_portfolio",
        choices=["mean", "ucb", "lcb", "conformal_portfolio"],
        help="Selection mode for validation candidates.",
    )
    ap.add_argument("--portfolio-mean-frac", type=float, default=0.6, help="Portfolio mean fraction.")
    ap.add_argument("--portfolio-ucb-frac", type=float, default=0.2, help="Portfolio UCB fraction.")
    ap.add_argument("--portfolio-lcb-frac", type=float, default=0.2, help="Portfolio LCB fraction.")

    ap.add_argument(
        "--route-prefilter",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable route/grt prefilter stage.",
    )
    ap.add_argument("--route-prefilter-stage", default="grt", choices=["grt", "route"], help="Prefilter stage.")
    ap.add_argument("--route-prefilter-n", type=int, default=28, help="Prefilter pool size (M).")
    ap.add_argument("--route-prefilter-jobs", type=int, default=10, help="Parallel prefilter jobs.")

    ap.add_argument("--conformal-alpha", type=float, default=0.1, help="Conformal alpha.")
    ap.add_argument("--conformal-sigma", default="", help="Conformal sigma scheme (empty => per-objective default).")
    ap.add_argument("--conformal-fail-risk-c0", type=float, default=0.001, help="sigma=fail_risk constant.")

    args = ap.parse_args(list(argv) if argv is not None else None)

    platforms = _split_csv(args.platforms)
    designs = _split_csv(args.designs)
    user_sigma = args.conformal_sigma.strip()

    want = set(_split_csv(args.objectives))
    suite: List[SuiteRun] = []
    for r in DEFAULT_SUITE:
        tag = "ecp" if r.objective == "effective_clock_period" else "wl"
        if tag in want:
            suite.append(r)

    repo_root = Path(__file__).resolve().parents[2]
    flow_dir = repo_root / "flow"
    _ensure_exists(flow_dir / "Makefile", "flow/Makefile")

    results: List[Dict[str, Any]] = []
    for platform in platforms:
        for design in designs:
            for run in suite:
                # Use per-objective defaults unless explicitly overridden.
                sigma = user_sigma or ("fail_risk" if run.objective == "effective_clock_period" else "constant")

                try:
                    results.append(
                        _run_one(
                            repo_root=repo_root,
                            flow_dir=flow_dir,
                            platform=platform,
                            design=design,
                            run=run,
                            conformal_sigma=sigma,
                            args=args,
                        )
                    )
                except Exception as e:
                    if not args.keep_going:
                        raise
                    results.append(
                        {
                            "platform": platform,
                            "design": design,
                            "flow_variant": run.flow_variant,
                            "objective": run.objective,
                            "status": "error",
                            "error": f"{type(e).__name__}: {e}",
                            "summary": None,
                            "backup": None,
                            "baseline": None,
                            "best": None,
                            "improve_pct": None,
                        }
                    )

    # Summary table.
    print("\n== Summary ==", flush=True)
    for r in results:
        status = r.get("status")
        err = r.get("error")
        plat = r.get("platform")
        des = r.get("design")
        var = r.get("flow_variant")
        obj = r.get("objective")
        imp = r.get("improve_pct")
        if imp is None:
            imp_s = "n/a"
        else:
            imp_s = f"{imp:+.3f}%"
        line = f"{plat}/{des} {var} {obj} improve={imp_s} status={status}"
        if status != "ok" and err:
            line += f" error={err}"
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
