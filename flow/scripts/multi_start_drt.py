#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ITER_START_RE = re.compile(
    r"^\[INFO DRT-0195\] Start (\d+)(?:st|nd|rd|th) (?:stubborn tiles|optimization) iteration\.\s*$"
)
VIOL_RE = re.compile(r"^\[INFO DRT-0199\]\s+Number of violations = (\d+)\.\s*$")
TOOK_RE = re.compile(r"^Took (\d+) seconds: detailed_route\b")


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_float_list(name: str) -> list[float] | None:
    value = os.environ.get(name)
    if value is None:
        return None
    parts = [p.strip() for p in value.split(",")]
    values: list[float] = []
    for p in parts:
        if not p:
            continue
        try:
            values.append(float(p))
        except ValueError:
            continue
    return values or None


def _geom_mean(values: list[float]) -> float:
    if not values:
        return 1.0
    prod = 1.0
    for v in values:
        prod *= v
    return prod ** (1.0 / len(values))


def _is_success(*, result: "AttemptResult", success_max_violations: int) -> bool:
    if result.returncode != 0:
        return False
    if result.final_violations is None:
        return False
    return result.final_violations <= success_max_violations


def _should_kill_run(
    *,
    viols: list[int],
    max_iter: int,
    min_iters_before_kill: int,
    ratio_window: int,
    slack: float,
    stall_iters: int,
    stall_rel: float,
    min_ratio_for_predict_kill: float,
) -> bool:
    if len(viols) < min_iters_before_kill:
        return False
    if viols[-1] == 0:
        return False

    if stall_iters > 0 and len(viols) >= stall_iters + 1:
        stalled = True
        for prev, cur in zip(viols[-(stall_iters + 1) : -1], viols[-stall_iters:]):
            if prev <= 0:
                stalled = False
                break
            rel_improve = (prev - cur) / prev
            if rel_improve >= stall_rel:
                stalled = False
                break
        if stalled:
            return True

    if ratio_window <= 0 or len(viols) < ratio_window + 1:
        return False

    recent = viols[-(ratio_window + 1) :]
    ratios: list[float] = []
    for prev, cur in zip(recent[:-1], recent[1:]):
        ratios.append((cur + 1.0) / (prev + 1.0))
    if min(ratios) < min_ratio_for_predict_kill:
        return False
    r = _geom_mean(ratios)
    if r >= 1.0:
        return True

    remaining_budget = max_iter - len(viols)
    if remaining_budget <= 0:
        return False

    est_remaining = math.log(viols[-1] + 1.0) / (-math.log(r))
    return est_remaining > remaining_budget * slack


@dataclass(frozen=True)
class AttemptResult:
    attempt: int
    seed: int
    or_k: float
    killed: bool
    returncode: int
    final_violations: int | None
    iter_count: int
    took_seconds: int | None
    results_dir: Path
    logs_dir: Path
    reports_dir: Path

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.final_violations == 0


def _copy_checkpoint(*, src_results: Path, dst_results: Path) -> None:
    dst_results.mkdir(parents=True, exist_ok=True)
    for name in ("5_1_grt.odb", "5_1_grt.sdc", "route.guide"):
        src = src_results / name
        if not src.exists():
            raise FileNotFoundError(f"Missing global-route checkpoint file: {src}")
        shutil.copy2(src, dst_results / name)


def _run_attempt(
    *,
    stage: str,
    script: str,
    attempt: int,
    seed: int,
    max_iter: int,
    or_k: float,
    allow_kill: bool,
    base_env: dict[str, str],
    orig_results_dir: Path,
    orig_log_dir: Path,
    orig_reports_dir: Path,
    orig_objects_dir: Path,
    min_iters_before_kill: int,
    ratio_window: int,
    slack: float,
    stall_iters: int,
    stall_rel: float,
    min_ratio_for_predict_kill: float,
) -> AttemptResult:
    try_results_dir = orig_results_dir / "_multistart" / f"try{attempt:02d}"
    try_log_dir = orig_log_dir / "_multistart" / f"try{attempt:02d}"
    try_reports_dir = orig_reports_dir / "_multistart" / f"try{attempt:02d}"
    try_objects_dir = orig_objects_dir / "_multistart" / f"try{attempt:02d}"

    for d in (try_results_dir, try_log_dir, try_reports_dir, try_objects_dir):
        d.mkdir(parents=True, exist_ok=True)

    _copy_checkpoint(src_results=orig_results_dir, dst_results=try_results_dir)

    env = dict(base_env)
    env["RESULTS_DIR"] = str(try_results_dir)
    env["LOG_DIR"] = str(try_log_dir)
    env["REPORTS_DIR"] = str(try_reports_dir)
    env["OBJECTS_DIR"] = str(try_objects_dir)
    env["DETAILED_ROUTE_END_ITERATION"] = str(max_iter)
    env["OR_SEED"] = str(seed)
    env["OR_K"] = str(or_k)
    env.setdefault("SKIP_ANTENNA_REPAIR_POST_DRT", "1")

    scripts_dir = Path(env["SCRIPTS_DIR"])

    tcl = scripts_dir / f"{script}.tcl"
    metrics_path = try_log_dir / f"{stage}.json"
    log_path = try_log_dir / f"{stage}.log"

    time_cmd = shlex.split(env.get("TIME_CMD", ""))
    openroad_cmd = shlex.split(env["OPENROAD_CMD"])
    cmd = [*time_cmd, *openroad_cmd, "-no_splash", str(tcl), "-metrics", str(metrics_path)]

    header = (
        f"\n=== DRT multistart try {attempt} seed={seed} or_k={or_k} "
        f"droute_end_iter={max_iter} ===\n"
    )
    sys.stdout.write(header)
    sys.stdout.flush()

    killed = False
    viols: list[int] = []
    iter_count = 0
    took_seconds: int | None = None
    with log_path.open("w") as log_f:
        log_f.write(header)
        log_f.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log_f.write(line)
            log_f.flush()
            sys.stdout.write(line)
            sys.stdout.flush()

            if ITER_START_RE.match(line):
                iter_count += 1

            m = VIOL_RE.match(line)
            if m:
                viols.append(int(m.group(1)))
                if _should_kill_run(
                    viols=viols,
                    max_iter=max_iter,
                    min_iters_before_kill=min_iters_before_kill,
                    ratio_window=ratio_window,
                    slack=slack,
                    stall_iters=stall_iters,
                    stall_rel=stall_rel,
                    min_ratio_for_predict_kill=min_ratio_for_predict_kill,
                ):
                    if allow_kill and not killed:
                        killed = True
                        msg = (
                            f"[DRT multistart] Killing try {attempt} (seed={seed}) after "
                            f"{len(viols)} iterations; violations={viols[-1]}.\n"
                        )
                        log_f.write(msg)
                        log_f.flush()
                        sys.stdout.write(msg)
                        sys.stdout.flush()
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass

            m = TOOK_RE.match(line)
            if m:
                took_seconds = int(m.group(1))

        returncode = proc.wait()

    final_violations: int | None = viols[-1] if viols else None

    return AttemptResult(
        attempt=attempt,
        seed=seed,
        or_k=or_k,
        killed=killed,
        returncode=returncode,
        final_violations=final_violations,
        iter_count=iter_count,
        took_seconds=took_seconds,
        results_dir=try_results_dir,
        logs_dir=try_log_dir,
        reports_dir=try_reports_dir,
    )


def _copy_outputs(*, src: AttemptResult, stage: str, dst_results: Path, dst_logs: Path, dst_reports: Path) -> None:
    for name in ("5_2_route.odb", "maze.log"):
        src_path = src.results_dir / name
        if src_path.exists():
            shutil.copy2(src_path, dst_results / name)

    for name in ("5_route_drc.rpt", "drt_antennas.log"):
        src_path = src.reports_dir / name
        if src_path.exists():
            shutil.copy2(src_path, dst_reports / name)

    src_metrics = src.logs_dir / f"{stage}.json"
    if src_metrics.exists():
        shutil.copy2(src_metrics, dst_logs / f"{stage}.json")


def _append_elapsed(*, stage: str, log_dir: Path, log_path: Path, python_exe: str, utils_dir: Path) -> None:
    cmd = [python_exe, str(utils_dir / "genElapsedTime.py"), "--match", stage, "-d", str(log_dir)]
    elapsed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    with log_path.open("a") as log_f:
        log_f.write(elapsed.stdout)
    sys.stdout.write(elapsed.stdout)
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("stage")
    ap.add_argument("script")
    args = ap.parse_args()

    stage, script = args.stage, args.script
    if stage != "5_2_route":
        print(f"multi_start_drt.py only supports stage 5_2_route (got {stage})", file=sys.stderr)
        return 2

    base_env = dict(os.environ)
    orig_results_dir = Path(base_env["RESULTS_DIR"])
    orig_log_dir = Path(base_env["LOG_DIR"])
    orig_reports_dir = Path(base_env["REPORTS_DIR"])
    orig_objects_dir = Path(base_env["OBJECTS_DIR"])
    utils_dir = Path(base_env["UTILS_DIR"])
    python_exe = base_env.get("PYTHON_EXE", "python3")

    for d in (orig_results_dir, orig_log_dir, orig_reports_dir, orig_objects_dir):
        d.mkdir(parents=True, exist_ok=True)

    max_runs = _env_int("DETAILED_ROUTE_MULTI_START_MAX_RUNS", 8)
    max_iter = _env_int("DETAILED_ROUTE_MULTI_START_MAX_ITER", 10)
    base_seed = _env_int("DETAILED_ROUTE_MULTI_START_SEED_BASE", _env_int("OR_SEED", 0))
    success_max_violations = _env_int("DETAILED_ROUTE_MULTI_START_SUCCESS_MAX_VIOLATIONS", 0)
    accept_best = _env_int("DETAILED_ROUTE_MULTI_START_ACCEPT_BEST", 0)
    or_k_list = _env_float_list("DETAILED_ROUTE_MULTI_START_OR_K_LIST")
    if or_k_list is None:
        if "DETAILED_ROUTE_MULTI_START_OR_K" in base_env:
            or_k = _env_float("DETAILED_ROUTE_MULTI_START_OR_K", 0.10)
        else:
            or_k = _env_float("OR_K", 0.10)
            if or_k == 0.0:
                or_k = 0.10
        or_k_list = [or_k]

    min_iters_before_kill = _env_int("DETAILED_ROUTE_MULTI_START_MIN_ITERS_BEFORE_KILL", 4)
    ratio_window = _env_int("DETAILED_ROUTE_MULTI_START_RATIO_WINDOW", 3)
    slack = _env_float("DETAILED_ROUTE_MULTI_START_PREDICT_SLACK", 1.25)
    stall_iters = _env_int("DETAILED_ROUTE_MULTI_START_STALL_ITERS", 2)
    stall_rel = _env_float("DETAILED_ROUTE_MULTI_START_STALL_REL", 0.01)
    min_ratio_for_predict_kill = _env_float("DETAILED_ROUTE_MULTI_START_MIN_RATIO_FOR_PREDICT_KILL", 0.80)

    sys.stdout.write(
        "\n=== DRT multistart enabled ===\n"
        f"max_runs={max_runs} max_iter={max_iter} seed_base={base_seed} or_k_list={or_k_list}\n"
        f"success_max_violations={success_max_violations}\n"
        f"kill: min_iters={min_iters_before_kill} ratio_window={ratio_window} "
        f"predict_slack={slack} stall_iters={stall_iters} stall_rel={stall_rel} "
        f"min_ratio_for_predict_kill={min_ratio_for_predict_kill}\n"
    )
    sys.stdout.flush()

    attempts: list[AttemptResult] = []
    for attempt in range(max_runs):
        seed = base_seed + attempt
        or_k = or_k_list[attempt % len(or_k_list)]
        allow_kill = not (accept_best and attempt == max_runs - 1)
        result = _run_attempt(
            stage=stage,
            script=script,
            attempt=attempt,
            seed=seed,
            max_iter=max_iter,
            or_k=or_k,
            allow_kill=allow_kill,
            base_env=base_env,
            orig_results_dir=orig_results_dir,
            orig_log_dir=orig_log_dir,
            orig_reports_dir=orig_reports_dir,
            orig_objects_dir=orig_objects_dir,
            min_iters_before_kill=min_iters_before_kill,
            ratio_window=ratio_window,
            slack=slack,
            stall_iters=stall_iters,
            stall_rel=stall_rel,
            min_ratio_for_predict_kill=min_ratio_for_predict_kill,
        )
        attempts.append(result)
        if _is_success(result=result, success_max_violations=success_max_violations):
            break

    best_ok = next(
        (r for r in attempts if _is_success(result=r, success_max_violations=success_max_violations)),
        None,
    )
    if best_ok is not None:
        best = best_ok
    else:
        completed = [r for r in attempts if r.returncode == 0]
        if completed:
            best = min(
                completed,
                key=lambda r: r.final_violations if r.final_violations is not None else 1_000_000_000,
            )
        else:
            best = min(
                attempts,
                key=lambda r: r.final_violations if r.final_violations is not None else 1_000_000_000,
            )

    summary_lines = [
        "\n=== DRT multistart summary ===",
        f"success_max_violations={success_max_violations}",
        "try seed  or_k killed rc iters violations took_s",
    ]
    for r in attempts:
        ok = _is_success(result=r, success_max_violations=success_max_violations)
        summary_lines.append(
            f"{r.attempt:3d} {r.seed:4d} {r.or_k:4.2f} {int(r.killed):6d} {r.returncode:2d} "
            f"{r.iter_count:5d} {r.final_violations if r.final_violations is not None else -1:10d} "
            f"{r.took_seconds if r.took_seconds is not None else -1:6d} "
            f"{'OK' if ok else ''}"
        )
    summary_lines.append("")
    summary = "\n".join(summary_lines)
    sys.stdout.write(summary)
    sys.stdout.flush()

    final_log_path = orig_log_dir / f"{stage}.log"
    with final_log_path.open("w") as f:
        f.write(summary)
        f.write("\n")

        chosen_log = best.logs_dir / f"{stage}.log"
        if chosen_log.exists():
            f.write(chosen_log.read_text())

    _copy_outputs(
        src=best,
        stage=stage,
        dst_results=orig_results_dir,
        dst_logs=orig_log_dir,
        dst_reports=orig_reports_dir,
    )

    if _is_success(result=best, success_max_violations=success_max_violations):
        _append_elapsed(
            stage=stage,
            log_dir=orig_log_dir,
            log_path=final_log_path,
            python_exe=python_exe,
            utils_dir=utils_dir,
        )
        return 0

    if accept_best and best.returncode == 0:
        sys.stdout.write(
            f"[DRT multistart] No run met success_max_violations={success_max_violations}; "
            f"accepting best completed attempt (violations={best.final_violations}).\n"
        )
        sys.stdout.flush()
        _append_elapsed(
            stage=stage,
            log_dir=orig_log_dir,
            log_path=final_log_path,
            python_exe=python_exe,
            utils_dir=utils_dir,
        )
        return 0

    fallback = _env_int("DETAILED_ROUTE_MULTI_START_FALLBACK_TO_SINGLE", 1)
    if not fallback:
        sys.stdout.write("[DRT multistart] No successful run; failing (fallback disabled).\n")
        sys.stdout.flush()
        _append_elapsed(
            stage=stage,
            log_dir=orig_log_dir,
            log_path=final_log_path,
            python_exe=python_exe,
            utils_dir=utils_dir,
        )
        return best.returncode if best.returncode != 0 else 1

    sys.stdout.write("[DRT multistart] No successful run; falling back to single-run detailed route.\n")
    sys.stdout.flush()

    base_env.pop("DETAILED_ROUTE_MULTI_START", None)
    base_env.pop("DETAILED_ROUTE_MULTI_START_FALLBACK_TO_SINGLE", None)

    scripts_dir = Path(base_env["SCRIPTS_DIR"])
    tcl = scripts_dir / f"{script}.tcl"
    metrics_path = orig_log_dir / f"{stage}.json"
    time_cmd = shlex.split(base_env.get("TIME_CMD", ""))
    openroad_cmd = shlex.split(base_env["OPENROAD_CMD"])
    cmd = [*time_cmd, *openroad_cmd, "-no_splash", str(tcl), "-metrics", str(metrics_path)]
    with final_log_path.open("a") as log_f:
        log_f.write("\n=== DRT multistart fallback: single-run ===\n")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=base_env,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log_f.write(line)
            log_f.flush()
            sys.stdout.write(line)
            sys.stdout.flush()
        rc = proc.wait()

    _append_elapsed(
        stage=stage,
        log_dir=orig_log_dir,
        log_path=final_log_path,
        python_exe=python_exe,
        utils_dir=utils_dir,
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

