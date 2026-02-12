#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Metrics:
    baseline: float
    best: float
    improve_pct: float
    ok: int
    total: int
    win_prob_vs_other: Optional[float] = None


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _win_prob(smaller_is_better_a: list[float], smaller_is_better_b: list[float]) -> Optional[float]:
    if not smaller_is_better_a or not smaller_is_better_b:
        return None
    win = 0
    tie = 0
    tot = 0
    for a in smaller_is_better_a:
        for b in smaller_is_better_b:
            tot += 1
            if a < b:
                win += 1
            elif a == b:
                tie += 1
    return (win + 0.5 * tie) / tot if tot else None


def read_surrogate(path: Path) -> Optional[tuple[Metrics, list[float]]]:
    if not path.exists():
        return None
    obj = _load_json(path)
    val = obj.get("validation")
    if not isinstance(val, dict) or not val.get("enabled"):
        return None
    baseline = float(val["baseline_objective"])
    best = float((val.get("best") or {})["objective"])
    improve_pct = float(val["improve_pct"])
    cands = []
    total = 0
    ok = 0
    for c in val.get("candidates", []):
        if not isinstance(c, dict):
            continue
        total += 1
        if c.get("error"):
            continue
        o = c.get("objective")
        if o is None:
            continue
        ok += 1
        cands.append(float(o))
    return Metrics(baseline=baseline, best=best, improve_pct=improve_pct, ok=ok, total=total), cands


def read_random(path: Path) -> Optional[tuple[Metrics, list[float]]]:
    if not path.exists():
        return None
    obj = _load_json(path)
    rnd = obj.get("random")
    if not isinstance(rnd, dict):
        return None
    baseline = rnd.get("baseline_objective")
    best = rnd.get("best_objective")
    improve_pct = rnd.get("improve_pct")
    if baseline is None or best is None or improve_pct is None:
        return None
    baseline_f = float(baseline)
    cands = []
    total = 0
    ok = 0
    for c in rnd.get("candidates", []):
        if not isinstance(c, dict):
            continue
        total += 1
        if c.get("error"):
            continue
        o = c.get("objective")
        if o is None:
            continue
        ok += 1
        cands.append(float(o))
    return (
        Metrics(baseline=baseline_f, best=float(best), improve_pct=float(improve_pct), ok=ok, total=total),
        cands,
    )


def variant_for(platform: str, design: str) -> str:
    if design == "ibex" and platform in {"asap7", "nangate45"}:
        return "ecpwall_modelv2_ibex_t3000s"
    return f"ecpwall_modelv2_t3000s_ecp_{platform}_{design}"


def random_prefixes_for(platform: str, design: str) -> list[str]:
    if platform == "nangate45":
        # Support both historical prefixes.
        return [f"rand18_ng45_{design}_20260211_ecp", f"rand18_nangate45_{design}_20260211_ecp"]
    return [f"rand18_{platform}_{design}_20260211_ecp"]


def main() -> int:
    root = Path(__file__).resolve().parent
    cases = [(p, d) for p in ("asap7", "nangate45", "sky130hd") for d in ("aes", "ibex", "jpeg")]

    print("ECP surrogate vs random (best-of-18 + winP over 18x18)")
    for platform, design in cases:
        s_variant = variant_for(platform, design)
        s_path = root / "flow" / "results" / platform / design / s_variant / "surrogate_autotune.json"
        s = read_surrogate(s_path)

        r = None
        r_path = None
        for pref in random_prefixes_for(platform, design):
            cand = root / "flow" / "results" / platform / design / pref / "random_baseline.json"
            if cand.exists():
                r_path = cand
                r = read_random(cand)
                break

        s_m, s_vals = (s if s else (None, []))
        r_m, r_vals = (r if r else (None, []))

        ratio = None
        if s_m and r_m and r_m.improve_pct:
            ratio = s_m.improve_pct / r_m.improve_pct

        winp = None
        if s_vals and r_vals:
            winp = _win_prob(s_vals, r_vals)

        def fmt(m: Optional[Metrics]) -> str:
            if not m:
                return "—"
            return f"{m.best:.3f}ps ({m.improve_pct:+.3f}%) ok={m.ok}/{m.total}"

        parts = [
            f"{platform:8} {design:5}",
            f"sur={fmt(s_m)}",
            f"rand={fmt(r_m)}",
            f"ratio={(f'{ratio:.3f}x' if ratio is not None else '—')}",
            f"winP={(f'{winp:.3f}' if winp is not None else '—')}",
        ]
        print("  ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

