#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ClockRun:
    clock_ps: float
    variant: str
    results_json: Path


def _split_tokens(s: str) -> List[str]:
    return [tok for tok in re.split(r"[,\s]+", s.strip()) if tok]


def _parse_float_list(s: str) -> List[float]:
    out: List[float] = []
    for tok in _split_tokens(s):
        out.append(float(tok))
    return out


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _clock_tag(clock_ps: float) -> str:
    s = f"{clock_ps:.3f}".rstrip("0").rstrip(".")
    return s.replace(".", "p")


def _infer_base_clock_ps_from_sdc(sdc_path: Path) -> Optional[float]:
    text = _read_text(sdc_path)

    m = re.search(r"(?m)^\s*set\s+clk_period\s+([0-9]*\.?[0-9]+)\s*$", text)
    if m:
        return float(m.group(1))

    m = re.search(r"(?m)create_clock\b.*?-period\s+([0-9]*\.?[0-9]+)\b", text)
    if m:
        return float(m.group(1))

    return None


def _rewrite_sdc_clock_period(base_sdc: Path, clock_ps: float) -> str:
    text = _read_text(base_sdc)
    replacement = f"set clk_period {clock_ps}"
    if re.search(r"(?m)^\s*set\s+clk_period\s+", text):
        return re.sub(r"(?m)^\s*set\s+clk_period\s+\S+.*$", replacement, text)

    # Fall back to create_clock -period <...>
    if re.search(r"(?m)create_clock\b.*?-period\s+\S+", text):
        return re.sub(
            r"(?m)(create_clock\b.*?-period)\s+\S+",
            rf"\1 {clock_ps}",
            text,
        )

    raise RuntimeError(f"Could not find clk_period or create_clock -period in {base_sdc}")


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=False)
        f.write("\n")


def _results_dir(flow_dir: Path, platform: str, design: str, variant: str) -> Path:
    return flow_dir / "results" / platform / design / variant


def _logs_dir(flow_dir: Path, platform: str, design: str, variant: str) -> Path:
    return flow_dir / "logs" / platform / design / variant


def _default_calibration_files(flow_dir: Path, platform: str, design: str) -> Tuple[Path, Path]:
    ws = flow_dir / "logs" / platform / design / "base" / "6_report.json"
    wl = flow_dir / "logs" / platform / design / "base" / "5_2_route.json"
    return ws, wl


def _ensure_file(path: Path, what: str) -> None:
    if not path.exists():
        raise RuntimeError(f"Missing {what}: {path}")


def _space_without_clock(flow_dir: Path, base_variant: str, space_file: Path) -> Path:
    space = _load_json(space_file)
    if "clock_period" in space:
        del space["clock_period"]
    out = flow_dir / "tmp_surrogate" / f"{base_variant}_space_no_clock.json"
    _dump_json(out, space)
    return out


def _extract_scales(surrogate_opt_json: Path) -> Tuple[float, float, float]:
    data = _load_json(surrogate_opt_json)
    feats = data.get("best_features", {})
    length_scale = float(feats["builtin_length_scale"])
    timing_scale = float(feats["builtin_timing_scale"])
    ref_clock = float(feats["clock_period"])
    return length_scale, timing_scale, ref_clock


def _summarize_run(surrogate_opt_json: Path) -> Dict[str, Any]:
    data = _load_json(surrogate_opt_json)
    return {
        "best_objective": data.get("best_objective"),
        "best_params": data.get("best_params"),
        "best_outputs": data.get("best_outputs"),
        "top": data.get("top", []),
    }


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    flow_dir = script_dir.parent

    design_config = os.environ.get("DESIGN_CONFIG")
    if not design_config:
        print("ERROR: DESIGN_CONFIG is not set", file=sys.stderr)
        return 2

    platform = os.environ.get("PLATFORM")
    design = os.environ.get("DESIGN_NICKNAME")
    if not platform or not design:
        print("ERROR: PLATFORM/DESIGN_NICKNAME must be exported by DESIGN_CONFIG", file=sys.stderr)
        return 2

    base_variant = os.environ.get("FLOW_VARIANT", "base")
    if base_variant == "base":
        base_variant = "surrogate_synthaware"

    space_file_env = os.environ.get("SURROGATE_SPACE_FILE", "").strip()
    if space_file_env:
        space_file = Path(space_file_env)
    else:
        design_dir = Path(os.environ.get("DESIGN_DIR", str(Path(design_config).parent)))
        space_file = design_dir / "surrogate_space_no_cts.json"
    _ensure_file(space_file, "SURROGATE_SPACE_FILE")

    samples = int(os.environ.get("SURROGATE_SAMPLES", "500000"))
    top_n = int(os.environ.get("SURROGATE_TOP_N", "10"))

    base_sdc = Path(os.environ.get("SDC_FILE", "")).resolve()
    if not base_sdc.exists():
        print("ERROR: SDC_FILE is not set or does not exist", file=sys.stderr)
        return 2

    base_clock_ps = _infer_base_clock_ps_from_sdc(base_sdc)
    if base_clock_ps is None:
        print(f"ERROR: Could not infer base clock from {base_sdc}", file=sys.stderr)
        return 2

    clocks: List[float]
    if os.environ.get("SURROGATE_CLOCKS"):
        clocks = _parse_float_list(os.environ["SURROGATE_CLOCKS"])
    else:
        clock_min = float(os.environ.get("SURROGATE_CLOCK_MIN", "300"))
        clock_max = float(os.environ.get("SURROGATE_CLOCK_MAX", "420"))
        clock_step = float(os.environ.get("SURROGATE_CLOCK_STEP", "10"))
        if clock_step <= 0:
            raise RuntimeError("SURROGATE_CLOCK_STEP must be > 0")
        n = int(round((clock_max - clock_min) / clock_step))
        clocks = [clock_min + i * clock_step for i in range(n + 1)]

    clocks.append(base_clock_ps)
    clocks = sorted({float(c) for c in clocks if c > 0.0})

    ws_file_env = os.environ.get("SURROGATE_CALIBRATE_WS_FILE", "").strip()
    wl_file_env = os.environ.get("SURROGATE_CALIBRATE_WL_FILE", "").strip()
    if ws_file_env and wl_file_env:
        ws_file = Path(ws_file_env)
        wl_file = Path(wl_file_env)
    else:
        ws_file, wl_file = _default_calibration_files(flow_dir, platform, design)
    _ensure_file(ws_file, "calibration WS file (6_report.json)")
    _ensure_file(wl_file, "calibration WL file (5_2_route.json)")

    # Create a space file that excludes clock_period; clock is swept by re-synthesizing.
    space_no_clock = _space_without_clock(flow_dir, base_variant, space_file)

    base_env = dict(os.environ)
    base_env["PYTHONUNBUFFERED"] = "1"

    print(f"Design: {platform}/{design}")
    print(f"Base variant prefix: {base_variant}")
    print(f"Clock sweep (ps): {', '.join(_clock_tag(c) for c in clocks)}")
    print(f"Samples/clock: {samples}, top_n/clock: {top_n}")
    print(f"Calibration: ws={ws_file} wl={wl_file}")

    # 1) Calibrate once (use base clock / default constraint) to get stable builtin scales.
    calib_variant = f"{base_variant}_calib"

    calib_env = dict(base_env)
    calib_env["SURROGATE_SPACE_FILE"] = str(space_no_clock)
    calib_env["SURROGATE_FREEZE"] = "clock_period"
    calib_env["SURROGATE_RESET_CALIBRATION"] = "1"
    calib_env["SURROGATE_CALIBRATE_WS_FILE"] = str(ws_file)
    calib_env["SURROGATE_CALIBRATE_WL_FILE"] = str(wl_file)

    print(f"+ make surrogate_tune (calibrate) FLOW_VARIANT={calib_variant}", flush=True)
    subprocess.run(
        [
            "make",
            "--no-print-directory",
            "surrogate_tune",
            f"FLOW_VARIANT={calib_variant}",
            f"SURROGATE_SPACE_FILE={space_no_clock}",
            "SURROGATE_FREEZE=clock_period",
            "SURROGATE_RESET_CALIBRATION=1",
            f"SURROGATE_CALIBRATE_WS_FILE={ws_file}",
            f"SURROGATE_CALIBRATE_WL_FILE={wl_file}",
            "SURROGATE_SAMPLES=1",
            "SURROGATE_TOP_N=1",
        ],
        cwd=str(flow_dir),
        env=calib_env,
        check=True,
    )

    calib_out = _results_dir(flow_dir, platform, design, calib_variant) / "surrogate_optimize.json"
    _ensure_file(calib_out, "calibration surrogate_optimize.json")
    length_scale, timing_scale, ref_clock_ps = _extract_scales(calib_out)
    print(
        f"Calibration scales: builtin_length_scale={length_scale:.6g} "
        f"builtin_timing_scale={timing_scale:.6g} ref_clock_ps={ref_clock_ps:.6g}"
    )

    # 2) Sweep clocks: re-synthesize per clock, then run fast surrogate optimization on that netlist.
    clock_runs: List[ClockRun] = []
    clock_constraints_dir = flow_dir / "tmp_surrogate" / base_variant
    clock_constraints_dir.mkdir(parents=True, exist_ok=True)

    for clock_ps in clocks:
        tag = _clock_tag(clock_ps)
        variant = f"{base_variant}_clk{tag}"
        sdc_out = clock_constraints_dir / f"constraint_clk{tag}.sdc"
        _write_text(sdc_out, _rewrite_sdc_clock_period(base_sdc, clock_ps))

        run_env = dict(base_env)
        run_env["SURROGATE_SPACE_FILE"] = str(space_no_clock)
        run_env["SURROGATE_FREEZE"] = "clock_period"
        run_env["SURROGATE_RESET_CALIBRATION"] = "1"
        run_env["SURROGATE_BUILTIN_LENGTH_SCALE"] = str(length_scale)
        run_env["SURROGATE_BUILTIN_TIMING_SCALE"] = str(timing_scale)
        run_env["SURROGATE_BUILTIN_REF_CLOCK_USER"] = str(ref_clock_ps)

        print(f"+ make surrogate_tune FLOW_VARIANT={variant} SDC_FILE={sdc_out}", flush=True)
        subprocess.run(
            [
                "make",
                "--no-print-directory",
                "surrogate_tune",
                f"FLOW_VARIANT={variant}",
                f"SDC_FILE={sdc_out}",
                f"SURROGATE_SPACE_FILE={space_no_clock}",
                "SURROGATE_FREEZE=clock_period",
                "SURROGATE_RESET_CALIBRATION=1",
                f"SURROGATE_SAMPLES={samples}",
                f"SURROGATE_TOP_N={top_n}",
            ],
            cwd=str(flow_dir),
            env=run_env,
            check=True,
        )

        out_json = _results_dir(flow_dir, platform, design, variant) / "surrogate_optimize.json"
        _ensure_file(out_json, f"{variant} surrogate_optimize.json")
        clock_runs.append(ClockRun(clock_ps=clock_ps, variant=variant, results_json=out_json))

    # 3) Merge results across clocks into a single summary artifact.
    merged_top: List[Dict[str, Any]] = []
    per_clock: List[Dict[str, Any]] = []
    for run in clock_runs:
        summary = _summarize_run(run.results_json)
        per_clock.append(
            {
                "clock_ps": run.clock_ps,
                "variant": run.variant,
                "results": summary,
            }
        )
        for cand in summary.get("top", []):
            merged_top.append(
                {
                    "clock_ps": run.clock_ps,
                    "variant": run.variant,
                    "candidate": cand,
                }
            )

    merged_top.sort(key=lambda x: float(x["candidate"].get("objective", 1e300)))

    global_top_n = int(os.environ.get("SURROGATE_GLOBAL_TOP_N", str(top_n)))
    merged_top = merged_top[: max(1, global_top_n)]

    out_dir = _results_dir(flow_dir, platform, design, base_variant)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "surrogate_synthaware.json"
    _dump_json(
        summary_path,
        {
            "design": {"platform": platform, "design": design},
            "base_variant": base_variant,
            "space_file": str(space_file),
            "space_no_clock_file": str(space_no_clock),
            "base_sdc": str(base_sdc),
            "clock_sweep_ps": clocks,
            "calibration": {
                "ws_file": str(ws_file),
                "wl_file": str(wl_file),
                "builtin_length_scale": length_scale,
                "builtin_timing_scale": timing_scale,
                "builtin_ref_clock_ps": ref_clock_ps,
            },
            "per_clock": per_clock,
            "global_top": merged_top,
        },
    )

    print(f"Wrote merged summary: {summary_path}")
    if merged_top:
        best = merged_top[0]["candidate"]
        best_obj = best.get("objective")
        best_params = best.get("params")
        best_variant = merged_top[0]["variant"]
        print(f"Best predicted: obj={best_obj} variant={best_variant} params={best_params}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
