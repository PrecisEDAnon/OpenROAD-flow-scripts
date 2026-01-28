#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _split_csv(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _discover_designs(flow_dir: Path, platforms: Sequence[str]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    designs_root = flow_dir / "designs"
    for platform in platforms:
        plat_dir = designs_root / platform
        if not plat_dir.is_dir():
            continue
        for design_dir in sorted(plat_dir.iterdir(), key=lambda p: p.name):
            if not design_dir.is_dir():
                continue
            if (design_dir / "config.mk").exists():
                out.append((platform, design_dir.name))
    return out


def _kill_process_group(proc: subprocess.Popen[bytes], *, timeout_s: float = 10.0) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.2)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _run_checked(
    *,
    cmd: Sequence[str],
    cwd: Path,
    env: Dict[str, str],
    log_path: Path,
    timeout_s: Optional[float],
) -> Tuple[Optional[str], float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with log_path.open("ab") as log:
        log.write(f"== CMD {' '.join(cmd)} ==\n".encode())
        log.flush()
        proc = subprocess.Popen(
            list(cmd),
            cwd=str(cwd),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            elapsed = time.monotonic() - start
            return f"timeout_after_{timeout_s:.0f}s", elapsed

        rc = proc.returncode
        elapsed = time.monotonic() - start
        if rc != 0:
            return f"exit_{rc}", elapsed
        return None, elapsed


@dataclass(frozen=True)
class ObjectiveRun:
    objective: str
    tag: str
    validate_n: int


CSV_FIELDS: Tuple[str, ...] = (
    "run_id",
    "platform",
    "design",
    "objective",
    "flow_variant",
    "status",
    "error",
    "baseline_time_s",
    "wall_time_s",
    "summary_json",
    "clock_sweep_n",
    "clock_sweep_ps",
    "samples_per_clock",
    "top_n_per_clock",
    "global_top_n",
    "validate_select",
    "validate_n",
    "validate_make_target",
    "route_prefilter",
    "route_prefilter_stage",
    "route_n",
    "conformal_alpha",
    "conformal_sigma",
    "conformal_q_native",
    "baseline_objective",
    "best_objective",
    "improve_pct",
    "best_variant",
    "validated_candidates",
    "validated_ok",
)


def _objective_plan(args: argparse.Namespace) -> List[ObjectiveRun]:
    want = set(_split_csv(args.objectives))
    out: List[ObjectiveRun] = []

    if "ecp" in want:
        out.append(ObjectiveRun(objective="effective_clock_period", tag="ecp", validate_n=args.validate_n))
    if "wl" in want:
        out.append(ObjectiveRun(objective="routed_wirelength", tag="wl", validate_n=args.validate_n))
    if "power" in want:
        out.append(ObjectiveRun(objective="power", tag="power", validate_n=args.validate_n_power))
    return out


def _count_ok(items: Iterable[Dict[str, Any]], key: str) -> int:
    n = 0
    for it in items:
        if it.get("error"):
            continue
        if _as_float(it.get(key)) is None:
            continue
        n += 1
    return n


def _extract_summary_row(
    *,
    run_id: str,
    platform: str,
    design: str,
    flow_variant: str,
    objective: str,
    status: str,
    error: Optional[str],
    wall_time_s: float,
    baseline_time_s: Optional[float],
    summary_path: Optional[Path],
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "run_id": run_id,
        "platform": platform,
        "design": design,
        "objective": objective,
        "flow_variant": flow_variant,
        "status": status,
        "error": error,
        "baseline_time_s": baseline_time_s,
        "wall_time_s": wall_time_s,
        "summary_json": str(summary_path) if summary_path is not None else None,
    }

    if summary_path is None or not summary_path.exists():
        return row

    try:
        summary = _load_json(summary_path)
    except Exception as e:
        row["status"] = "error"
        row["error"] = f"parse_summary_failed: {type(e).__name__}: {e}"
        return row

    tuning = summary.get("tuning") if isinstance(summary, dict) else None
    sel = summary.get("selection") if isinstance(summary, dict) else None
    val = summary.get("validation") if isinstance(summary, dict) else None

    row["clock_sweep_n"] = len(summary.get("clock_sweep_ps") or [])
    row["clock_sweep_ps"] = " ".join(str(x) for x in (summary.get("clock_sweep_ps") or []))

    if isinstance(tuning, dict):
        row["samples_per_clock"] = tuning.get("samples_per_clock")
        row["top_n_per_clock"] = tuning.get("top_n_per_clock")
        row["global_top_n"] = tuning.get("global_top_n")

    if isinstance(sel, dict):
        row["validate_select"] = sel.get("validate_select")
        row["validate_n"] = sel.get("validate_n")
        row["validate_make_target"] = sel.get("validate_make_target")
        row["route_prefilter"] = sel.get("route_prefilter")
        row["route_prefilter_stage"] = sel.get("route_prefilter_stage")
        row["route_n"] = sel.get("route_n")
        row["conformal_alpha"] = sel.get("conformal_alpha")
        row["conformal_sigma"] = sel.get("conformal_sigma")
        row["conformal_q_native"] = sel.get("conformal_q_native")

    baseline_obj = None
    best_obj = None
    improve_pct = None
    best_variant = None
    if isinstance(val, dict):
        baseline_obj = _as_float(val.get("baseline_objective"))
        improve_pct = _as_float(val.get("improve_pct"))
        best = val.get("best")
        if isinstance(best, dict):
            best_obj = _as_float(best.get("objective"))
            best_variant = best.get("variant")

        cands = val.get("candidates")
        if isinstance(cands, list):
            row["validated_candidates"] = len(cands)
            row["validated_ok"] = _count_ok(cands, "objective")

    if improve_pct is None and baseline_obj is not None and best_obj is not None and baseline_obj != 0:
        improve_pct = (baseline_obj - best_obj) / baseline_obj * 100.0

    row["baseline_objective"] = baseline_obj
    row["best_objective"] = best_obj
    row["improve_pct"] = improve_pct
    row["best_variant"] = best_variant
    return row


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run a parallel surrogate_autotune sweep and emit a CSV summary in the repo root."
    )
    ap.add_argument(
        "--preset",
        default="throughput",
        choices=["throughput", "quality"],
        help="Preset for knob defaults and design set (default: throughput).",
    )
    ap.add_argument(
        "--platforms",
        default=None,
        help="Comma-separated platforms to include (default depends on --preset).",
    )
    ap.add_argument(
        "--designs",
        default=None,
        help="Optional comma-separated designs to include (default depends on --preset; empty => auto-discover all designs with config.mk under each platform).",
    )
    ap.add_argument(
        "--objectives",
        default=None,
        help="Comma-separated objective tags: ecp,wl,power (default depends on --preset).",
    )
    ap.add_argument("--jobs", type=int, default=None, help="Parallel designs in flight (default depends on --preset).")
    ap.add_argument(
        "--num-cores",
        type=int,
        default=None,
        help="Threads per ORFS job (sets NUM_CORES, default depends on --preset).",
    )

    ap.add_argument("--variant-prefix", default=f"bench_{_now_tag()}", help="FLOW_VARIANT prefix (default: bench_<ts>).")
    ap.add_argument("--out", default="", help="Output CSV path (default: surrogate_autotune_perf_<ts>.csv in repo root).")

    ap.add_argument("--time-budget-s", type=int, default=None, help="SURROGATE_TIME_BUDGET_S (default depends on --preset).")
    ap.add_argument("--samples", type=int, default=1_000_000_000, help="SURROGATE_SAMPLES (default: 1e9).")
    ap.add_argument("--top-n", type=int, default=None, help="SURROGATE_TOP_N (default depends on --preset).")
    ap.add_argument("--global-top-n", type=int, default=None, help="SURROGATE_GLOBAL_TOP_N (default depends on --preset).")
    ap.add_argument(
        "--clock-factors",
        default=None,
        help="SURROGATE_CLOCK_FACTORS (space-separated). Empty string disables sweep; unset uses ORFS default.",
    )

    ap.add_argument("--validate-n", type=int, default=None, help="SURROGATE_VALIDATE_N for ecp/wl (default depends on --preset).")
    ap.add_argument("--validate-n-power", type=int, default=None, help="SURROGATE_VALIDATE_N for power (default depends on --preset).")
    ap.add_argument("--validate-jobs", type=int, default=None, help="SURROGATE_VALIDATE_JOBS (default depends on --preset).")
    ap.add_argument(
        "--validate-select",
        default="conformal_portfolio",
        choices=["mean", "ucb", "lcb", "conformal_portfolio"],
        help="Validation selection mode (default: conformal_portfolio).",
    )
    ap.add_argument(
        "--portfolio-mean-frac",
        type=float,
        default=None,
        help="SURROGATE_VALIDATE_PORTFOLIO_MEAN_FRAC (default: ORFS default; quality preset uses 0.6).",
    )
    ap.add_argument(
        "--portfolio-ucb-frac",
        type=float,
        default=None,
        help="SURROGATE_VALIDATE_PORTFOLIO_UCB_FRAC (default: ORFS default; quality preset uses 0.2).",
    )
    ap.add_argument(
        "--portfolio-lcb-frac",
        type=float,
        default=None,
        help="SURROGATE_VALIDATE_PORTFOLIO_LCB_FRAC (default: ORFS default; quality preset uses 0.2).",
    )
    ap.add_argument("--conformal-alpha", type=float, default=0.1, help="SURROGATE_CONFORMAL_ALPHA (default: 0.1).")
    ap.add_argument("--conformal-fail-risk-c0", type=float, default=0.001, help="SURROGATE_CONFORMAL_FAIL_RISK_C0.")

    ap.add_argument(
        "--validate-make-target",
        default="report",
        help="Final validation target: 'report' (default, runs up to 6_report without GDS) or 'finish' (also generates GDS).",
    )
    ap.add_argument(
        "--baseline-make-target",
        default="report",
        help="Baseline target when missing: 'report' (default, runs up to 6_report without GDS) or 'finish'.",
    )

    ap.add_argument(
        "--route-prefilter",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable 2-stage validation (route/grt prefilter then report/finish) (default depends on --preset).",
    )
    ap.add_argument(
        "--route-prefilter-stage",
        choices=["grt", "route"],
        default=None,
        help="Prefilter stage when --route-prefilter is enabled (default depends on --preset).",
    )
    ap.add_argument(
        "--route-n",
        type=int,
        default=None,
        help="SURROGATE_ROUTE_VALIDATE_N (M prefilter candidates; default: ORFS default based on K).",
    )
    ap.add_argument(
        "--route-jobs",
        type=int,
        default=None,
        help="SURROGATE_ROUTE_VALIDATE_JOBS (default: ORFS default == validate jobs).",
    )

    ap.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True, help="Reuse existing outputs.")
    ap.add_argument("--timeout-baseline-s", type=int, default=14_400, help="Baseline timeout seconds (default: 4h).")
    ap.add_argument(
        "--timeout-surrogate-s",
        type=int,
        default=21_600,
        help="Per-objective surrogate_autotune timeout seconds (default: 6h).",
    )

    args = ap.parse_args(list(argv) if argv is not None else None)

    preset = (args.preset or "throughput").strip().lower()
    if preset not in {"throughput", "quality"}:
        print(f"ERROR: unsupported --preset={args.preset!r}", file=sys.stderr)
        return 2

    def set_default(name: str, value: Any) -> None:
        if getattr(args, name) is None:
            setattr(args, name, value)

    # Keep the existing "broad sweep throughput" behavior as the default.
    if preset == "throughput":
        set_default("platforms", "asap7,nangate45,sky130hd,gf180")
        set_default("designs", "")
        set_default("objectives", "ecp,wl,power")
        set_default("jobs", 12)
        set_default("num_cores", 8)
        set_default("time_budget_s", 300)
        set_default("top_n", 120)
        set_default("global_top_n", 120)
        # Disable the extra synth-aware clock sweep for throughput.
        set_default("clock_factors", "")
        set_default("validate_n", 2)
        set_default("validate_n_power", 1)
        set_default("validate_jobs", 1)
        set_default("route_prefilter", False)
        set_default("route_prefilter_stage", "grt")
        # Leave portfolio fractions unset (use ORFS defaults).
    else:
        # "Quality" preset: spend more tuning/validation effort on a smaller suite
        # so gains are easier to see.
        set_default("platforms", "asap7,nangate45,sky130hd")
        set_default("designs", "aes,ibex,jpeg")
        set_default("objectives", "ecp,wl")
        set_default("jobs", 1)
        set_default("num_cores", 8)
        set_default("time_budget_s", 600)
        set_default("top_n", 560)
        set_default("global_top_n", 560)
        # Leave clock factors unset to use ORFS default sweep when the space includes clock_period.
        set_default("validate_n", 14)
        set_default("validate_n_power", 3)
        set_default("validate_jobs", 10)
        set_default("route_prefilter", True)
        set_default("route_prefilter_stage", "grt")
        set_default("portfolio_mean_frac", 0.6)
        set_default("portfolio_ucb_frac", 0.2)
        set_default("portfolio_lcb_frac", 0.2)

    repo_root = Path(__file__).resolve().parent
    flow_dir = repo_root / "flow"
    if not (flow_dir / "Makefile").exists():
        print("ERROR: expected flow/Makefile in repo root", file=sys.stderr)
        return 2

    run_id = args.variant_prefix
    out_csv = Path(args.out) if args.out else (repo_root / f"surrogate_autotune_perf_{_now_tag()}.csv")

    platforms = _split_csv(args.platforms)
    all_designs = _discover_designs(flow_dir, platforms)
    if args.designs.strip():
        wanted = set(_split_csv(args.designs))
        designs = [(p, d) for (p, d) in all_designs if d in wanted]
    else:
        designs = all_designs

    objectives = _objective_plan(args)
    if not designs:
        print("ERROR: no designs selected", file=sys.stderr)
        return 2
    if not objectives:
        print("ERROR: no objectives selected", file=sys.stderr)
        return 2

    csv_lock = threading.Lock()
    progress_lock = threading.Lock()
    designs_done = 0
    objectives_done = 0

    def write_row(row: Dict[str, Any]) -> None:
        with csv_lock:
            with out_csv.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
                w.writerow(row)

    def ensure_baseline(platform: str, design: str) -> Tuple[Optional[str], Optional[float]]:
        ws = flow_dir / "logs" / platform / design / "base" / "6_report.json"
        wl = flow_dir / "logs" / platform / design / "base" / "5_2_route.json"
        if ws.exists() and wl.exists():
            return None, None

        design_config = f"designs/{platform}/{design}/config.mk"
        env = os.environ.copy()
        env["DESIGN_CONFIG"] = design_config
        env["FLOW_VARIANT"] = "base"
        env["NUM_CORES"] = str(args.num_cores)

        target = args.baseline_make_target.strip() or "report"
        target_norm = target.lower()
        if target_norm in {"report", "6_report", "report_only", "do-6_report"}:
            target = f"logs/{platform}/{design}/base/6_report.log"
        cmd = ["make", "--no-print-directory", "-C", "flow", target]
        log_path = repo_root / "bench_logs" / run_id / platform / design / "baseline.log"
        err, elapsed = _run_checked(
            cmd=cmd,
            cwd=repo_root,
            env=env,
            log_path=log_path,
            timeout_s=float(args.timeout_baseline_s) if args.timeout_baseline_s > 0 else None,
        )
        if err is not None:
            return err, elapsed
        if not (ws.exists() and wl.exists()):
            return "baseline_missing_metrics", elapsed
        return None, elapsed

    def run_one_design(platform: str, design: str) -> None:
        nonlocal designs_done, objectives_done
        with progress_lock:
            print(f"[start] {platform}/{design}", flush=True)

        base_err, base_time = ensure_baseline(platform, design)
        if base_err is not None:
            with progress_lock:
                print(f"[baseline-fail] {platform}/{design} err={base_err}", flush=True)
            for r in objectives:
                flow_variant = f"{run_id}_{r.tag}"
                summary_path = flow_dir / "results" / platform / design / flow_variant / "surrogate_autotune.json"
                write_row(
                    _extract_summary_row(
                        run_id=run_id,
                        platform=platform,
                        design=design,
                        flow_variant=flow_variant,
                        objective=r.objective,
                        status="baseline_error",
                        error=base_err,
                        wall_time_s=0.0,
                        baseline_time_s=base_time,
                        summary_path=summary_path if summary_path.exists() else None,
                    )
                )
            return
        with progress_lock:
            if base_time is None:
                print(f"[baseline-ok] {platform}/{design} reused", flush=True)
            else:
                print(f"[baseline-ok] {platform}/{design} time_s={base_time:.1f}", flush=True)

        design_config = f"designs/{platform}/{design}/config.mk"
        design_dir = flow_dir / "designs" / platform / design
        generic_space = repo_root / "surrogate_space_generic_no_clock.json"

        for r in objectives:
            flow_variant = f"{run_id}_{r.tag}"
            results_dir = flow_dir / "results" / platform / design / flow_variant
            summary_path = results_dir / "surrogate_autotune.json"

            if args.resume and summary_path.exists():
                row = _extract_summary_row(
                    run_id=run_id,
                    platform=platform,
                    design=design,
                    flow_variant=flow_variant,
                    objective=r.objective,
                    status="ok_resume",
                    error=None,
                    wall_time_s=0.0,
                    baseline_time_s=base_time,
                    summary_path=summary_path,
                )
                write_row(row)
                with progress_lock:
                    objectives_done += 1
                    print(
                        f"[resume] {platform}/{design} {r.tag} improve_pct={row.get('improve_pct')}",
                        flush=True,
                    )
                continue

            env = os.environ.copy()
            env["DESIGN_CONFIG"] = design_config
            env["FLOW_VARIANT"] = flow_variant
            env["NUM_CORES"] = str(args.num_cores)

            env["SURROGATE_OBJECTIVE"] = r.objective
            if not any(design_dir.glob("surrogate_space*.json")):
                env["SURROGATE_SPACE_FILE"] = str(generic_space)
            env["SURROGATE_RESUME"] = "1" if args.resume else "0"
            env["SURROGATE_SAMPLES"] = str(args.samples)
            env["SURROGATE_TOP_N"] = str(args.top_n)
            env["SURROGATE_GLOBAL_TOP_N"] = str(args.global_top_n)
            env["SURROGATE_TIME_BUDGET_S"] = str(args.time_budget_s)
            if args.clock_factors is not None:
                env["SURROGATE_CLOCK_FACTORS"] = args.clock_factors

            env["SURROGATE_MULTI_FIDELITY"] = "1"
            env["SURROGATE_SHRINK"] = "0.15"
            env["SURROGATE_PORTFOLIO"] = "1"
            env["SURROGATE_PORTFOLIO_SHRINK"] = "0.25"

            env["SURROGATE_VALIDATE"] = "1"
            env["SURROGATE_VALIDATE_N"] = str(r.validate_n)
            env["SURROGATE_VALIDATE_JOBS"] = str(args.validate_jobs)
            env["SURROGATE_VALIDATE_SELECT"] = args.validate_select
            env["SURROGATE_VALIDATE_MAKE_TARGET"] = args.validate_make_target
            if args.portfolio_mean_frac is not None:
                env["SURROGATE_VALIDATE_PORTFOLIO_MEAN_FRAC"] = str(args.portfolio_mean_frac)
            if args.portfolio_ucb_frac is not None:
                env["SURROGATE_VALIDATE_PORTFOLIO_UCB_FRAC"] = str(args.portfolio_ucb_frac)
            if args.portfolio_lcb_frac is not None:
                env["SURROGATE_VALIDATE_PORTFOLIO_LCB_FRAC"] = str(args.portfolio_lcb_frac)

            env["SURROGATE_CONFORMAL_ALPHA"] = str(args.conformal_alpha)
            env["SURROGATE_CONFORMAL_FAIL_RISK_C0"] = str(args.conformal_fail_risk_c0)
            env["SURROGATE_CONFORMAL_SIGMA"] = "fail_risk" if r.objective == "effective_clock_period" else "constant"

            env["SURROGATE_ROUTE_VALIDATE"] = "1" if args.route_prefilter else "0"
            if args.route_prefilter:
                env["SURROGATE_ROUTE_VALIDATE_STAGE"] = args.route_prefilter_stage
                if args.route_n is not None and args.route_n > 0:
                    env["SURROGATE_ROUTE_VALIDATE_N"] = str(args.route_n)
                if args.route_jobs is not None and args.route_jobs > 0:
                    env["SURROGATE_ROUTE_VALIDATE_JOBS"] = str(args.route_jobs)

            cmd = ["make", "--no-print-directory", "-C", "flow", "surrogate_autotune"]
            log_path = repo_root / "bench_logs" / run_id / platform / design / f"{r.tag}.log"
            err, elapsed = _run_checked(
                cmd=cmd,
                cwd=repo_root,
                env=env,
                log_path=log_path,
                timeout_s=float(args.timeout_surrogate_s) if args.timeout_surrogate_s > 0 else None,
            )
            status = "ok" if err is None else "error"

            row = _extract_summary_row(
                run_id=run_id,
                platform=platform,
                design=design,
                flow_variant=flow_variant,
                objective=r.objective,
                status=status,
                error=err,
                wall_time_s=elapsed,
                baseline_time_s=base_time,
                summary_path=summary_path if summary_path.exists() else None,
            )
            write_row(row)
            with progress_lock:
                objectives_done += 1
                print(
                    f"[done] {platform}/{design} {r.tag} status={status} time_s={elapsed:.1f} improve_pct={row.get('improve_pct')}",
                    flush=True,
                )

        with progress_lock:
            designs_done += 1
            print(f"[design-done] {platform}/{design} designs_done={designs_done}", flush=True)

    print(f"Selected designs: {len(designs)}")
    print(f"Objectives: {[r.tag for r in objectives]}")
    print(f"CSV: {out_csv}")
    print(f"Parallel jobs: {args.jobs}  threads/job: {args.num_cores}")
    sys.stdout.flush()

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max(1, int(args.jobs))) as ex:
        futs = [ex.submit(run_one_design, plat, des) for (plat, des) in designs]
        for fut in as_completed(futs):
            # Surface unexpected exceptions early.
            fut.result()

    print(f"Done. Wrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
