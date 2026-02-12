#!/usr/bin/env python3
import hashlib
import math
import json
import os
import random
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
class SurrogateCandidate:
    rank: int  # 1-based rank in merged_top
    clock_ps: float
    tag: str
    tune_variant: str
    sdc_file: Path
    params: Dict[str, Any]
    predicted_objective: float
    predicted_outputs: Dict[str, Any]
    predicted_features: Dict[str, Any]


@dataclass(frozen=True)
class RouteValidation:
    rank: int
    candidate: SurrogateCandidate
    variant: str
    sdc_file: Path
    synth_netlist: Optional[Path]
    params: Dict[str, Any]
    route_json: Path


@dataclass(frozen=True)
class FinishValidation:
    rank: int
    candidate: SurrogateCandidate
    variant: str
    sdc_file: Path
    synth_netlist: Optional[Path]
    params: Dict[str, Any]
    route_json: Path
    report_json: Path


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
        return int(v)
    except (TypeError, ValueError):
        return None


def _ceil_quantile(scores: Sequence[float], alpha: float) -> float:
    if not scores:
        raise ValueError("Empty conformal scores")
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0,1), got {alpha}")
    n = len(scores)
    k = math.ceil((n + 1) * (1.0 - alpha))
    k = max(1, min(k, n))
    return sorted(scores)[k - 1]


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


def _compute_ecp_ps_from_route(route_json: Path) -> Optional[float]:
    try:
        data = _load_json(route_json)
    except FileNotFoundError:
        return None

    fmax = data.get("detailedroute__timing__fmax")
    if fmax is not None:
        try:
            fmax_hz = float(fmax)
            if fmax_hz > 0:
                return 1e12 / fmax_hz
        except (TypeError, ValueError):
            pass

    return None


def _infer_sdc_units_ps_from_calibration(calib: Dict[str, Any]) -> float:
    direct = _as_float(calib.get("sdc_units_ps"))
    if direct is not None and direct > 0:
        return direct

    base_ps = _as_float(calib.get("baseline_ecp_ps"))
    base_sdc = _as_float(calib.get("baseline_ecp_sdc_units"))
    if base_ps is not None and base_sdc is not None and base_ps > 0 and base_sdc > 0:
        return base_ps / base_sdc

    return 1000.0


def _infer_current_sdc_units_ps(*, base_clock_sdc: float, baseline_ecp_ps: Optional[float]) -> Optional[float]:
    env = _as_float(os.environ.get("SURROGATE_SDC_UNITS_PS"))
    if env is not None and env > 0:
        return env

    if baseline_ecp_ps is None or baseline_ecp_ps <= 0:
        return None
    if base_clock_sdc <= 0:
        return None

    # Heuristic: in this repo, SDC clock periods are typically in either ps-scale
    # (e.g. 1000) or ns-scale (e.g. 10). Use the baseline ECP to infer the unit.
    ratio = float(baseline_ecp_ps) / float(base_clock_sdc)
    if ratio > 100.0:
        return 1000.0
    return 1.0


def _sigma_from_features(*, features: Dict[str, Any], scheme: str, fail_risk_c0: float) -> float:
    eps = 1e-9
    if scheme == "constant":
        return 1.0
    if scheme == "fail_risk":
        fr = _as_float(features.get("surrogate_fail_risk")) or 0.0
        return max(eps, fr + fail_risk_c0)
    if scheme == "1+surrogate_power":
        sp = _as_float(features.get("surrogate_power")) or 0.0
        return max(eps, 1.0 + sp)
    if scheme == "log1p_hpwl_est":
        hpwl = _as_float(features.get("surrogate_hpwl_est")) or 0.0
        return max(eps, math.log1p(max(0.0, hpwl)))
    raise ValueError(f"Unknown sigma scheme: {scheme}")


def _historical_conformal_q(
    *,
    flow_dir: Path,
    objective: str,
    alpha: float,
    sigma_scheme: str,
    fail_risk_c0: float,
) -> Optional[float]:
    results_root = flow_dir / "results"
    scores: List[float] = []

    for path in results_root.glob("**/surrogate_autotune.json"):
        try:
            data = _load_json(path)
        except Exception:
            continue

        tuning = data.get("tuning") or {}
        if tuning.get("objective") != objective:
            continue

        calib = data.get("calibration") or {}
        sdc_units_ps = _infer_sdc_units_ps_from_calibration(calib)

        validation = data.get("validation") or {}
        candidates = validation.get("candidates") or []
        if not isinstance(candidates, list):
            continue

        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            if cand.get("error"):
                continue

            pred = _as_float(cand.get("predicted_objective"))
            feats = cand.get("predicted_features") or {}
            if pred is None or not isinstance(feats, dict):
                continue

            sigma = _sigma_from_features(features=feats, scheme=sigma_scheme, fail_risk_c0=fail_risk_c0)

            if objective == "effective_clock_period":
                actual_ps = _as_float(cand.get("ecp_ps"))
                if actual_ps is None:
                    continue
                pred_ps = pred * sdc_units_ps
                scores.append(abs(actual_ps - pred_ps) / sigma)
            elif objective == "routed_wirelength":
                actual = _as_float(cand.get("routed_wl"))
                if actual is None:
                    continue
                scores.append(abs(actual - pred) / sigma)
            elif objective == "power":
                actual = _as_float(cand.get("total_power"))
                if actual is None:
                    continue
                scores.append(abs(actual - pred) / sigma)
            elif objective == "instance_area":
                actual = _as_float(cand.get("instance_area"))
                if actual is None:
                    continue
                scores.append(abs(actual - pred) / sigma)
            else:
                continue

    if len(scores) < 20:
        return None

    return _ceil_quantile(scores, alpha)


def _select_portfolio(
    *,
    candidates: Sequence[SurrogateCandidate],
    n: int,
    q: float,
    sigma_scheme: str,
    fail_risk_c0: float,
    mean_frac: float = 0.7,
    ucb_frac: float = 0.2,
    lcb_frac: float = 0.1,
) -> List[SurrogateCandidate]:
    if n <= 0:
        return []

    # Normalize portfolio fractions (defaults bias toward exploitation).
    mean_frac = max(0.0, float(mean_frac))
    ucb_frac = max(0.0, float(ucb_frac))
    lcb_frac = max(0.0, float(lcb_frac))
    total = mean_frac + ucb_frac + lcb_frac
    if total <= 0:
        mean_frac, ucb_frac, lcb_frac = 1.0, 0.0, 0.0
        total = 1.0
    mean_frac /= total
    ucb_frac /= total
    lcb_frac /= total

    def sigma(c: SurrogateCandidate) -> float:
        return _sigma_from_features(
            features=c.predicted_features,
            scheme=sigma_scheme,
            fail_risk_c0=fail_risk_c0,
        )

    def mean(c: SurrogateCandidate) -> float:
        return c.predicted_objective

    def ucb(c: SurrogateCandidate) -> float:
        return c.predicted_objective + q * sigma(c)

    def lcb(c: SurrogateCandidate) -> float:
        return c.predicted_objective - q * sigma(c)

    # Mixture: exploit (mean) + pessimistic (UCB) + optimistic (LCB).
    # Ensure at least one "mean" candidate for stability.
    n_mean = max(1, int(round(n * mean_frac)))
    n_ucb = int(round(n * ucb_frac))
    n_lcb = int(round(n * lcb_frac))
    # Fix rounding drift to sum exactly to n.
    while n_mean + n_ucb + n_lcb > n:
        if n_lcb > 0:
            n_lcb -= 1
        elif n_ucb > 0:
            n_ucb -= 1
        else:
            n_mean -= 1
    while n_mean + n_ucb + n_lcb < n:
        # Bias extra slots toward mean to reduce regressions.
        n_mean += 1

    picked: List[SurrogateCandidate] = []
    seen: Set[Tuple[float, str]] = set()

    def add_top(sorted_list: List[SurrogateCandidate], count: int) -> None:
        for c in sorted_list:
            key = (c.clock_ps, c.tune_variant)
            # Candidate identity inside a clock is its params; use rank to keep stable.
            key = (c.clock_ps, f"{c.tune_variant}:{c.rank}")
            if key in seen:
                continue
            seen.add(key)
            picked.append(c)
            if len(picked) >= count:
                return

    by_mean = sorted(candidates, key=mean)
    by_ucb = sorted(candidates, key=ucb)
    by_lcb = sorted(candidates, key=lcb)

    add_top(by_mean, n_mean)
    add_top(by_ucb, n_mean + n_ucb)
    add_top(by_lcb, n_mean + n_ucb + n_lcb)

    if len(picked) < n:
        # Fill with remaining best-mean candidates.
        for c in by_mean:
            key = (c.clock_ps, f"{c.tune_variant}:{c.rank}")
            if key in seen:
                continue
            seen.add(key)
            picked.append(c)
            if len(picked) >= n:
                break

    return picked[:n]


def _route_objective_from_metrics(*, objective: str, route: Dict[str, Any]) -> Optional[float]:
    if objective == "effective_clock_period":
        return _as_float(route.get("ecp_ps"))
    if objective == "routed_wirelength":
        return _as_float(route.get("routed_wl"))
    if objective == "power":
        return _as_float(route.get("total_power"))
    if objective == "instance_area":
        return _as_float(route.get("instance_area"))
    if objective == "area":
        return _as_float(route.get("core_area"))
    return None


def _parse_route_metrics(route_json: Path) -> Dict[str, Any]:
    detailed = _load_json(route_json)
    grt_json = route_json.with_name("5_1_grt.json")
    globalroute: Dict[str, Any] = {}
    if grt_json.exists():
        try:
            globalroute = _load_json(grt_json)
        except Exception:
            globalroute = {}

    fmax_hz = (
        _as_float(globalroute.get("globalroute__timing__fmax"))
        or _as_float(globalroute.get("globalroute__timing__fmax__clock:core_clock"))
        or _as_float(detailed.get("detailedroute__timing__fmax"))
    )
    ecp_ps = None
    if fmax_hz is not None and fmax_hz > 0:
        ecp_ps = 1e12 / fmax_hz

    routed_wl = _as_float(detailed.get("detailedroute__route__wirelength"))
    if routed_wl is None:
        routed_wl = (
            _as_float(globalroute.get("globalroute__route__wirelength__estimated"))
            or _as_float(globalroute.get("globalroute__route__wirelength"))
            or _as_float(globalroute.get("globalroute__global_route__wirelength"))
        )
    return {
        "route_json": str(route_json),
        "grt_json": str(grt_json) if grt_json.exists() else None,
        "ecp_ps": ecp_ps,
        "fmax_hz": fmax_hz,
        "routed_wl": routed_wl,
        "drc_errors": _as_int(detailed.get("detailedroute__route__drc_errors")),
        "total_power": (
            _as_float(globalroute.get("globalroute__power__total"))
            or _as_float(detailed.get("detailedroute__power__total"))
        ),
        "core_area": (
            _as_float(globalroute.get("globalroute__design__core__area"))
            or _as_float(detailed.get("detailedroute__design__core__area"))
        ),
        "die_area": (
            _as_float(globalroute.get("globalroute__design__die__area"))
            or _as_float(detailed.get("detailedroute__design__die__area"))
        ),
        "instance_area": (
            _as_float(globalroute.get("globalroute__design__instance__area"))
            or _as_float(detailed.get("detailedroute__design__instance__area"))
        ),
        "instance_area_stdcell": (
            _as_float(globalroute.get("globalroute__design__instance__area__stdcell"))
            or _as_float(detailed.get("detailedroute__design__instance__area__stdcell"))
        ),
        "instance_count": (
            _as_int(globalroute.get("globalroute__design__instance__count"))
            or _as_int(detailed.get("detailedroute__design__instance__count"))
        ),
        "instance_count_stdcell": (
            _as_int(globalroute.get("globalroute__design__instance__count__stdcell"))
            or _as_int(detailed.get("detailedroute__design__instance__count__stdcell"))
        ),
    }


def _parse_finish_metrics(report_json: Path, route_json: Optional[Path]) -> Dict[str, Any]:
    data = _load_json(report_json)
    fmax_hz = _as_float(data.get("finish__timing__fmax"))
    ecp_ps = None
    if fmax_hz is not None and fmax_hz > 0:
        ecp_ps = 1e12 / fmax_hz

    out: Dict[str, Any] = {
        "report_json": str(report_json),
        "ecp_ps": ecp_ps,
        "fmax_hz": fmax_hz,
        "instance_area": _as_float(data.get("finish__design__instance__area")),
        "instance_area_stdcell": _as_float(data.get("finish__design__instance__area__stdcell")),
        "core_area": _as_float(data.get("finish__design__core__area")),
        "die_area": _as_float(data.get("finish__design__die__area")),
        "total_power": _as_float(data.get("finish__power__total")),
        "instance_count": _as_int(data.get("finish__design__instance__count")),
        "instance_count_stdcell": _as_int(data.get("finish__design__instance__count__stdcell")),
    }
    if route_json is not None and route_json.exists():
        try:
            route = _parse_route_metrics(route_json)
            out["routed_wl"] = route.get("routed_wl")
            out["drc_errors"] = route.get("drc_errors")
        except Exception:
            pass
    return out


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


def _pick_space_file(design_dir: Path, space_file_env: str, objective: str) -> Path:
    if space_file_env.strip():
        return Path(space_file_env)

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
    space = _load_json(space_file)
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


def _tuned_synth_netlist(
    *, flow_dir: Path, platform: str, design: str, cand: SurrogateCandidate, runs_by_tag: Dict[str, ClockTuneRun]
) -> Optional[Path]:
    tune_run = runs_by_tag.get(cand.tag)
    if tune_run is None or tune_run.variant != cand.tune_variant:
        return None
    tune_results = _results_dir(flow_dir, platform, design, cand.tune_variant)
    for name in ("1_2_yosys.v", "1_synth.v"):
        p = tune_results / name
        if p.exists():
            return p
    return None


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


def _make_print(
    *,
    flow_dir: Path,
    design_config: str,
    name: str,
    env: Dict[str, str],
) -> str:
    cmd = ["make", "--no-print-directory", f"print-{name}", f"DESIGN_CONFIG={design_config}"]
    out = subprocess.check_output(cmd, cwd=str(flow_dir), env=env, text=True, stderr=subprocess.STDOUT)
    m = re.search(rf"(?m)^\s*{re.escape(name)}\s*=\s*(.*)$", out)
    return m.group(1).strip() if m else ""


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
    step = _as_float(spec.get("step")) or 0.0
    if vmax < vmin:
        vmin, vmax = vmax, vmin

    if typ == "binary":
        base = 0 if baseline is None else int(round(float(baseline)))
        base = 1 if base else 0
        if rng.random() < float(flip_prob):
            base = 1 - base
        return float(base)

    if baseline is None:
        baseline = 0.5 * (vmin + vmax)

    baseline = float(min(vmax, max(vmin, float(baseline))))
    span = float(vmax - vmin)
    radius = span * float(max(0.0, min(1.0, float(radius_frac))))
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


def _orfs_var_assignments_from_params(params: Dict[str, Any]) -> List[str]:
    knob_map: Dict[str, str] = {
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

    out: List[str] = []
    # ORFS floorplan.tcl enforces that exactly one initialization method is set.
    # If we are trying to tune utilization/aspect ratio, disable other methods
    # that may be defined by the design config (e.g., FLOORPLAN_DEF).
    if "core_utilization" in params:
        out.extend(["FLOORPLAN_DEF=", "FOOTPRINT=", "DIE_AREA=", "CORE_AREA="])
    for k, env_k in knob_map.items():
        if k not in params:
            continue
        v = params[k]
        if isinstance(v, bool):
            v = int(v)
        if k in {"core_utilization", "tns_end_percent", "global_padding", "detail_padding", "enable_dpo", "cts_cluster_size"}:
            try:
                v = int(round(float(v)))
            except Exception:
                pass
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

    objective = os.environ.get("SURROGATE_OBJECTIVE", "effective_clock_period").strip() or "effective_clock_period"

    space_file = _pick_space_file(design_dir, os.environ.get("SURROGATE_SPACE_FILE", ""), objective)
    _ensure_file(space_file, "SURROGATE_SPACE_FILE")
    # Default to a "characterization-quality" run (time-bounded in OpenROAD).
    samples = int(os.environ.get("SURROGATE_SAMPLES", "1000000000"))
    top_n = int(os.environ.get("SURROGATE_TOP_N", "560"))
    global_top_n = int(os.environ.get("SURROGATE_GLOBAL_TOP_N", str(top_n)))
    resume = _truthy(os.environ.get("SURROGATE_RESUME"))

    conformal_alpha = float(os.environ.get("SURROGATE_CONFORMAL_ALPHA", "0.1"))
    conformal_fail_risk_c0 = float(os.environ.get("SURROGATE_CONFORMAL_FAIL_RISK_C0", "0.001"))
    conformal_sigma_default = "fail_risk" if objective == "effective_clock_period" else "constant"
    conformal_sigma = os.environ.get("SURROGATE_CONFORMAL_SIGMA", conformal_sigma_default).strip() or conformal_sigma_default

    base_env = dict(os.environ)
    base_env["PYTHONUNBUFFERED"] = "1"
    base_env.setdefault("SURROGATE_TIME_BUDGET_S", "600")
    explicit_time_budget_s = (
        _as_float(os.environ.get("SURROGATE_TIME_BUDGET_S")) if os.environ.get("SURROGATE_TIME_BUDGET_S") else None
    )
    total_time_budget_s = _as_float(os.environ.get("SURROGATE_TIME_BUDGET_S_TOTAL"))

    eqy_path = shutil.which("eqy")
    validate_equivalence = _truthy(os.environ.get("SURROGATE_EQUIVALENCE_CHECK"))
    if eqy_path is None and not validate_equivalence:
        print("Note: 'eqy' not found; disabling EQUIVALENCE_CHECK during validation runs.")
    elif eqy_path is None and validate_equivalence:
        print("WARNING: SURROGATE_EQUIVALENCE_CHECK=1 but 'eqy' is not on PATH; validation may fail.", file=sys.stderr)

    print(f"Design: {platform}/{design}")
    print(f"Base variant prefix: {base_variant}")
    print(f"Space file: {space_file}")
    print(f"Objective: {objective}")
    print(f"Calibration: ws={ws_file} wl={wl_file}")
    if baseline_ecp_ps is not None:
        print(f"Baseline ECP (from calibration): {baseline_ecp_ps:.3f} ps")

    current_sdc_units_ps = _infer_current_sdc_units_ps(base_clock_sdc=base_clock_ps, baseline_ecp_ps=baseline_ecp_ps)
    if objective == "effective_clock_period":
        print(f"Inferred SDC time unit: {current_sdc_units_ps} ps/unit")

    q_native = _historical_conformal_q(
        flow_dir=flow_dir,
        objective=objective,
        alpha=conformal_alpha,
        sigma_scheme=conformal_sigma,
        fail_risk_c0=conformal_fail_risk_c0,
    )
    if q_native is None:
        print("Conformal: no historical calibration found; falling back to mean-only selection")

    q_for_selection = 0.0
    if q_native is not None:
        if objective == "effective_clock_period":
            if current_sdc_units_ps is not None and current_sdc_units_ps > 0:
                q_for_selection = float(q_native) / float(current_sdc_units_ps)
        else:
            q_for_selection = float(q_native)

    if q_native is not None:
        unit = "ps" if objective == "effective_clock_period" else "objective_units"
        print(f"Conformal: alpha={conformal_alpha} sigma={conformal_sigma} q={q_native:.6g} ({unit})")

    # -------------------------------------------------------------------------
    # Surrogate tuning: either single-netlist or synth-aware clock sweep.
    clock_mode = "no_clock"
    clock_runs: List[ClockTuneRun] = []
    per_clock: List[Dict[str, Any]] = []
    merged_top: List[Dict[str, Any]] = []
    clocks: List[float] = []

    space_no_clock: Optional[Path] = None
    length_scale: Optional[float] = None
    timing_scale: Optional[float] = None
    ref_clock_ps: Optional[float] = None

    clock_constraints_dir = flow_dir / "tmp_surrogate" / base_variant
    clock_constraints_dir.mkdir(parents=True, exist_ok=True)

    if not _space_has_clock(space_file):
        variant = base_variant
        out_json = _results_dir(flow_dir, platform, design, variant) / "surrogate_optimize.json"
        if not (resume and out_json.exists()):
            print("+ make surrogate_tune (no clock in space)", flush=True)
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
        _ensure_file(out_json, f"{variant} surrogate_optimize.json")

        clocks = [base_clock_ps]
        clock_runs = [
            ClockTuneRun(
                clock_ps=base_clock_ps,
                tag=_clock_tag(base_clock_ps),
                variant=variant,
                sdc_file=base_sdc,
                results_json=out_json,
            )
        ]
        summary = _summarize_optimize_json(out_json)
        per_clock = [{"clock_ps": base_clock_ps, "variant": variant, "results": summary}]
        for cand in summary.get("top", []):
            merged_top.append({"clock_ps": base_clock_ps, "variant": variant, "candidate": cand})
        merged_top.sort(key=lambda x: float(x["candidate"].get("objective", 1e300)))
        merged_top = merged_top[: max(1, global_top_n)]
    else:
        clock_mode = os.environ.get("SURROGATE_CLOCK_MODE", "").strip().lower()
        if not clock_mode:
            # Default policy for ECP: prefer single-netlist clock search to avoid
            # re-synthesizing for every sampled clock (clock is continuous in most
            # spaces via step=0).
            clock_mode = "single_netlist" if objective == "effective_clock_period" else "synth_sweep"
        if clock_mode not in {"synth_sweep", "single_netlist"}:
            raise RuntimeError(
                "Invalid SURROGATE_CLOCK_MODE (expected 'synth_sweep' or 'single_netlist'): "
                f"{clock_mode!r}"
            )

        if clock_mode == "single_netlist":
            variant = base_variant
            out_json = _results_dir(flow_dir, platform, design, variant) / "surrogate_optimize.json"
            if not (resume and out_json.exists()):
                run_env = dict(base_env)
                run_env["SURROGATE_FREEZE"] = run_env.get("SURROGATE_FREEZE", "")
                # Bench scripts often set SURROGATE_TIME_BUDGET_S_TOTAL (per design)
                # rather than SURROGATE_TIME_BUDGET_S (per surrogate_tune run).
                if total_time_budget_s is not None and total_time_budget_s > 0 and explicit_time_budget_s is None:
                    run_env["SURROGATE_TIME_BUDGET_S"] = str(int(total_time_budget_s))
                print("+ make surrogate_tune (single-netlist clock search)", flush=True)
                _make_cmd(
                    target="surrogate_tune",
                    flow_dir=flow_dir,
                    extra_make_args=[
                        f"FLOW_VARIANT={variant}",
                        f"SURROGATE_SPACE_FILE={space_file}",
                        f"SURROGATE_SAMPLES={samples}",
                        f"SURROGATE_TOP_N={top_n}",
                        "SURROGATE_RESET_CALIBRATION=1",
                        f"SURROGATE_CALIBRATE_WS_FILE={ws_file}",
                        f"SURROGATE_CALIBRATE_WL_FILE={wl_file}",
                    ],
                    env=run_env,
                )
            _ensure_file(out_json, f"{variant} surrogate_optimize.json")

            clocks = [base_clock_ps]
            clock_runs = [
                ClockTuneRun(
                    clock_ps=base_clock_ps,
                    tag=_clock_tag(base_clock_ps),
                    variant=variant,
                    sdc_file=base_sdc,
                    results_json=out_json,
                )
            ]
            summary = _summarize_optimize_json(out_json)
            per_clock = [{"clock_ps": base_clock_ps, "variant": variant, "results": summary}]
            for cand in summary.get("top", []):
                merged_top.append({"clock_ps": base_clock_ps, "variant": variant, "candidate": cand})
            merged_top.sort(key=lambda x: float(x["candidate"].get("objective", 1e300)))
            merged_top = merged_top[: max(1, global_top_n)]
        else:
            # Synthesis-aware tuning.
            space_no_clock = _space_without_clock(flow_dir, base_variant, space_file)

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
                        "SURROGATE_TIME_BUDGET_S=0",
                        "SURROGATE_MULTI_FIDELITY=0",
                        "SURROGATE_PORTFOLIO=0",
                    ],
                    env=base_env,
                )

            _ensure_file(calib_out, "calibration surrogate_optimize.json")
            length_scale, timing_scale, ref_clock_ps = _extract_scales(calib_out)
            print(
                f"Calibration scales: builtin_length_scale={length_scale:.6g} "
                f"builtin_timing_scale={timing_scale:.6g} ref_clock_ps={ref_clock_ps:.6g}"
            )

            append_base_clock = True
            clock_spec = None

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
                clock_spec = _clock_spec_from_space(space_file)

                factors_default = "0.78 0.84 0.90 0.96" if objective == "effective_clock_period" else ""
                factors_s = os.environ.get("SURROGATE_CLOCK_FACTORS", factors_default)
                factors = [float(x) for x in _split_tokens(factors_s)] if factors_s.strip() else []

                sweep_n_default = max(1, len(factors) + 1)
                sweep_n = int(os.environ.get("SURROGATE_CLOCK_SWEEP_N", str(sweep_n_default)))

                clock_strategy = os.environ.get("SURROGATE_CLOCK_STRATEGY", "").strip().lower()
                if not clock_strategy:
                    clock_strategy = "space" if (objective == "effective_clock_period" and clock_spec is not None) else "factors"

                min_ps = float(os.environ["SURROGATE_CLOCK_MIN"]) if os.environ.get("SURROGATE_CLOCK_MIN") else None
                max_ps = float(os.environ["SURROGATE_CLOCK_MAX"]) if os.environ.get("SURROGATE_CLOCK_MAX") else None
                if clock_spec is not None:
                    spec_min, spec_max, _ = clock_spec
                    min_ps = spec_min if min_ps is None else max(min_ps, spec_min)
                    max_ps = spec_max if max_ps is None else min(max_ps, spec_max)

                if clock_strategy == "space" and clock_spec is not None and objective == "effective_clock_period":
                    spec_min, spec_max, spec_step = clock_spec
                    clamp_min = spec_min if min_ps is None else max(spec_min, float(min_ps))
                    clamp_max = spec_max if max_ps is None else min(spec_max, float(max_ps))
                    if clamp_max < clamp_min:
                        clamp_min, clamp_max = clamp_max, clamp_min
                    include_base_outside = _truthy(os.environ.get("SURROGATE_CLOCK_INCLUDE_BASE_OUTSIDE_SPACE", "0"))
                    append_base_clock = include_base_outside or (clamp_min <= float(base_clock_ps) <= clamp_max)
                    clocks = _clock_candidates_from_spec(spec=(clamp_min, clamp_max, spec_step), n=sweep_n)
                    # Keep clock sweep size stable by snapping one point to the
                    # design's base clock (instead of always adding it later).
                    if clocks and (clamp_min <= float(base_clock_ps) <= clamp_max):
                        idx = min(range(len(clocks)), key=lambda i: abs(float(clocks[i]) - float(base_clock_ps)))
                        clocks[idx] = float(base_clock_ps)
                else:
                    baseline_ecp_sdc_units = (
                        (float(baseline_ecp_ps) / float(current_sdc_units_ps))
                        if (baseline_ecp_ps is not None and current_sdc_units_ps is not None and current_sdc_units_ps > 0)
                        else baseline_ecp_ps
                    )
                    clocks = _auto_clock_candidates(
                        base_clock_ps=base_clock_ps,
                        baseline_ecp_ps=baseline_ecp_sdc_units,
                        factors=factors,
                        min_ps=min_ps,
                        max_ps=max_ps,
                    )

            if append_base_clock:
                clocks.append(base_clock_ps)
            clocks = sorted({round(float(c), 3) for c in clocks if float(c) > 0.0})
            print(f"Clock sweep (SDC units): {', '.join(_clock_tag(c) for c in clocks)}")
            print(f"Samples/clock: {samples}, top_n/clock: {top_n}, global_top_n: {global_top_n}")

            total_budget_s = total_time_budget_s
            per_clock_budget_s: Optional[int] = None
            if total_budget_s is not None and total_budget_s > 0:
                per_clock_budget_s = max(1, int(math.floor(float(total_budget_s) / float(max(1, len(clocks))))))
                print(f"Time budget: total={total_budget_s:.0f}s => per-clock={per_clock_budget_s}s")

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
                if per_clock_budget_s is not None:
                    run_env["SURROGATE_TIME_BUDGET_S"] = str(per_clock_budget_s)

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

            for run in clock_runs:
                summary = _summarize_optimize_json(run.results_json)
                per_clock.append({"clock_ps": run.clock_ps, "variant": run.variant, "results": summary})
                for cand in summary.get("top", []):
                    merged_top.append({"clock_ps": run.clock_ps, "variant": run.variant, "candidate": cand})

            merged_top.sort(key=lambda x: float(x["candidate"].get("objective", 1e300)))
            merged_top = merged_top[: max(1, global_top_n)]

    out_dir = _results_dir(flow_dir, platform, design, base_variant)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "surrogate_autotune.json"

    # -------------------------------------------------------------------------
    # Build candidate objects for selection / validation.
    runs_by_tag = {r.tag: r for r in clock_runs}
    candidates: List[SurrogateCandidate] = []
    for rank, entry in enumerate(merged_top, start=1):
        cand = entry.get("candidate") or {}
        params = cand.get("params") or {}
        outputs = cand.get("outputs") or {}
        feats = cand.get("features") or {}
        if not isinstance(params, dict):
            params = {}
        if not isinstance(outputs, dict):
            outputs = {}
        if not isinstance(feats, dict):
            feats = {}

        predicted_obj = _as_float(cand.get("objective"))
        if predicted_obj is None:
            continue

        clock_ps = _as_float(params.get("clock_period", entry.get("clock_ps")))
        if clock_ps is None:
            continue
        tag = _clock_tag(clock_ps)

        tune_variant = str(entry.get("variant"))
        tune_run = runs_by_tag.get(tag)
        sdc_file = tune_run.sdc_file if tune_run else clock_constraints_dir / f"constraint_clk{tag}.sdc"
        if not sdc_file.exists():
            _write_text(sdc_file, _rewrite_sdc_clock_period(base_sdc, clock_ps))

        candidates.append(
            SurrogateCandidate(
                rank=rank,
                clock_ps=clock_ps,
                tag=tag,
                tune_variant=tune_variant,
                sdc_file=sdc_file,
                params=params,
                predicted_objective=float(predicted_obj),
                predicted_outputs=outputs,
                predicted_features=feats,
            )
        )

    # -------------------------------------------------------------------------
    # Optional validation: route prefilter then finish.
    validate_enabled = _truthy(os.environ.get("SURROGATE_VALIDATE", "1"))
    validate_select = os.environ.get("SURROGATE_VALIDATE_SELECT", "conformal_portfolio").strip() or "conformal_portfolio"
    validate_select = validate_select.lower()
    validate_n = max(1, int(os.environ.get("SURROGATE_VALIDATE_N", "14"))) if validate_enabled else 0
    validate_jobs = (
        max(1, int(os.environ.get("SURROGATE_VALIDATE_JOBS", str(validate_n or 1)))) if validate_enabled else 1
    )
    validate_make_target = os.environ.get("SURROGATE_VALIDATE_MAKE_TARGET", "finish").strip() or "finish"
    validate_make_target_norm = validate_make_target.strip().lower()

    validate_variant_tag = os.environ.get("SURROGATE_VALIDATE_VARIANT_TAG", "").strip()
    if validate_variant_tag and not validate_variant_tag.startswith("_"):
        validate_variant_tag = "_" + validate_variant_tag
    validate_variant_prefix = f"{base_variant}{validate_variant_tag}"

    route_prefilter = validate_enabled and _truthy(os.environ.get("SURROGATE_ROUTE_VALIDATE", "1"))
    route_prefilter_stage = os.environ.get("SURROGATE_ROUTE_VALIDATE_STAGE", "grt").strip().lower() or "grt"
    if route_prefilter_stage not in {"route", "grt"}:
        raise RuntimeError(f"Invalid SURROGATE_ROUTE_VALIDATE_STAGE={route_prefilter_stage!r} (expected 'route' or 'grt')")
    route_json_name = "5_1_grt.json" if route_prefilter_stage == "grt" else "5_2_route.json"
    # With a cheaper prefilter stage, default to probing more candidates.
    route_n_factor = 2.0 if route_prefilter_stage == "grt" else 1.4
    route_n_default = int(math.ceil(validate_n * route_n_factor)) if validate_n else 0
    route_n = max(validate_n, int(os.environ.get("SURROGATE_ROUTE_VALIDATE_N", str(route_n_default)))) if route_prefilter else 0
    route_jobs = max(1, int(os.environ.get("SURROGATE_ROUTE_VALIDATE_JOBS", str(validate_jobs)))) if route_prefilter else 1

    route_inject_random_n_default = 0
    if route_prefilter and objective == "effective_clock_period" and clock_mode == "single_netlist":
        route_inject_random_n_default = min(route_n, validate_n)
    route_inject_random_n = int(os.environ.get("SURROGATE_ROUTE_INJECT_RANDOM_N", str(route_inject_random_n_default)))
    route_inject_random_n = max(0, min(route_n, int(route_inject_random_n))) if route_prefilter else 0
    route_inject_clock_sweep_n = max(1, int(os.environ.get("SURROGATE_ROUTE_INJECT_CLOCK_SWEEP_N", "5")))
    route_inject_radius_frac = float(os.environ.get("SURROGATE_ROUTE_INJECT_RADIUS_FRAC", "0.20"))
    route_inject_flip_prob = float(os.environ.get("SURROGATE_ROUTE_INJECT_FLIP_PROB", "0.25"))
    route_inject_seed_s = os.environ.get("SURROGATE_ROUTE_INJECT_SEED", "").strip()
    if route_inject_seed_s:
        route_inject_seed = int(route_inject_seed_s)
    else:
        seed_bytes = hashlib.sha1(f"{platform}:{design}:{base_variant}:route_inject".encode("utf-8")).digest()
        route_inject_seed = int.from_bytes(seed_bytes[:4], "big")

    portfolio_mean_frac = _as_float(os.environ.get("SURROGATE_VALIDATE_PORTFOLIO_MEAN_FRAC", "0.7")) or 0.7
    portfolio_ucb_frac = _as_float(os.environ.get("SURROGATE_VALIDATE_PORTFOLIO_UCB_FRAC", "0.2")) or 0.2
    portfolio_lcb_frac = _as_float(os.environ.get("SURROGATE_VALIDATE_PORTFOLIO_LCB_FRAC", "0.1")) or 0.1

    route_results: List[Dict[str, Any]] = []
    finish_results: List[Dict[str, Any]] = []
    selected_route_variants: List[str] = []
    selected_finish_variants: List[str] = []

    validate_netlist_mode = os.environ.get("SURROGATE_VALIDATE_NETLIST_MODE", "").strip().lower()
    if not validate_netlist_mode:
        # When doing single-netlist clock tuning (default for ECP), keep validation
        # consistent and fast by reusing the base synth netlist for all candidates.
        validate_netlist_mode = (
            "base" if (clock_mode == "single_netlist" and objective == "effective_clock_period") else "auto"
        )
    if validate_netlist_mode not in {"auto", "base", "resynth"}:
        raise RuntimeError(
            "Invalid SURROGATE_VALIDATE_NETLIST_MODE (expected 'auto', 'base', or 'resynth'): "
            f"{validate_netlist_mode!r}"
        )

    base_synth_netlist: Optional[Path] = None
    if validate_netlist_mode == "base":
        base_results = _results_dir(flow_dir, platform, design, base_variant)
        for name in ("1_2_yosys.v", "1_synth.v"):
            cand = base_results / name
            if cand.exists():
                base_synth_netlist = cand
                break
        if base_synth_netlist is None:
            print(
                f"WARNING: SURROGATE_VALIDATE_NETLIST_MODE=base but no synth netlist found under {base_results}; "
                "falling back to per-candidate synthesis.",
                file=sys.stderr,
            )
            validate_netlist_mode = "resynth"

    def make_args_for(variant: str, sdc_file: Path, synth_netlist: Optional[Path], params: Dict[str, Any]) -> List[str]:
        args = [f"FLOW_VARIANT={variant}", f"SDC_FILE={sdc_file}"]
        if eqy_path is None and not validate_equivalence:
            args.append("EQUIVALENCE_CHECK=0")
        chosen_netlist: Optional[Path] = synth_netlist
        if validate_netlist_mode == "resynth":
            chosen_netlist = None
        elif validate_netlist_mode == "base":
            chosen_netlist = base_synth_netlist
        if chosen_netlist is not None:
            args.append(f"SYNTH_NETLIST_FILES={chosen_netlist}")
        args.extend(_orfs_var_assignments_from_params(params))
        return args

    if validate_enabled and candidates:
        def sigma_for(c: SurrogateCandidate) -> float:
            return _sigma_from_features(
                features=c.predicted_features,
                scheme=conformal_sigma,
                fail_risk_c0=conformal_fail_risk_c0,
            )

        def ucb_for(c: SurrogateCandidate) -> float:
            return c.predicted_objective + q_for_selection * sigma_for(c)

        def lcb_for(c: SurrogateCandidate) -> float:
            return c.predicted_objective - q_for_selection * sigma_for(c)

        if validate_select == "mean":
            pick_for = lambda n: sorted(candidates, key=lambda c: c.predicted_objective)[:n]
        elif validate_select == "ucb":
            if q_native is None:
                pick_for = lambda n: sorted(candidates, key=lambda c: c.predicted_objective)[:n]
            else:
                pick_for = lambda n: sorted(candidates, key=ucb_for)[:n]
        elif validate_select == "lcb":
            if q_native is None:
                pick_for = lambda n: sorted(candidates, key=lambda c: c.predicted_objective)[:n]
            else:
                pick_for = lambda n: sorted(candidates, key=lcb_for)[:n]
        else:
            if q_native is None:
                pick_for = lambda n: sorted(candidates, key=lambda c: c.predicted_objective)[:n]
            else:
                pick_for = lambda n: _select_portfolio(
                    candidates=candidates,
                    n=n,
                    q=q_for_selection,
                    sigma_scheme=conformal_sigma,
                    fail_risk_c0=conformal_fail_risk_c0,
                    mean_frac=portfolio_mean_frac,
                    ucb_frac=portfolio_ucb_frac,
                    lcb_frac=portfolio_lcb_frac,
                )

        if route_prefilter:
            jobs: List[RouteValidation] = []
            reused_route_jobs = False
            if resume and summary_path.exists():
                try:
                    prev = _load_json(summary_path)
                    prev_sel = prev.get("selection") if isinstance(prev, dict) else None
                    prev_route = prev.get("route_prefilter_results") if isinstance(prev, dict) else None
                    if isinstance(prev_sel, dict) and isinstance(prev_route, list) and prev_route:
                        want = {
                            "validate_select": validate_select,
                            "validate_n": validate_n,
                            "validate_variant_tag": validate_variant_tag,
                            "route_prefilter": True,
                            "route_prefilter_stage": route_prefilter_stage,
                            "route_n": route_n,
                            "route_inject_random_n": route_inject_random_n,
                            "route_inject_clock_sweep_n": route_inject_clock_sweep_n,
                            "route_inject_radius_frac": route_inject_radius_frac,
                            "route_inject_flip_prob": route_inject_flip_prob,
                            "route_inject_seed": route_inject_seed,
                            "conformal_alpha": conformal_alpha,
                            "conformal_sigma": conformal_sigma,
                            "conformal_fail_risk_c0": conformal_fail_risk_c0,
                            "portfolio_mean_frac": portfolio_mean_frac,
                            "portfolio_ucb_frac": portfolio_ucb_frac,
                            "portfolio_lcb_frac": portfolio_lcb_frac,
                        }
                        have = {k: prev_sel.get(k) for k in want}
                        if want == have:
                            for r in prev_route:
                                if not isinstance(r, dict):
                                    continue
                                variant = str(r.get("variant") or "")
                                if not variant:
                                    continue
                                sdc_file = Path(str(r.get("sdc_file")))
                                params = r.get("params") if isinstance(r.get("params"), dict) else {}
                                preds_out = r.get("predicted_outputs") if isinstance(r.get("predicted_outputs"), dict) else {}
                                preds_feat = (
                                    r.get("predicted_features") if isinstance(r.get("predicted_features"), dict) else {}
                                )
                                predicted_obj = _as_float(r.get("predicted_objective"))
                                clock_ps = _as_float(r.get("clock_ps"))
                                tune_variant = str(r.get("tune_variant") or "")
                                if predicted_obj is None or clock_ps is None or not tune_variant:
                                    continue
                                tag = _clock_tag(clock_ps)
                                cand = SurrogateCandidate(
                                    rank=int(r.get("rank") or 0),
                                    clock_ps=float(clock_ps),
                                    tag=tag,
                                    tune_variant=tune_variant,
                                    sdc_file=sdc_file,
                                    params=params,
                                    predicted_objective=float(predicted_obj),
                                    predicted_outputs=preds_out,
                                    predicted_features=preds_feat,
                                )
                                synth_netlist_s = r.get("synth_netlist")
                                synth_netlist = Path(str(synth_netlist_s)) if synth_netlist_s else None
                                route_json_s = r.get("route_json")
                                default_route_json = _logs_dir(flow_dir, platform, design, variant) / route_json_name
                                route_json = Path(str(route_json_s)) if route_json_s else default_route_json
                                jobs.append(
                                    RouteValidation(
                                        rank=int(r.get("rank") or 0),
                                        candidate=cand,
                                        variant=variant,
                                        sdc_file=sdc_file,
                                        synth_netlist=synth_netlist if (synth_netlist and synth_netlist.exists()) else None,
                                        params=params,
                                        route_json=route_json,
                                    )
                                )
                            jobs.sort(key=lambda j: j.rank)
                            if len(jobs) >= route_n:
                                jobs = jobs[:route_n]
                                reused_route_jobs = True
                                print(f"Resume: reusing existing route-prefilter variants from {summary_path}")
                except Exception:
                    reused_route_jobs = False

            if not reused_route_jobs:
                model_n = max(0, int(route_n) - int(route_inject_random_n))
                route_cands = pick_for(model_n)

                # For discrete clock sweeps (synth-aware ECP), ensure we probe at
                # least one candidate per clock. This reduces the chance that a
                # small model error collapses all validation to a single clock.
                if objective == "effective_clock_period":
                    uniq_tags = sorted({c.tag for c in candidates})
                    diversify = _truthy(os.environ.get("SURROGATE_ROUTE_DIVERSIFY_CLOCKS", "1"))
                    if diversify and 1 < len(uniq_tags) <= min(route_n, 32):
                        best_per: Dict[str, SurrogateCandidate] = {}
                        for c in candidates:
                            prev = best_per.get(c.tag)
                            if prev is None or c.predicted_objective < prev.predicted_objective:
                                best_per[c.tag] = c
                        seed = sorted(best_per.values(), key=lambda c: c.predicted_objective)
                        merged: List[SurrogateCandidate] = []
                        seen: Set[Tuple[str, int]] = set()
                        for c in list(seed) + list(route_cands):
                            key = (c.tag, c.rank)
                            if key in seen:
                                continue
                            seen.add(key)
                            merged.append(c)
                            if len(merged) >= route_n:
                                break
                        route_cands = merged

                if route_inject_random_n:
                    try:
                        space = _load_json(space_file)
                    except Exception:
                        space = {}
                    if not isinstance(space, dict):
                        space = {}

                    knob_map: Dict[str, str] = {
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

                    baseline_vars: Dict[str, Optional[float]] = {"clock_period": base_clock_ps}
                    for knob in space.keys():
                        if knob == "clock_period":
                            continue
                        orfs_var = knob_map.get(knob)
                        if not orfs_var:
                            continue
                        s = _make_print(flow_dir=flow_dir, design_config=design_config, name=orfs_var, env=base_env)
                        baseline_vars[knob] = _as_float(s) if s else None

                    clock_spec = _clock_spec_from_space(space_file)
                    clock_plan: List[float] = []
                    if clock_spec is not None:
                        clock_plan = _clock_candidates_from_spec(spec=clock_spec, n=route_inject_clock_sweep_n)
                        spec_min, spec_max, _ = clock_spec
                        if clock_plan and (spec_min <= float(base_clock_ps) <= spec_max):
                            idx = min(range(len(clock_plan)), key=lambda i: abs(float(clock_plan[i]) - float(base_clock_ps)))
                            clock_plan[idx] = float(base_clock_ps)
                    if not clock_plan:
                        clock_plan = [base_clock_ps]

                    rng = random.Random(int(route_inject_seed))
                    injected: List[SurrogateCandidate] = []
                    for i in range(int(route_inject_random_n)):
                        clock_ps = float(clock_plan[i % len(clock_plan)])
                        tag = _clock_tag(clock_ps)
                        sdc_file = clock_constraints_dir / f"constraint_clk{tag}.sdc"
                        if not sdc_file.exists():
                            _write_text(sdc_file, _rewrite_sdc_clock_period(base_sdc, clock_ps))

                        params: Dict[str, Any] = {"clock_period": clock_ps}
                        for knob, spec in space.items():
                            if knob == "clock_period" or not isinstance(spec, dict):
                                continue
                            v = _sample_value(
                                spec=spec,
                                baseline=baseline_vars.get(knob),
                                rng=rng,
                                radius_frac=route_inject_radius_frac,
                                flip_prob=route_inject_flip_prob,
                            )
                            typ = str(spec.get("type") or "").strip().lower()
                            if typ in {"int", "binary"}:
                                v = int(round(float(v)))
                            params[knob] = v

                        injected.append(
                            SurrogateCandidate(
                                rank=0,
                                clock_ps=clock_ps,
                                tag=tag,
                                tune_variant=base_variant,
                                sdc_file=sdc_file,
                                params=params,
                                predicted_objective=float("inf"),
                                predicted_outputs={},
                                predicted_features={},
                            )
                        )

                    route_cands.extend(injected)
                for idx, c in enumerate(route_cands, start=1):
                    variant = f"{validate_variant_prefix}_v{idx:02d}_clk{c.tag}"
                    logs_dir = _logs_dir(flow_dir, platform, design, variant)
                    route_json = logs_dir / route_json_name
                    synth_netlist = _tuned_synth_netlist(
                        flow_dir=flow_dir, platform=platform, design=design, cand=c, runs_by_tag=runs_by_tag
                    )
                    jobs.append(
                        RouteValidation(
                            rank=idx,
                            candidate=c,
                            variant=variant,
                            sdc_file=c.sdc_file,
                            synth_netlist=synth_netlist,
                            params=c.params,
                            route_json=route_json,
                        )
                    )

            def run_route(job: RouteValidation) -> Dict[str, Any]:
                make_args = make_args_for(job.variant, job.sdc_file, job.synth_netlist, job.params)
                out: Dict[str, Any] = {
                    "rank": job.rank,
                    "clock_ps": job.candidate.clock_ps,
                    "variant": job.variant,
                    "tune_variant": job.candidate.tune_variant,
                    "sdc_file": str(job.sdc_file),
                    "synth_netlist": str(job.synth_netlist) if job.synth_netlist is not None else None,
                    "params": job.params,
                    "predicted_objective": job.candidate.predicted_objective,
                    "predicted_outputs": job.candidate.predicted_outputs,
                    "predicted_features": job.candidate.predicted_features,
                    "error": None,
                }
                if resume and job.route_json.exists():
                    try:
                        route = _parse_route_metrics(job.route_json)
                        out.update(route)
                        out["route_objective"] = _route_objective_from_metrics(objective=objective, route=route)
                        return out
                    except Exception as e:
                        out["error"] = f"Failed to parse route metrics: {e}"
                        return out

                print(
                    f"+ make {route_prefilter_stage} FLOW_VARIANT={job.variant} (prefilter rank {job.rank})",
                    flush=True,
                )
                try:
                    _make_cmd(target=route_prefilter_stage, flow_dir=flow_dir, extra_make_args=make_args, env=base_env)
                except subprocess.CalledProcessError as e:
                    out["error"] = str(e)
                    return out

                try:
                    route = _parse_route_metrics(job.route_json)
                    out.update(route)
                    out["route_objective"] = _route_objective_from_metrics(objective=objective, route=route)
                except Exception as e:
                    out["error"] = f"Missing/invalid route metrics: {e}"
                return out

            with ThreadPoolExecutor(max_workers=route_jobs) as ex:
                futs = [ex.submit(run_route, j) for j in jobs]
                for fut in as_completed(futs):
                    route_results.append(fut.result())
            route_results.sort(key=lambda x: int(x.get("rank", 1_000_000)))

            # Pick the best N by route objective (min), then run finish on those variants.
            ok = [r for r in route_results if not r.get("error") and r.get("route_objective") is not None]
            ok.sort(key=lambda r: float(r["route_objective"]))
            selected_finish_variants = [r["variant"] for r in ok[:validate_n]]
            selected_route_variants = [r["variant"] for r in route_results]
        else:
            finish_cands = pick_for(validate_n)
            selected_finish_variants = []
            jobs_f: List[FinishValidation] = []
            for idx, c in enumerate(finish_cands, start=1):
                variant = f"{validate_variant_prefix}_v{idx:02d}_clk{c.tag}"
                logs_dir = _logs_dir(flow_dir, platform, design, variant)
                route_json = logs_dir / "5_2_route.json"
                report_json = logs_dir / "6_report.json"
                synth_netlist = _tuned_synth_netlist(
                    flow_dir=flow_dir, platform=platform, design=design, cand=c, runs_by_tag=runs_by_tag
                )
                jobs_f.append(
                    FinishValidation(
                        rank=idx,
                        candidate=c,
                        variant=variant,
                        sdc_file=c.sdc_file,
                        synth_netlist=synth_netlist,
                        params=c.params,
                        route_json=route_json,
                        report_json=report_json,
                    )
                )
                selected_finish_variants.append(variant)

            # Reuse finish runner below.
            selected_route_variants = []
            selected_finish_variants = [j.variant for j in jobs_f]
            finish_jobs = jobs_f
        # If we ran route prefilter, construct finish jobs from selected variants.
        if route_prefilter:
            job_by_variant: Dict[str, RouteValidation] = {j.variant: j for j in jobs}
            finish_jobs = []
            for idx, variant in enumerate(selected_finish_variants, start=1):
                j = job_by_variant.get(variant)
                if j is None:
                    continue
                logs_dir = _logs_dir(flow_dir, platform, design, variant)
                finish_jobs.append(
                    FinishValidation(
                        rank=idx,
                        candidate=j.candidate,
                        variant=variant,
                        sdc_file=j.sdc_file,
                        synth_netlist=j.synth_netlist,
                        params=j.params,
                        route_json=j.route_json,
                        report_json=logs_dir / "6_report.json",
                    )
                )

        def run_finish(job: FinishValidation) -> Dict[str, Any]:
            make_args = make_args_for(job.variant, job.sdc_file, job.synth_netlist, job.params)

            def route_json_for_finish() -> Path:
                if route_prefilter and route_prefilter_stage == "grt":
                    det = job.report_json.parent / "5_2_route.json"
                    if det.exists():
                        return det
                return job.route_json

            out: Dict[str, Any] = {
                "rank": job.rank,
                "clock_ps": job.candidate.clock_ps,
                "variant": job.variant,
                "tune_variant": job.candidate.tune_variant,
                "sdc_file": str(job.sdc_file),
                "synth_netlist": str(job.synth_netlist) if job.synth_netlist is not None else None,
                "params": job.params,
                "predicted_objective": job.candidate.predicted_objective,
                "predicted_outputs": job.candidate.predicted_outputs,
                "predicted_features": job.candidate.predicted_features,
                "error": None,
            }
            if resume and job.report_json.exists():
                try:
                    finish = _parse_finish_metrics(job.report_json, route_json_for_finish())
                    out.update(finish)
                    out["objective"] = _route_objective_from_metrics(objective=objective, route=finish)
                    return out
                except Exception as e:
                    out["error"] = f"Failed to parse finish metrics: {e}"
                    return out

            make_target = validate_make_target
            if validate_make_target_norm in {"report", "6_report", "report_only", "do-6_report"}:
                make_target = f"logs/{platform}/{design}/{job.variant}/6_report.log"

            print(f"+ make {make_target} FLOW_VARIANT={job.variant} (final rank {job.rank})", flush=True)
            try:
                _make_cmd(target=make_target, flow_dir=flow_dir, extra_make_args=make_args, env=base_env)
            except subprocess.CalledProcessError as e:
                out["error"] = str(e)
                return out

            try:
                finish = _parse_finish_metrics(job.report_json, route_json_for_finish())
                out.update(finish)
                out["objective"] = _route_objective_from_metrics(objective=objective, route=finish)
            except Exception as e:
                out["error"] = f"Missing/invalid finish metrics: {e}"
            return out

        with ThreadPoolExecutor(max_workers=validate_jobs) as ex:
            futs = [ex.submit(run_finish, j) for j in finish_jobs]
            for fut in as_completed(futs):
                finish_results.append(fut.result())
        finish_results.sort(key=lambda x: int(x.get("rank", 1_000_000)))

    # -------------------------------------------------------------------------
    # Best-of selection (validated).
    baseline_metrics: Optional[Dict[str, Any]] = None
    baseline_objective: Optional[float] = None
    baseline_error: Optional[str] = None
    try:
        baseline_metrics = _parse_finish_metrics(ws_file, wl_file)
        baseline_objective = _route_objective_from_metrics(objective=objective, route=baseline_metrics)
    except Exception as e:
        baseline_error = f"{type(e).__name__}: {e}"

    baseline_entry: Dict[str, Any] = {
        "rank": 0,
        "clock_ps": base_clock_ps,
        "variant": "base",
        "tune_variant": None,
        "sdc_file": str(base_sdc),
        "synth_netlist": None,
        "params": {},
        "predicted_objective": None,
        "predicted_outputs": None,
        "predicted_features": None,
        "error": baseline_error,
    }
    if baseline_metrics is not None:
        baseline_entry.update(baseline_metrics)
    baseline_entry["objective"] = baseline_objective

    best_validated: Optional[Dict[str, Any]] = None
    best_obj: Optional[float] = None
    if baseline_objective is not None and baseline_error is None:
        best_validated = baseline_entry
        best_obj = float(baseline_objective)
    for r in finish_results:
        if r.get("error"):
            continue
        obj_v = _as_float(r.get("objective"))
        if obj_v is None:
            continue
        if best_obj is None or obj_v < best_obj:
            best_obj = obj_v
            best_validated = r

    improve_pct: Optional[float] = None
    if baseline_objective is not None and best_obj is not None and baseline_objective != 0:
        improve_pct = (float(baseline_objective) - float(best_obj)) / float(baseline_objective) * 100.0

    # -------------------------------------------------------------------------
    # Write summary.
    _dump_json(
        summary_path,
        {
            "design": {"platform": platform, "design": design},
            "base_variant": base_variant,
            "tuning": {
                "objective": objective,
                "samples_per_clock": samples,
                "top_n_per_clock": top_n,
                "global_top_n": global_top_n,
            },
            "selection": {
                "validate_enabled": validate_enabled,
                "validate_select": validate_select,
                "validate_n": validate_n,
                "validate_jobs": validate_jobs,
                "validate_make_target": validate_make_target,
                "validate_variant_tag": validate_variant_tag,
                "route_prefilter": route_prefilter,
                "route_prefilter_stage": route_prefilter_stage if route_prefilter else None,
                "route_n": route_n,
                "route_inject_random_n": route_inject_random_n if route_prefilter else None,
                "route_inject_clock_sweep_n": route_inject_clock_sweep_n if route_prefilter else None,
                "route_inject_radius_frac": route_inject_radius_frac if route_prefilter else None,
                "route_inject_flip_prob": route_inject_flip_prob if route_prefilter else None,
                "route_inject_seed": route_inject_seed if route_prefilter else None,
                "route_jobs": route_jobs,
                "conformal_alpha": conformal_alpha,
                "conformal_sigma": conformal_sigma,
                "conformal_fail_risk_c0": conformal_fail_risk_c0,
                "conformal_q_native": q_native,
                "conformal_q_for_selection": q_for_selection,
                "sdc_units_ps": current_sdc_units_ps,
                "portfolio_mean_frac": portfolio_mean_frac if validate_enabled else None,
                "portfolio_ucb_frac": portfolio_ucb_frac if validate_enabled else None,
                "portfolio_lcb_frac": portfolio_lcb_frac if validate_enabled else None,
                "selected_route_variants": selected_route_variants,
                "selected_finish_variants": selected_finish_variants,
            },
            "space_file": str(space_file),
            "space_no_clock_file": str(space_no_clock) if space_no_clock is not None else None,
            "base_sdc": str(base_sdc),
            "clock_sweep_ps": clocks,
            "calibration": {
                "ws_file": str(ws_file),
                "wl_file": str(wl_file),
                "baseline_ecp_ps": baseline_ecp_ps,
                "baseline_ecp_sdc_units": (
                    (float(baseline_ecp_ps) / float(current_sdc_units_ps))
                    if (baseline_ecp_ps is not None and current_sdc_units_ps is not None and current_sdc_units_ps > 0)
                    else None
                ),
                "sdc_units_ps": current_sdc_units_ps,
                "builtin_length_scale": length_scale,
                "builtin_timing_scale": timing_scale,
                "builtin_ref_clock_ps": ref_clock_ps,
            },
            "per_clock": per_clock,
            "global_top": merged_top,
            "route_prefilter_results": route_results,
            "validation": {
                "enabled": validate_enabled,
                "baseline": baseline_entry,
                "baseline_objective": baseline_objective,
                "improve_pct": improve_pct,
                "candidates": finish_results,
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
        print(f"Best validated: obj={best_obj} variant={best_validated.get('variant')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
