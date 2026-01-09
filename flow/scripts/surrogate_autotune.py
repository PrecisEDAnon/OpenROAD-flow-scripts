#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


@dataclass(frozen=True)
class ClockTuneRun:
    clock_ps: float
    tag: str
    variant: str
    sdc_file: Path
    results_json: Path


@dataclass(frozen=True)
class ValidationRun:
    rank: int
    clock_ps: float
    tag: str
    tune_variant: str
    validate_variant: str
    sdc_file: Path
    synth_netlist: Optional[Path]
    params: Dict[str, Any]
    report_json: Path
    ecp_ps: Optional[float]
    fmax_hz: Optional[float]


def _split_tokens(s: str) -> List[str]:
    return [tok for tok in re.split(r"[,\s]+", s.strip()) if tok]


def _parse_float_list(s: str) -> List[float]:
    return [float(tok) for tok in _split_tokens(s)]


def _truthy(v: Optional[str]) -> bool:
    if v is None:
        return False
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ensure_file(path: Path, what: str) -> None:
    if not path.exists():
        raise RuntimeError(f"Missing {what}: {path}")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=False)
        f.write("\n")


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

    if re.search(r"(?m)create_clock\b.*?-period\s+\S+", text):
        return re.sub(
            r"(?m)(create_clock\b.*?-period)\s+\S+",
            rf"\1 {clock_ps}",
            text,
        )

    raise RuntimeError(f"Could not find clk_period or create_clock -period in {base_sdc}")


def _results_dir(flow_dir: Path, platform: str, design: str, variant: str) -> Path:
    return flow_dir / "results" / platform / design / variant


def _logs_dir(flow_dir: Path, platform: str, design: str, variant: str) -> Path:
    return flow_dir / "logs" / platform / design / variant


def _default_calibration_files(flow_dir: Path, platform: str, design: str) -> Tuple[Path, Path]:
    ws = flow_dir / "logs" / platform / design / "base" / "6_report.json"
    wl = flow_dir / "logs" / platform / design / "base" / "5_2_route.json"
    return ws, wl


def _compute_ecp_ps_from_report(report_json: Path) -> Optional[float]:
    try:
        data = _load_json(report_json)
    except FileNotFoundError:
        return None

    fmax = data.get("finish__timing__fmax")
    if fmax is not None:
        try:
            fmax_hz = float(fmax)
            if fmax_hz > 0:
                return 1e12 / fmax_hz
        except (TypeError, ValueError):
            pass

    clk = data.get("constraint__clock__period")
    ws = data.get("finish__timing__setup__ws")
    if clk is not None and ws is not None:
        try:
            return float(clk) - float(ws)
        except (TypeError, ValueError):
            return None

    return None


def _extract_scales(surrogate_opt_json: Path) -> Tuple[float, float, float]:
    data = _load_json(surrogate_opt_json)
    feats = data.get("best_features", {})
    length_scale = float(feats["builtin_length_scale"])
    timing_scale = float(feats["builtin_timing_scale"])
    ref_clock = float(feats["clock_period"])
    return length_scale, timing_scale, ref_clock


def _summarize_optimize_json(surrogate_opt_json: Path) -> Dict[str, Any]:
    data = _load_json(surrogate_opt_json)
    top = data.get("top")
    if not isinstance(top, list) or not top:
        # Older/alternate formats may not include an explicit "top" list when
        # top_n == 1; synthesize a 1-entry list from the best_* fields.
        top = [
            {
                "objective": data.get("best_objective"),
                "params": data.get("best_params"),
                "outputs": data.get("best_outputs"),
            }
        ]
    return {
        "best_objective": data.get("best_objective"),
        "best_params": data.get("best_params"),
        "best_outputs": data.get("best_outputs"),
        "top": top,
    }


def _space_without_clock(flow_dir: Path, base_variant: str, space_file: Path) -> Path:
    space = _load_json(space_file)
    if not isinstance(space, dict):
        raise RuntimeError(f"Invalid space file (expected dict): {space_file}")
    if "clock_period" in space:
        del space["clock_period"]
    out = flow_dir / "tmp_surrogate" / f"{base_variant}_space_no_clock.json"
    _dump_json(out, space)
    return out


def _pick_space_file(design_dir: Path, space_file_env: str) -> Path:
    if space_file_env.strip():
        return Path(space_file_env)

    sane = design_dir / "surrogate_space_no_cts_sane.json"
    if sane.exists():
        return sane

    no_cts = design_dir / "surrogate_space_no_cts.json"
    if no_cts.exists():
        return no_cts

    full = design_dir / "surrogate_space.json"
    if full.exists():
        return full

    return design_dir / "surrogate_space.json"


def _space_has_clock(space_file: Path) -> bool:
    space = _load_json(space_file)
    return isinstance(space, dict) and ("clock_period" in space)


def _auto_clock_candidates(
    *,
    base_clock_ps: float,
    baseline_ecp_ps: Optional[float],
    factors: Sequence[float],
    min_ps: Optional[float],
    max_ps: Optional[float],
) -> List[float]:
    clocks: List[float] = [base_clock_ps]

    if baseline_ecp_ps is not None and baseline_ecp_ps > 0:
        for f in factors:
            clocks.append(baseline_ecp_ps * float(f))

    uniq: List[float] = []
    seen: Set[float] = set()
    for c in sorted(clocks):
        if not (c > 0.0):
            continue
        if min_ps is not None and c < min_ps:
            continue
        if max_ps is not None and c > max_ps:
            continue
        key = round(c, 3)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(float(key))

    return uniq


def _make_cmd(
    *,
    target: str,
    flow_dir: Path,
    extra_make_args: Sequence[str],
    env: Dict[str, str],
) -> None:
    cmd = ["make", "--no-print-directory", target, *extra_make_args]
    subprocess.run(cmd, cwd=str(flow_dir), env=env, check=True)


def _orfs_var_assignments_from_params(params: Dict[str, Any]) -> List[str]:
    knob_map: Dict[str, str] = {
        "core_utilization": "CORE_UTILIZATION",
        "core_aspect_ratio": "CORE_ASPECT_RATIO",
        "tns_end_percent": "TNS_END_PERCENT",
        "global_padding": "CELL_PAD_IN_SITES_GLOBAL_PLACEMENT",
        "detail_padding": "CELL_PAD_IN_SITES_DETAIL_PLACEMENT",
        "enable_dpo": "ENABLE_DPO",
        "pin_layer_adjust": "PIN_LAYER_ADJUST",
        "above_layer_adjust": "ABOVE_LAYER_ADJUST",
        "density_margin_addon": "PLACE_DENSITY_LB_ADDON",
        "cts_cluster_size": "CTS_CLUSTER_SIZE",
        "cts_cluster_diameter": "CTS_CLUSTER_DIAMETER",
    }

    out: List[str] = []
    for k, env_k in knob_map.items():
        if k not in params:
            continue
        v = params[k]
        if isinstance(v, bool):
            v = int(v)
        out.append(f"{env_k}={v}")
    return out


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    flow_dir = script_dir.parent

    design_config = os.environ.get("DESIGN_CONFIG", "").strip()
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
        base_variant = "surrogate_autotune"

    base_sdc = Path(os.environ.get("SDC_FILE", "")).resolve()
    if not base_sdc.exists():
        print("ERROR: SDC_FILE is not set or does not exist", file=sys.stderr)
        return 2

    base_clock_ps = _infer_base_clock_ps_from_sdc(base_sdc)
    if base_clock_ps is None:
        print(f"ERROR: Could not infer base clock from {base_sdc}", file=sys.stderr)
        return 2

    design_dir = Path(os.environ.get("DESIGN_DIR", str(Path(design_config).parent)))
    space_file = _pick_space_file(design_dir, os.environ.get("SURROGATE_SPACE_FILE", ""))
    _ensure_file(space_file, "SURROGATE_SPACE_FILE")

    ws_file_env = os.environ.get("SURROGATE_CALIBRATE_WS_FILE", "").strip()
    wl_file_env = os.environ.get("SURROGATE_CALIBRATE_WL_FILE", "").strip()
    if ws_file_env and wl_file_env:
        ws_file = Path(ws_file_env)
        wl_file = Path(wl_file_env)
    else:
        ws_file, wl_file = _default_calibration_files(flow_dir, platform, design)
    _ensure_file(ws_file, "calibration WS file (6_report.json)")
    _ensure_file(wl_file, "calibration WL file (5_2_route.json)")

    baseline_ecp_ps = _compute_ecp_ps_from_report(ws_file)

    samples = int(os.environ.get("SURROGATE_SAMPLES", "500000"))
    top_n = int(os.environ.get("SURROGATE_TOP_N", "10"))
    global_top_n = int(os.environ.get("SURROGATE_GLOBAL_TOP_N", str(top_n)))
    resume = _truthy(os.environ.get("SURROGATE_RESUME"))

    base_env = dict(os.environ)
    base_env["PYTHONUNBUFFERED"] = "1"

    print(f"Design: {platform}/{design}")
    print(f"Base variant prefix: {base_variant}")
    print(f"Space file: {space_file}")
    print(f"Calibration: ws={ws_file} wl={wl_file}")
    if baseline_ecp_ps is not None:
        print(f"Baseline ECP (from calibration): {baseline_ecp_ps:.3f} ps")

    if not _space_has_clock(space_file):
        variant = base_variant
        out_json = _results_dir(flow_dir, platform, design, variant) / "surrogate_optimize.json"
        if resume and out_json.exists():
            print(f"Resume: reusing existing {out_json}")
            return 0

        print("+ Running single-netlist surrogate_tune (no clock in space)")
        _make_cmd(
            target="surrogate_tune",
            flow_dir=flow_dir,
            extra_make_args=[
                f"FLOW_VARIANT={variant}",
                f"SURROGATE_SPACE_FILE={space_file}",
                f"SURROGATE_SAMPLES={samples}",
                f"SURROGATE_TOP_N={top_n}",
                "SURROGATE_FREEZE=clock_period",
                "SURROGATE_RESET_CALIBRATION=1",
                f"SURROGATE_CALIBRATE_WS_FILE={ws_file}",
                f"SURROGATE_CALIBRATE_WL_FILE={wl_file}",
            ],
            env=base_env,
        )
        print(f"Wrote: {out_json}")
        return 0

    # Synthesis-aware tuning: sweep clocks, re-synth per clock, tune knobs with clock frozen.
    space_no_clock = _space_without_clock(flow_dir, base_variant, space_file)

    # 1) Calibrate once at base clock to get stable builtin scales.
    calib_variant = f"{base_variant}_calib"
    calib_out = _results_dir(flow_dir, platform, design, calib_variant) / "surrogate_optimize.json"
    if not (resume and calib_out.exists()):
        print(f"+ make surrogate_tune (calibrate) FLOW_VARIANT={calib_variant}", flush=True)
        _make_cmd(
            target="surrogate_tune",
            flow_dir=flow_dir,
            extra_make_args=[
                f"FLOW_VARIANT={calib_variant}",
                f"SURROGATE_SPACE_FILE={space_no_clock}",
                "SURROGATE_FREEZE=clock_period",
                "SURROGATE_RESET_CALIBRATION=1",
                f"SURROGATE_CALIBRATE_WS_FILE={ws_file}",
                f"SURROGATE_CALIBRATE_WL_FILE={wl_file}",
                "SURROGATE_SAMPLES=1",
                "SURROGATE_TOP_N=1",
            ],
            env=base_env,
        )
    _ensure_file(calib_out, "calibration surrogate_optimize.json")
    length_scale, timing_scale, ref_clock_ps = _extract_scales(calib_out)
    print(
        f"Calibration scales: builtin_length_scale={length_scale:.6g} "
        f"builtin_timing_scale={timing_scale:.6g} ref_clock_ps={ref_clock_ps:.6g}"
    )

    # 2) Determine clock candidates.
    clocks: List[float]
    if os.environ.get("SURROGATE_CLOCKS"):
        clocks = _parse_float_list(os.environ["SURROGATE_CLOCKS"])
    elif (
        os.environ.get("SURROGATE_CLOCK_MIN")
        or os.environ.get("SURROGATE_CLOCK_MAX")
        or os.environ.get("SURROGATE_CLOCK_STEP")
    ):
        clock_min = float(os.environ.get("SURROGATE_CLOCK_MIN", "300"))
        clock_max = float(os.environ.get("SURROGATE_CLOCK_MAX", "420"))
        clock_step = float(os.environ.get("SURROGATE_CLOCK_STEP", "10"))
        if clock_step <= 0:
            raise RuntimeError("SURROGATE_CLOCK_STEP must be > 0")
        n = int(round((clock_max - clock_min) / clock_step))
        clocks = [clock_min + i * clock_step for i in range(n + 1)]
    else:
        factors_s = os.environ.get("SURROGATE_CLOCK_FACTORS", "0.78 0.84 0.90 0.96")
        factors = [float(x) for x in _split_tokens(factors_s)]
        min_ps = float(os.environ["SURROGATE_CLOCK_MIN"]) if os.environ.get("SURROGATE_CLOCK_MIN") else None
        max_ps = float(os.environ["SURROGATE_CLOCK_MAX"]) if os.environ.get("SURROGATE_CLOCK_MAX") else None
        clocks = _auto_clock_candidates(
            base_clock_ps=base_clock_ps,
            baseline_ecp_ps=baseline_ecp_ps,
            factors=factors,
            min_ps=min_ps,
            max_ps=max_ps,
        )

    clocks.append(base_clock_ps)
    clocks = sorted({round(float(c), 3) for c in clocks if float(c) > 0.0})

    print(f"Clock sweep (ps): {', '.join(_clock_tag(c) for c in clocks)}")
    print(f"Samples/clock: {samples}, top_n/clock: {top_n}, global_top_n: {global_top_n}")

    clock_runs: List[ClockTuneRun] = []
    clock_constraints_dir = flow_dir / "tmp_surrogate" / base_variant
    clock_constraints_dir.mkdir(parents=True, exist_ok=True)

    # 3) Sweep clocks: re-synthesize per clock, then run fast surrogate optimization on that netlist.
    for clock_ps in clocks:
        tag = _clock_tag(clock_ps)
        variant = f"{base_variant}_clk{tag}"
        sdc_out = clock_constraints_dir / f"constraint_clk{tag}.sdc"
        _write_text(sdc_out, _rewrite_sdc_clock_period(base_sdc, clock_ps))

        out_json = _results_dir(flow_dir, platform, design, variant) / "surrogate_optimize.json"
        if resume and out_json.exists():
            print(f"Resume: reusing existing {out_json}")
            clock_runs.append(
                ClockTuneRun(
                    clock_ps=clock_ps,
                    tag=tag,
                    variant=variant,
                    sdc_file=sdc_out,
                    results_json=out_json,
                )
            )
            continue

        run_env = dict(base_env)
        run_env["SURROGATE_BUILTIN_LENGTH_SCALE"] = str(length_scale)
        run_env["SURROGATE_BUILTIN_TIMING_SCALE"] = str(timing_scale)
        run_env["SURROGATE_BUILTIN_REF_CLOCK_USER"] = str(ref_clock_ps)

        print(f"+ make surrogate_tune FLOW_VARIANT={variant} SDC_FILE={sdc_out}", flush=True)
        _make_cmd(
            target="surrogate_tune",
            flow_dir=flow_dir,
            extra_make_args=[
                f"FLOW_VARIANT={variant}",
                f"SDC_FILE={sdc_out}",
                f"SURROGATE_SPACE_FILE={space_no_clock}",
                "SURROGATE_FREEZE=clock_period",
                "SURROGATE_RESET_CALIBRATION=1",
                f"SURROGATE_SAMPLES={samples}",
                f"SURROGATE_TOP_N={top_n}",
            ],
            env=run_env,
        )

        _ensure_file(out_json, f"{variant} surrogate_optimize.json")
        clock_runs.append(
            ClockTuneRun(
                clock_ps=clock_ps,
                tag=tag,
                variant=variant,
                sdc_file=sdc_out,
                results_json=out_json,
            )
        )

    # 4) Merge results across clocks.
    merged_top: List[Dict[str, Any]] = []
    per_clock: List[Dict[str, Any]] = []
    for run in clock_runs:
        summary = _summarize_optimize_json(run.results_json)
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
    merged_top = merged_top[: max(1, global_top_n)]

    out_dir = _results_dir(flow_dir, platform, design, base_variant)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "surrogate_autotune.json"

    # 5) Optional validation: run full ORFS finish for top candidates.
    validations: List[ValidationRun] = []
    if _truthy(os.environ.get("SURROGATE_VALIDATE")):
        validate_n = int(os.environ.get("SURROGATE_VALIDATE_N", "3"))
        validate_n = max(1, validate_n)

        runs_by_tag = {r.tag: r for r in clock_runs}

        for idx, entry in enumerate(merged_top[:validate_n], start=1):
            cand = entry["candidate"]
            params = cand.get("params") or {}
            if not isinstance(params, dict):
                continue

            clock_ps = float(params.get("clock_period", entry["clock_ps"]))
            tag = _clock_tag(clock_ps)
            tune_run = runs_by_tag.get(tag)
            tune_variant = entry["variant"]
            sdc_file = tune_run.sdc_file if tune_run else clock_constraints_dir / f"constraint_clk{tag}.sdc"
            if not sdc_file.exists():
                _write_text(sdc_file, _rewrite_sdc_clock_period(base_sdc, clock_ps))

            validate_variant = f"{base_variant}_validate_clk{tag}_best{idx}"
            report_json = _logs_dir(flow_dir, platform, design, validate_variant) / "6_report.json"
            if resume and report_json.exists():
                ecp_ps = _compute_ecp_ps_from_report(report_json)
                fmax_hz = None
                if ecp_ps is not None:
                    fmax_hz = 1e12 / ecp_ps
                validations.append(
                    ValidationRun(
                        rank=idx,
                        clock_ps=clock_ps,
                        tag=tag,
                        tune_variant=tune_variant,
                        validate_variant=validate_variant,
                        sdc_file=sdc_file,
                        synth_netlist=None,
                        params=params,
                        report_json=report_json,
                        ecp_ps=ecp_ps,
                        fmax_hz=fmax_hz,
                    )
                )
                continue

            synth_netlist: Optional[Path] = None
            tune_results = _results_dir(flow_dir, platform, design, tune_variant)
            cand_netlist = tune_results / "1_synth.v"
            if cand_netlist.exists():
                synth_netlist = cand_netlist

            make_args = [
                f"FLOW_VARIANT={validate_variant}",
                f"SDC_FILE={sdc_file}",
            ]
            if synth_netlist is not None:
                make_args.append(f"SYNTH_NETLIST_FILES={synth_netlist}")
            make_args.extend(_orfs_var_assignments_from_params(params))

            print(f"+ make finish FLOW_VARIANT={validate_variant} (validate rank {idx})", flush=True)
            _make_cmd(target="finish", flow_dir=flow_dir, extra_make_args=make_args, env=base_env)

            ecp_ps = _compute_ecp_ps_from_report(report_json)
            fmax_hz = None
            try:
                data = _load_json(report_json)
                if "finish__timing__fmax" in data:
                    fmax_hz = float(data["finish__timing__fmax"])
            except FileNotFoundError:
                pass

            validations.append(
                ValidationRun(
                    rank=idx,
                    clock_ps=clock_ps,
                    tag=tag,
                    tune_variant=tune_variant,
                    validate_variant=validate_variant,
                    sdc_file=sdc_file,
                    synth_netlist=synth_netlist,
                    params=params,
                    report_json=report_json,
                    ecp_ps=ecp_ps,
                    fmax_hz=fmax_hz,
                )
            )

    validations_json: List[Dict[str, Any]] = []
    for v in validations:
        validations_json.append(
            {
                "rank": v.rank,
                "clock_ps": v.clock_ps,
                "variant": v.validate_variant,
                "tune_variant": v.tune_variant,
                "sdc_file": str(v.sdc_file),
                "synth_netlist": str(v.synth_netlist) if v.synth_netlist is not None else None,
                "report_json": str(v.report_json),
                "ecp_ps": v.ecp_ps,
                "fmax_hz": v.fmax_hz,
                "params": v.params,
            }
        )

    best_validated = None
    for v in validations_json:
        if v.get("ecp_ps") is None:
            continue
        if best_validated is None or float(v["ecp_ps"]) < float(best_validated["ecp_ps"]):
            best_validated = v

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
                "baseline_ecp_ps": baseline_ecp_ps,
                "builtin_length_scale": length_scale,
                "builtin_timing_scale": timing_scale,
                "builtin_ref_clock_ps": ref_clock_ps,
            },
            "per_clock": per_clock,
            "global_top": merged_top,
            "validation": {
                "enabled": _truthy(os.environ.get("SURROGATE_VALIDATE")),
                "candidates": validations_json,
                "best": best_validated,
            },
        },
    )

    print(f"Wrote summary: {summary_path}")
    if merged_top:
        best_pred = merged_top[0]
        c0 = best_pred["candidate"]
        print(
            "Best predicted: "
            f"obj={c0.get('objective')} variant={best_pred['variant']} params={c0.get('params')}"
        )
    if best_validated is not None:
        print(
            "Best validated: "
            f"ecp_ps={best_validated.get('ecp_ps')} variant={best_validated.get('variant')} "
            f"clock_ps={best_validated.get('clock_ps')}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
