#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from scan_chain_plot import parse_def_placements, reconstruct_chains_from_verilog
from scan_chain_validate import ValidationSummary, validate_netlist


@dataclass(frozen=True)
class VariantMetrics:
    platform: str
    design: str
    variant: str
    tns: Optional[float]
    wns: Optional[float]
    wirelength: Optional[float]
    chains: int
    min_len: Optional[int]
    median_len: Optional[int]
    max_len: Optional[int]
    max_step_um: Optional[float]
    p99_step_um: Optional[float]
    broken_links: int
    orphan_cells: int
    duplicate_cells: int


def _get_first(d: Dict[str, Any], keys: Sequence[str]) -> Optional[float]:
    for k in keys:
        if k in d and d[k] not in ("", None):
            try:
                return float(d[k])
            except (TypeError, ValueError):
                return None
    return None


def read_qor(
    report_json: Path, route_json: Optional[Path]
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    data = json.loads(report_json.read_text())
    tns = _get_first(data, ["finish__timing__setup__tns", "finish__timing__setup__TNS"])
    wns = _get_first(
        data,
        ["finish__timing__setup__ws", "finish__timing__setup__WNS", "finish__timing__setup__wns"],
    )
    wl = _get_first(
        data,
        [
            "finish__route__wirelength",
            "finish__route__wire_length",
            "finish__route__wirelength__total",
        ],
    )
    if wl is None and route_json and route_json.exists():
        r = json.loads(route_json.read_text())
        wl = _get_first(
            r,
            [
                "detailedroute__route__wirelength",
                "detailedroute__route__wire_length",
                "route__wirelength",
                "route__wire_length",
            ],
        )
    return tns, wns, wl


def scan_jump_metrics(verilog: Path, deff: Path) -> Tuple[ValidationSummary, Optional[float], Optional[float], Optional[int], Optional[int], Optional[int]]:
    validation = validate_netlist(
        verilog,
        scan_in="scan_in_0",
        scan_out="scan_out_0",
        scan_enable="scan_enable_0",
        auto_chains=True,
        scan_in_prefix="scan_in_",
        scan_out_prefix="scan_out_",
    )

    placements_um, _pins_um, _diearea = parse_def_placements(deff)
    chains, _errors = reconstruct_chains_from_verilog(
        verilog,
        scan_in="scan_in_0",
        scan_out="scan_out_0",
        auto_chains=True,
        scan_in_prefix="scan_in_",
        scan_out_prefix="scan_out_",
    )

    lengths = sorted([len(c.cells) for c in chains if c.cells])
    min_len = lengths[0] if lengths else None
    max_len = lengths[-1] if lengths else None
    median_len = int(statistics.median(lengths)) if lengths else None

    all_steps: List[float] = []
    for chain in chains:
        if not chain.cells:
            continue
        pts = [placements_um[c] for c in chain.cells]
        all_steps.extend(
            [abs(x1 - x0) + abs(y1 - y0) for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:])]
        )

    all_steps.sort()
    if not all_steps:
        return validation, None, None, min_len, median_len, max_len

    p99_idx = max(0, int(round(0.99 * len(all_steps))) - 1)
    p99 = all_steps[p99_idx]
    return validation, all_steps[-1], p99, min_len, median_len, max_len


def fmt(v: Optional[float], prec: int = 3) -> str:
    if v is None:
        return ""
    return f"{v:.{prec}f}"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Summarize QoR (TNS/WNS/WL) + scan-chain jump metrics from existing ORFS runs."
    )
    ap.add_argument("--platform", required=True)
    ap.add_argument("--design", required=True)
    ap.add_argument("--variants", nargs="*", default=[])
    ap.add_argument("--glob", default=None, help="Optional glob under flow/results/<platform>/<design>/")
    ap.add_argument("--flow-root", type=Path, default=Path("flow"))
    ap.add_argument("--out-json", type=Path)
    args = ap.parse_args(argv)

    results_root = args.flow_root / "results" / args.platform / args.design
    logs_root = args.flow_root / "logs" / args.platform / args.design

    variants: List[str] = list(args.variants)
    if args.glob:
        variants.extend(sorted([p.name for p in results_root.glob(args.glob) if p.is_dir()]))
    variants = sorted(set(variants))
    if not variants:
        raise SystemExit("No variants specified (use --variants and/or --glob).")

    rows: List[VariantMetrics] = []
    for variant in variants:
        report_json = logs_root / variant / "6_report.json"
        route_json = logs_root / variant / "5_2_route.json"
        final_def = results_root / variant / "6_final.def"
        final_v = results_root / variant / "6_final.v"

        tns = wns = wl = None
        if report_json.exists():
            tns, wns, wl = read_qor(report_json, route_json)

        validation = None
        max_step = None
        p99_step = None
        min_len = median_len = max_len = None
        if final_def.exists() and final_v.exists():
            validation, max_step, p99_step, min_len, median_len, max_len = scan_jump_metrics(final_v, final_def)

        rows.append(
            VariantMetrics(
                platform=args.platform,
                design=args.design,
                variant=variant,
                tns=tns,
                wns=wns,
                wirelength=wl,
                chains=(validation.chains_found if validation else 0),
                min_len=min_len,
                median_len=median_len,
                max_len=max_len,
                max_step_um=max_step,
                p99_step_um=p99_step,
                broken_links=(validation.broken_links if validation else 0),
                orphan_cells=(validation.orphan_cells if validation else 0),
                duplicate_cells=(validation.duplicate_cells if validation else 0),
            )
        )

    print(
        "| variant | tns | wns | wirelength | chains | min_len | median | max_len | max_step_um | p99_step_um | broken | orphans | dups |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        print(
            "| "
            + " | ".join(
                [
                    r.variant,
                    fmt(r.tns),
                    fmt(r.wns),
                    fmt(r.wirelength, prec=0),
                    str(r.chains),
                    "" if r.min_len is None else str(r.min_len),
                    "" if r.median_len is None else str(r.median_len),
                    "" if r.max_len is None else str(r.max_len),
                    fmt(r.max_step_um),
                    fmt(r.p99_step_um),
                    str(r.broken_links),
                    str(r.orphan_cells),
                    str(r.duplicate_cells),
                ]
            )
            + " |"
        )

    if args.out_json:
        args.out_json.write_text(json.dumps([asdict(r) for r in rows], indent=2) + "\n")
        print(f"WROTE: {args.out_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
