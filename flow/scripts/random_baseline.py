#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_PLATFORMS = ("asap7", "nangate45", "sky130hd")
DEFAULT_DESIGNS = ("aes", "ibex", "jpeg")


@dataclass(frozen=True)
class ObjectiveRun:
    tag: str
    objective: str


OBJECTIVES: Dict[str, ObjectiveRun] = {
    "ecp": ObjectiveRun(tag="ecp", objective="effective_clock_period"),
    "wl": ObjectiveRun(tag="wl", objective="routed_wirelength"),
    "power": ObjectiveRun(tag="power", objective="power"),
    "instance_area": ObjectiveRun(tag="instance_area", objective="instance_area"),
    "area": ObjectiveRun(tag="area", objective="area"),
}


KNOB_MAP: Dict[str, str] = {
    "core_utilization": "CORE_UTILIZATION",
    "core_aspect_ratio": "CORE_ASPECT_RATIO",
    "tns_end_percent": "TNS_END_PERCENT",
    "place_density": "PLACE_DENSITY",
    "global_padding": "CELL_PAD_IN_SITES_GLOBAL_PLACEMENT",
    "detail_padding": "CELL_PAD_IN_SITES_DETAIL_PLACEMENT",
    "enable_dpo": "ENABLE_DPO",
    "pin_layer_adjust": "PIN_LAYER_ADJUST",
    "above_layer_adjust": "ABOVE_LAYER_ADJUST",
    "density_margin_addon": "PLACE_DENSITY_LB_ADDON",
    "cts_cluster_size": "CTS_CLUSTER_SIZE",
    "cts_cluster_diameter": "CTS_CLUSTER_DIAMETER",
}


FLOORPLAN_RESET_ARGS = ["FLOORPLAN_DEF=", "FOOTPRINT=", "DIE_AREA=", "CORE_AREA="]


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _split_csv(s: str) -> Tuple[str, ...]:
    items = [x.strip() for x in s.split(",")]
    return tuple(x for x in items if x)


def _split_tokens(s: str) -> Tuple[str, ...]:
    return tuple(x for x in re.split(r"\s+", s.strip()) if x)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _clock_tag(clock_sdc: float) -> str:
    s = f"{clock_sdc:.3f}".rstrip("0").rstrip(".")
    return s.replace(".", "p")


def _infer_clock_from_sdc(sdc_path: Path) -> Optional[float]:
    text = sdc_path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"(?m)^\s*set\s+clk_period\s+([0-9]*\.?[0-9]+)\s*$", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(?m)create_clock\b.*?-period\s+([0-9]*\.?[0-9]+)\b", text)
    if m:
        return float(m.group(1))
    return None


def _rewrite_sdc_clock_period(base_sdc: Path, clock_sdc: float) -> str:
    text = base_sdc.read_text(encoding="utf-8", errors="ignore")
    replacement = f"set clk_period {clock_sdc}"
    if re.search(r"(?m)^\s*set\s+clk_period\s+", text):
        return re.sub(r"(?m)^\s*set\s+clk_period\s+\S+.*$", replacement, text)
    if re.search(r"(?m)create_clock\b.*?-period\s+\S+", text):
        return re.sub(r"(?m)(create_clock\b.*?-period)\s+\S+", rf"\1 {clock_sdc}", text)
    raise RuntimeError(f"Could not find clk_period or create_clock -period in {base_sdc}")


def _infer_sdc_units_ps(*, base_clock_sdc: float, baseline_ecp_ps: Optional[float]) -> Optional[float]:
    if baseline_ecp_ps is None or baseline_ecp_ps <= 0 or base_clock_sdc <= 0:
        return None
    ratio = float(baseline_ecp_ps) / float(base_clock_sdc)
    return 1000.0 if ratio > 100.0 else 1.0


def _parse_ecp_ps_from_report(report_json: Path) -> Optional[float]:
    try:
        data = _load_json(report_json)
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


def _parse_wirelength(route_json: Path) -> Optional[float]:
    try:
        data = _load_json(route_json)
    except Exception:
        return None
    wl = _as_float(data.get("detailedroute__route__wirelength"))
    if wl is None:
        wl = _as_float(data.get("globalroute__route__wirelength__estimated"))
    return wl


def _parse_total_power(report_json: Path) -> Optional[float]:
    try:
        data = _load_json(report_json)
    except Exception:
        return None
    return _as_float(data.get("finish__power__total"))


def _parse_instance_area(report_json: Path) -> Optional[float]:
    try:
        data = _load_json(report_json)
    except Exception:
        return None
    v = _as_float(data.get("finish__design__instance__area"))
    if v is None:
        v = _as_float(data.get("finish__design__instance__area__stdcell"))
    return v


def _parse_core_area(report_json: Path) -> Optional[float]:
    try:
        data = _load_json(report_json)
    except Exception:
        return None
    return _as_float(data.get("finish__design__core__area"))


def _objective_from_metrics(*, objective: str, report_json: Path, route_json: Path) -> Optional[float]:
    if objective == "effective_clock_period":
        return _parse_ecp_ps_from_report(report_json)
    if objective == "routed_wirelength":
        return _parse_wirelength(route_json)
    if objective == "power":
        return _parse_total_power(report_json)
    if objective == "instance_area":
        return _parse_instance_area(report_json)
    if objective == "area":
        return _parse_core_area(report_json)
    raise ValueError(f"Unsupported objective: {objective}")


def _make_print(*, flow_dir: Path, design_config: str, name: str, env: Dict[str, str]) -> str:
    cmd = ["make", "--no-print-directory", f"print-{name}", f"DESIGN_CONFIG={design_config}"]
    out = subprocess.check_output(cmd, cwd=str(flow_dir), env=env, text=True, stderr=subprocess.STDOUT)
    m = re.search(rf"(?m)^\s*{re.escape(name)}\s*=\s*(.*)$", out)
    if not m:
        return ""
    return m.group(1).strip()


def _pick_space_file(*, design_dir: Path, objective: str, explicit: str) -> Path:
    if explicit.strip():
        return Path(explicit)

    if objective == "routed_wirelength":
        wl_safe = design_dir / "surrogate_space_wl_safe.json"
        if wl_safe.exists():
            return wl_safe

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
    try:
        space = _load_json(space_file)
    except Exception:
        return False
    return isinstance(space, dict) and ("clock_period" in space)


def _clock_spec_from_space(space_file: Path) -> Optional[Tuple[float, float, float]]:
    try:
        space = _load_json(space_file)
    except Exception:
        return None
    if not isinstance(space, dict):
        return None
    spec = space.get("clock_period")
    if not isinstance(spec, dict):
        return None
    mm = spec.get("minmax")
    if not (isinstance(mm, list) and len(mm) == 2):
        return None
    try:
        vmin = float(mm[0])
        vmax = float(mm[1])
        step = float(spec.get("step") or 0.0)
    except (TypeError, ValueError):
        return None
    if vmax < vmin:
        vmin, vmax = vmax, vmin
    return vmin, vmax, step


def _linspace(lo: float, hi: float, n: int) -> List[float]:
    if n <= 1:
        return [float(lo)]
    step = (float(hi) - float(lo)) / float(n - 1)
    return [float(lo) + i * step for i in range(n)]


def _clock_candidates_from_spec(*, spec: Tuple[float, float, float], n: int) -> List[float]:
    vmin, vmax, step = spec
    if not (vmax > 0.0 and vmin > 0.0):
        return []
    n = max(1, int(n))
    if step and step > 0:
        count = int(math.floor((vmax - vmin) / step)) + 1
        if count <= 1 or n <= 1:
            return [float(vmin)]
        values = [float(vmin + i * step) for i in range(count)]
        if len(values) <= n:
            return values
        idxs = [int(round(i * (len(values) - 1) / (n - 1))) for i in range(n)]
        return [values[i] for i in idxs]
    return _linspace(vmin, vmax, n)


def _space_spec(space_file: Path) -> Dict[str, Dict[str, Any]]:
    data = _load_json(space_file)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid space file (expected dict): {space_file}")
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        out[str(k)] = v
    return out


def _sample_value(
    *,
    spec: Dict[str, Any],
    baseline: Optional[float],
    rng: random.Random,
    radius_frac: float,
    flip_prob: float,
) -> float:
    typ = str(spec.get("type") or "").strip().lower()
    mm = spec.get("minmax") or []
    if not (isinstance(mm, list) and len(mm) == 2):
        raise RuntimeError(f"Invalid knob spec minmax: {spec}")
    vmin = float(mm[0])
    vmax = float(mm[1])
    step = _as_float(spec.get("step"))
    if step is None:
        step = 0.0

    if typ == "binary":
        base = 0 if baseline is None else int(round(float(baseline)))
        base = 1 if base else 0
        if rng.random() < flip_prob:
            base = 1 - base
        return float(base)

    if baseline is None or math.isnan(baseline):
        baseline = 0.5 * (vmin + vmax)

    # Clamp baseline into the spec range.
    baseline = float(min(vmax, max(vmin, baseline)))
    span = float(vmax - vmin)
    radius = span * float(max(0.0, min(1.0, radius_frac)))
    lo = max(vmin, baseline - radius)
    hi = min(vmax, baseline + radius)
    if hi < lo:
        lo, hi = hi, lo

    if step and step > 0:
        k_lo = int(math.ceil((lo - vmin) / step))
        k_hi = int(math.floor((hi - vmin) / step))
        if k_hi < k_lo:
            k_lo = k_hi
        k = rng.randint(k_lo, k_hi) if k_hi >= k_lo else k_lo
        return float(vmin + k * step)

    return float(rng.uniform(lo, hi))


def _format_make_value(v: Any, *, want_int: bool) -> str:
    if want_int:
        try:
            return str(int(round(float(v))))
        except Exception:
            return str(v)
    if isinstance(v, bool):
        return "1" if v else "0"
    try:
        fv = float(v)
        # Keep CLI output stable and avoid scientific notation.
        return f"{fv:.9f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def _orfs_make_args_from_params(params: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if "core_utilization" in params:
        out.extend(FLOORPLAN_RESET_ARGS)
    for k, v in params.items():
        if k == "clock_period":
            continue
        env_k = KNOB_MAP.get(k)
        if not env_k:
            continue
        want_int = k in {
            "core_utilization",
            "tns_end_percent",
            "global_padding",
            "detail_padding",
            "enable_dpo",
            "cts_cluster_size",
        }
        out.append(f"{env_k}={_format_make_value(v, want_int=want_int)}")
    return out


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
            proc.kill()
            elapsed = time.monotonic() - start
            return f"timeout_after_{timeout_s:.0f}s", elapsed

        rc = proc.returncode
        elapsed = time.monotonic() - start
        if rc != 0:
            return f"exit_{rc}", elapsed
        return None, elapsed


def _best_of(cands: Iterable[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_v: Optional[float] = None
    for c in cands:
        if c.get("error"):
            continue
        v = _as_float(c.get(key))
        if v is None:
            continue
        if best_v is None or v < best_v:
            best_v = v
            best = c
    return best


def _best_objective_from_surrogate_summary(*, summary: Dict[str, Any], objective: str) -> Optional[float]:
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
    if objective == "power":
        return _as_float(best.get("total_power"))
    if objective == "instance_area":
        return _as_float(best.get("instance_area"))
    if objective == "area":
        return _as_float(best.get("core_area"))
    return None


def run_random_baseline_for_design(
    *,
    repo_root: Path,
    flow_dir: Path,
    platform: str,
    design: str,
    design_config: str,
    objective: str,
    variant_prefix: str,
    space_file: Path,
    n: int,
    jobs: int,
    num_cores: int,
    radius_frac: float,
    flip_prob: float,
    clock_factors: Sequence[float],
    clock_strategy: str,
    clock_sweep_n: int,
    seed: int,
    make_target: str,
    resume: bool,
    bench_dir: Path,
    synth_from_variant: str,
    compare_to_variant: str,
    timeout_s: Optional[float],
) -> Dict[str, Any]:
    design_dir = flow_dir / "designs" / platform / design
    space = _space_spec(space_file)

    env_base = os.environ.copy()
    env_base["DESIGN_CONFIG"] = design_config
    env_base["NUM_CORES"] = str(num_cores)
    if shutil.which("eqy") is None:
        env_base.setdefault("EQUIVALENCE_CHECK", "0")

    # Ensure toolchain paths are valid even if this repo clone doesn't have tools/ installed.
    env_base.setdefault("OPENROAD_EXE", str((repo_root / "tools/install/OpenROAD/bin/openroad").resolve()))
    env_base.setdefault("YOSYS_EXE", str((repo_root / "tools/install/yosys/bin/yosys").resolve()))
    if not Path(env_base["OPENROAD_EXE"]).exists():
        fallback = Path("/home/aghose/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad")
        if fallback.exists():
            env_base["OPENROAD_EXE"] = str(fallback)
    if not Path(env_base["YOSYS_EXE"]).exists():
        fallback = Path("/home/aghose/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys")
        if fallback.exists():
            env_base["YOSYS_EXE"] = str(fallback)

    sdc_s = _make_print(flow_dir=flow_dir, design_config=design_config, name="SDC_FILE", env=env_base)
    base_sdc = (flow_dir / sdc_s).resolve() if sdc_s else None
    if base_sdc is None or not base_sdc.exists():
        raise FileNotFoundError(f"Could not resolve SDC_FILE for {platform}/{design}: {sdc_s}")

    base_clock_sdc = _infer_clock_from_sdc(base_sdc)
    if base_clock_sdc is None:
        raise RuntimeError(f"Could not infer base clock from {base_sdc}")

    ws_file = flow_dir / "logs" / platform / design / "base" / "6_report.json"
    wl_file = flow_dir / "logs" / platform / design / "base" / "5_2_route.json"
    if not ws_file.exists() or not wl_file.exists():
        raise FileNotFoundError(f"Missing baseline metrics for {platform}/{design}: {ws_file} {wl_file}")

    baseline_obj: Optional[float]
    if objective == "effective_clock_period":
        baseline_obj = _parse_ecp_ps_from_report(ws_file)
    else:
        baseline_obj = _parse_wirelength(wl_file)

    baseline_vars: Dict[str, Optional[float]] = {}
    for knob, spec in space.items():
        if knob == "clock_period":
            baseline_vars[knob] = base_clock_sdc
            continue
        orfs_var = KNOB_MAP.get(knob)
        if not orfs_var:
            continue
        s = _make_print(flow_dir=flow_dir, design_config=design_config, name=orfs_var, env=env_base)
        baseline_vars[knob] = _as_float(s) if s else None

    # Decide which clocks to use (only meaningful when the space includes clock_period).
    clocks: List[float] = [base_clock_sdc]
    if _space_has_clock(space_file):
        clk_spec = _clock_spec_from_space(space_file)
        if objective == "effective_clock_period":
            clock_strategy_norm = (clock_strategy or "").strip().lower() or "space"
            if clock_strategy_norm not in {"space", "factors"}:
                raise RuntimeError(f"Invalid clock strategy: {clock_strategy!r} (expected 'space' or 'factors')")

            if clock_strategy_norm == "space" and clk_spec is not None:
                spec_min, spec_max, spec_step = clk_spec
                clocks = _clock_candidates_from_spec(spec=(spec_min, spec_max, spec_step), n=max(1, int(clock_sweep_n)))
                if clocks and (spec_min <= float(base_clock_sdc) <= spec_max):
                    idx = min(range(len(clocks)), key=lambda i: abs(float(clocks[i]) - float(base_clock_sdc)))
                    clocks[idx] = float(base_clock_sdc)
            else:
                if baseline_obj is not None:
                    sdc_units_ps = _infer_sdc_units_ps(base_clock_sdc=base_clock_sdc, baseline_ecp_ps=baseline_obj)
                    if sdc_units_ps is not None and sdc_units_ps > 0:
                        baseline_ecp_sdc = baseline_obj / sdc_units_ps
                        for f in clock_factors:
                            clocks.append(float(baseline_ecp_sdc) * float(f))

        # Clamp and dedup within the space bounds.
        spec_min = spec_max = None
        if clk_spec is not None:
            spec_min, spec_max, _ = clk_spec
        clk_min = spec_min
        clk_max = spec_max
        uniq: List[float] = []
        seen: set[float] = set()
        for c in sorted(clocks):
            if not (c > 0):
                continue
            if clk_min is not None and c < clk_min:
                continue
            if clk_max is not None and c > clk_max:
                continue
            key = round(float(c), 3)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(float(key))
        clocks = uniq or [base_clock_sdc]

    rng = random.Random(seed)
    want_clock = _space_has_clock(space_file)
    tmp_sdc_dir = flow_dir / "tmp_random_baseline" / variant_prefix
    tmp_sdc_dir.mkdir(parents=True, exist_ok=True)

    def synth_netlist_for_clock(clock_sdc: float) -> Optional[Path]:
        if not synth_from_variant:
            return None
        tag = _clock_tag(clock_sdc)
        tune_variant = f"{synth_from_variant}_clk{tag}"
        cand = flow_dir / "results" / platform / design / tune_variant / "1_2_yosys.v"
        if cand.exists():
            return cand
        cand = flow_dir / "results" / platform / design / synth_from_variant / "1_2_yosys.v"
        return cand if cand.exists() else None

    # Generate N unique random candidates (params + optional clock choice).
    attempts = 0
    max_attempts = max(1000, 50 * n)
    candidates: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    clock_plan: List[float] = []
    if want_clock:
        clock_plan = list(clocks)
    while len(candidates) < n and attempts < max_attempts:
        attempts += 1
        if want_clock and clock_plan:
            clock_sdc = float(clock_plan.pop(0))
        else:
            clock_sdc = float(rng.choice(clocks)) if want_clock else base_clock_sdc
        params: Dict[str, Any] = {}
        for knob, spec in space.items():
            if knob == "clock_period":
                continue
            typ = str(spec.get("type") or "").strip().lower()
            want_int = typ == "int"
            base = baseline_vars.get(knob)
            v = _sample_value(spec=spec, baseline=base, rng=rng, radius_frac=radius_frac, flip_prob=flip_prob)
            if want_int:
                v = int(round(v))
            params[knob] = v

        key = json.dumps({"clock": round(clock_sdc, 6), "params": params}, sort_keys=True)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append({"clock_sdc": clock_sdc, "params": params})

    if len(candidates) < n:
        raise RuntimeError(f"Could only generate {len(candidates)}/{n} unique random candidates (attempts={attempts})")

    make_target_norm = (make_target or "report").strip().lower()

    def run_one(idx: int, cand: Dict[str, Any]) -> Dict[str, Any]:
        clock_sdc = float(cand["clock_sdc"])
        clock_tag = _clock_tag(clock_sdc)
        variant = f"{variant_prefix}_v{idx:02d}" + (f"_clk{clock_tag}" if want_clock else "")
        logs_dir = flow_dir / "logs" / platform / design / variant
        report_json = logs_dir / "6_report.json"
        route_json = logs_dir / "5_2_route.json"
        log_path = bench_dir / platform / design / f"{variant}.log"
        needs_route = objective == "routed_wirelength"

        out: Dict[str, Any] = {
            "rank": idx,
            "variant": variant,
            "clock_sdc": clock_sdc,
            "clock_tag": clock_tag,
            "sdc_file": None,
            "synth_netlist": None,
            "params": cand["params"],
            "objective": None,
            "error": None,
            "wall_time_s": None,
            "log": str(log_path),
            "report_json": str(report_json),
            "route_json": str(route_json),
        }

        # Resume if the metrics are already present.
        if resume and report_json.exists() and (route_json.exists() or not needs_route):
            out["objective"] = _objective_from_metrics(objective=objective, report_json=report_json, route_json=route_json)
            return out

        sdc_path = base_sdc
        if want_clock:
            sdc_path = tmp_sdc_dir / f"constraint_clk{clock_tag}_v{idx:02d}.sdc"
            sdc_path.write_text(_rewrite_sdc_clock_period(base_sdc, clock_sdc), encoding="utf-8")
        out["sdc_file"] = str(sdc_path)

        netlist = synth_netlist_for_clock(clock_sdc)
        if netlist is not None:
            out["synth_netlist"] = str(netlist)

        make_args = [f"FLOW_VARIANT={variant}", f"SDC_FILE={sdc_path}"]
        if netlist is not None:
            make_args.append(f"SYNTH_NETLIST_FILES={netlist}")
        make_args.extend(_orfs_make_args_from_params(cand["params"]))

        if make_target_norm in {"report", "6_report", "report_only", "do-6_report"}:
            target = f"logs/{platform}/{design}/{variant}/6_report.log"
        else:
            target = make_target
        cmd = ["make", "--no-print-directory", target, *make_args]

        err, elapsed = _run_checked(cmd=cmd, cwd=flow_dir, env=env_base, log_path=log_path, timeout_s=timeout_s)
        out["error"] = err
        out["wall_time_s"] = elapsed
        if err is None and report_json.exists() and (route_json.exists() or not needs_route):
            out["objective"] = _objective_from_metrics(objective=objective, report_json=report_json, route_json=route_json)
        return out

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = [ex.submit(run_one, i + 1, c) for i, c in enumerate(candidates)]
        for fut in as_completed(futs):
            results.append(fut.result())
    results.sort(key=lambda r: int(r.get("rank", 1_000_000)))

    best = _best_of(results, "objective")
    best_obj = _as_float((best or {}).get("objective"))
    improve_pct = None
    if baseline_obj is not None and best_obj is not None and baseline_obj != 0:
        improve_pct = (baseline_obj - best_obj) / baseline_obj * 100.0

    surrogate_best_obj: Optional[float] = None
    surrogate_improve_pct: Optional[float] = None
    gain_ratio: Optional[float] = None
    surrogate_summary_path: Optional[Path] = None
    if compare_to_variant:
        surrogate_summary_path = flow_dir / "results" / platform / design / compare_to_variant / "surrogate_autotune.json"
        if surrogate_summary_path.exists():
            try:
                surrogate_summary = _load_json(surrogate_summary_path)
                surrogate_best_obj = _best_objective_from_surrogate_summary(
                    summary=surrogate_summary, objective=objective
                )
                if baseline_obj is not None and surrogate_best_obj is not None and baseline_obj != 0:
                    surrogate_improve_pct = (baseline_obj - surrogate_best_obj) / baseline_obj * 100.0
            except Exception:
                pass

    if improve_pct is not None and surrogate_improve_pct is not None and improve_pct > 0 and surrogate_improve_pct > 0:
        gain_ratio = float(surrogate_improve_pct) / float(improve_pct)

    summary = {
        "design": {"platform": platform, "design": design},
        "objective": objective,
        "space_file": str(space_file),
        "random": {
            "n": n,
            "jobs": jobs,
            "num_cores": num_cores,
            "radius_frac": radius_frac,
            "flip_prob": flip_prob,
            "seed": seed,
            "clock_factors": list(clock_factors),
            "clocks": clocks,
            "candidates": results,
            "best": best,
            "best_objective": best_obj,
            "baseline_objective": baseline_obj,
            "improve_pct": improve_pct,
        },
        "surrogate_reference": {
            "flow_variant": compare_to_variant or None,
            "summary_json": str(surrogate_summary_path) if surrogate_summary_path is not None else None,
            "best_objective": surrogate_best_obj,
            "improve_pct": surrogate_improve_pct,
            "gain_ratio": gain_ratio,
        },
        "baseline": {
            "ws_file": str(ws_file),
            "wl_file": str(wl_file),
            "base_sdc": str(base_sdc),
            "base_clock_sdc": base_clock_sdc,
            "baseline_vars": baseline_vars,
        },
    }

    out_dir = flow_dir / "results" / platform / design / variant_prefix
    _dump_json(out_dir / "random_baseline.json", summary)
    return {
        "platform": platform,
        "design": design,
        "objective": objective,
        "flow_variant": variant_prefix,
        "status": "ok" if best_obj is not None else "error",
        "baseline_objective": baseline_obj,
        "best_objective": best_obj,
        "improve_pct": improve_pct,
        "surrogate_best_objective": surrogate_best_obj,
        "surrogate_improve_pct": surrogate_improve_pct,
        "gain_ratio": gain_ratio,
        "surrogate_flow_variant": compare_to_variant or None,
        "best_variant": (best or {}).get("variant"),
        "summary_json": str(out_dir / "random_baseline.json"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Random-search baseline: sample random perturbations around the ORFS baseline and keep the best of N validations."
    )
    ap.add_argument("--platforms", default=",".join(DEFAULT_PLATFORMS), help="Comma-separated platforms.")
    ap.add_argument("--designs", default=",".join(DEFAULT_DESIGNS), help="Comma-separated designs.")
    ap.add_argument(
        "--objectives",
        default="ecp,wl",
        help="Comma-separated objective tags: ecp,wl,power,instance_area,area.",
    )

    ap.add_argument("--validate-n", type=int, default=18, help="N random candidates per design/objective.")
    ap.add_argument("--validate-jobs", type=int, default=18, help="Parallel jobs per design/objective.")
    ap.add_argument("--num-cores", type=int, default=6, help="Threads per ORFS job (NUM_CORES).")
    ap.add_argument("--radius-frac", type=float, default=0.20, help="Local perturbation radius as a fraction of knob span.")
    ap.add_argument("--flip-prob", type=float, default=0.25, help="Flip probability for binary knobs.")

    ap.add_argument(
        "--clock-factors",
        default="0.78 0.84 0.90 0.96",
        help="Clock sweep factors used to pick random clocks (effective_clock_period only).",
    )
    ap.add_argument(
        "--clock-strategy",
        default="space",
        choices=["space", "factors"],
        help="Clock selection strategy for effective_clock_period when clock_period is present in the space.",
    )
    ap.add_argument(
        "--clock-sweep-n",
        type=int,
        default=5,
        help="Number of clock points for --clock-strategy=space.",
    )

    ap.add_argument("--variant-prefix", default=f"bench_randbase_{_timestamp()}", help="FLOW_VARIANT prefix.")
    ap.add_argument("--out", default="", help="Output CSV path (default: repo root).")
    ap.add_argument("--bench-dir", default="", help="Bench log dir (default: repo_root/bench_logs/<variant-prefix>).")

    ap.add_argument(
        "--make-target",
        default="report",
        help="Validation target: 'report' (default, runs up to 6_report) or 'finish'.",
    )
    ap.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True, help="Reuse existing outputs.")
    ap.add_argument("--timeout-s", type=int, default=0, help="Per-candidate timeout seconds (0 => none).")

    ap.add_argument(
        "--synth-from-variant",
        default="",
        help="Optional FLOW_VARIANT prefix to reuse synthesized netlists from (expects <variant>_clk<tag>/1_2_yosys.v).",
    )
    ap.add_argument(
        "--compare-to-variant-ecp",
        default="",
        help="Optional ECP surrogate FLOW_VARIANT to compare against (loads results/<p>/<d>/<variant>/surrogate_autotune.json).",
    )
    ap.add_argument(
        "--compare-to-variant-wl",
        default="",
        help="Optional WL surrogate FLOW_VARIANT to compare against (loads results/<p>/<d>/<variant>/surrogate_autotune.json).",
    )
    ap.add_argument(
        "--compare-to-variant-power",
        default="",
        help="Optional power surrogate FLOW_VARIANT to compare against (loads results/<p>/<d>/<variant>/surrogate_autotune.json).",
    )
    ap.add_argument(
        "--compare-to-variant-instance-area",
        default="",
        help="Optional instance-area surrogate FLOW_VARIANT to compare against (loads results/<p>/<d>/<variant>/surrogate_autotune.json).",
    )
    ap.add_argument(
        "--compare-to-variant-area",
        default="",
        help="Optional area surrogate FLOW_VARIANT to compare against (loads results/<p>/<d>/<variant>/surrogate_autotune.json).",
    )
    ap.add_argument("--seed", type=int, default=0, help="Base RNG seed (0 => time-based).")

    args = ap.parse_args(list(argv) if argv is not None else None)

    platforms = _split_csv(args.platforms)
    designs = _split_csv(args.designs)
    obj_tags = _split_csv(args.objectives)
    objectives: List[ObjectiveRun] = []
    for t in obj_tags:
        if t not in OBJECTIVES:
            raise SystemExit(f"ERROR: unsupported objective tag {t!r} (expected one of {sorted(OBJECTIVES)})")
        objectives.append(OBJECTIVES[t])

    script_dir = Path(__file__).resolve().parent
    flow_dir = script_dir.parent
    repo_root = flow_dir.parent
    if not (flow_dir / "Makefile").exists():
        raise SystemExit(f"ERROR: expected {flow_dir}/Makefile")

    run_id = args.variant_prefix
    out_csv = Path(args.out) if args.out else (repo_root / f"random_baseline_perf_{run_id}.csv")
    bench_dir = Path(args.bench_dir) if args.bench_dir else (repo_root / "bench_logs" / run_id)

    base_seed = int(args.seed) if int(args.seed) != 0 else int(time.time())
    clock_factors = [float(x) for x in _split_tokens(args.clock_factors)]
    clock_strategy = str(args.clock_strategy)
    clock_sweep_n = int(args.clock_sweep_n)
    timeout_s = float(args.timeout_s) if int(args.timeout_s) > 0 else None

    rows: List[Dict[str, Any]] = []
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "run_id",
                "platform",
                "design",
                "objective",
                "flow_variant",
                "baseline_objective",
                "best_objective",
                "improve_pct",
                "surrogate_flow_variant",
                "surrogate_best_objective",
                "surrogate_improve_pct",
                "gain_ratio",
                "best_variant",
                "status",
                "summary_json",
            ],
        )
        w.writeheader()

        for platform in platforms:
            for design in designs:
                design_config = str(Path("designs") / platform / design / "config.mk")
                for obj in objectives:
                    variant_prefix = f"{run_id}_{obj.tag}"
                    compare_variant = ""
                    if obj.tag == "ecp":
                        compare_variant = str(args.compare_to_variant_ecp)
                    elif obj.tag == "wl":
                        compare_variant = str(args.compare_to_variant_wl)
                    elif obj.tag == "power":
                        compare_variant = str(args.compare_to_variant_power)
                    elif obj.tag == "instance_area":
                        compare_variant = str(args.compare_to_variant_instance_area)
                    elif obj.tag == "area":
                        compare_variant = str(args.compare_to_variant_area)
                    design_dir = flow_dir / "designs" / platform / design
                    space_file = _pick_space_file(
                        design_dir=design_dir, objective=obj.objective, explicit=os.environ.get("SURROGATE_SPACE_FILE", "")
                    )

                    # Derive a stable per-(platform,design,objective) seed.
                    tag_bytes = f"{base_seed}:{platform}:{design}:{obj.tag}".encode("utf-8")
                    tag_seed = int.from_bytes(hashlib.sha1(tag_bytes).digest()[:4], "big")
                    try:
                        r = run_random_baseline_for_design(
                            repo_root=repo_root,
                            flow_dir=flow_dir,
                            platform=platform,
                            design=design,
                            design_config=design_config,
                            objective=obj.objective,
                            variant_prefix=variant_prefix,
                            space_file=space_file,
                            n=max(1, int(args.validate_n)),
                            jobs=max(1, int(args.validate_jobs)),
                            num_cores=max(1, int(args.num_cores)),
                            radius_frac=float(args.radius_frac),
                            flip_prob=float(args.flip_prob),
                            clock_factors=clock_factors,
                            clock_strategy=clock_strategy,
                            clock_sweep_n=clock_sweep_n,
                            seed=int(tag_seed),
                            make_target=str(args.make_target),
                            resume=bool(args.resume),
                            bench_dir=bench_dir,
                            synth_from_variant=str(args.synth_from_variant),
                            compare_to_variant=compare_variant,
                            timeout_s=timeout_s,
                        )
                    except Exception as e:
                        r = {
                            "platform": platform,
                            "design": design,
                            "objective": obj.objective,
                            "flow_variant": variant_prefix,
                            "status": "error",
                            "baseline_objective": None,
                            "best_objective": None,
                            "improve_pct": None,
                            "best_variant": None,
                            "summary_json": None,
                            "error": f"{type(e).__name__}: {e}",
                        }
                    r["run_id"] = run_id
                    rows.append(r)
                    w.writerow({k: r.get(k) for k in w.fieldnames})
                    f.flush()

    print(f"Done. Wrote {out_csv}")
    for r in rows:
        imp = r.get("improve_pct")
        imp_s = "n/a" if imp is None else f"{imp:+.3f}%"
        print(f"{r.get('platform')}/{r.get('design')} {r.get('objective')} improve={imp_s} status={r.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
