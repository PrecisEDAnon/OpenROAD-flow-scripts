#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from scan_chain_plot import parse_def_placements, reconstruct_chains_from_verilog


def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if p <= 0.0:
        return sorted_vals[0]
    if p >= 1.0:
        return sorted_vals[-1]
    idx = int(math.ceil(p * len(sorted_vals))) - 1
    idx = max(0, min(idx, len(sorted_vals) - 1))
    return sorted_vals[idx]


@dataclass(frozen=True)
class ChainMetrics:
    chain_idx: int
    scan_in: str
    scan_out: str
    cells: int
    internal_um: float
    io_in_um: Optional[float]
    io_out_um: Optional[float]
    total_um: float
    max_step_um: float
    p99_step_um: float


@dataclass(frozen=True)
class Summary:
    chains: int
    total_cells: int
    min_cells: int
    median_cells: int
    max_cells: int
    imbalance_percent: float
    total_internal_um: float
    total_io_um: float
    total_um: float
    max_step_um: float
    p99_step_um: float
    per_chain: List[ChainMetrics]


def _median_int(vals: Sequence[int]) -> int:
    vals = sorted(vals)
    if not vals:
        return 0
    mid = len(vals) // 2
    if len(vals) % 2 == 1:
        return vals[mid]
    return int((vals[mid - 1] + vals[mid]) / 2)


def compute_metrics(
    *,
    verilog_path: Path,
    def_path: Path,
    scan_in: str,
    scan_out: str,
    auto_chains: bool,
    scan_in_prefix: str,
    scan_out_prefix: str,
    max_chain_count: int,
) -> Summary:
    placements_um, pins_um, _diearea = parse_def_placements(def_path)
    chains, errors = reconstruct_chains_from_verilog(
        verilog_path,
        scan_in=scan_in,
        scan_out=scan_out,
        auto_chains=auto_chains,
        scan_in_prefix=scan_in_prefix,
        scan_out_prefix=scan_out_prefix,
        max_chain_count=max_chain_count,
    )
    if errors:
        raise RuntimeError("Scan chain reconstruction failed:\n- " + "\n- ".join(errors))

    per_chain: List[ChainMetrics] = []
    all_steps: List[float] = []

    total_internal = 0.0
    total_io = 0.0

    chain_lens = [len(c.cells) for c in chains if c.cells]
    if not chain_lens:
        raise RuntimeError("No scan chains found in netlist.")

    for idx, chain in enumerate(chains):
        if not chain.cells:
            continue

        pts = [placements_um[name] for name in chain.cells]
        steps = [
            abs(x1 - x0) + abs(y1 - y0)
            for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:])
        ]
        steps_sorted = sorted(steps)
        internal = float(sum(steps))
        total_internal += internal
        all_steps.extend(steps)

        io_in = None
        io_out = None
        if chain.scan_in in pins_um and pts:
            px, py = pins_um[chain.scan_in]
            x0, y0 = pts[0]
            io_in = abs(px - x0) + abs(py - y0)
        if chain.scan_out in pins_um and pts:
            px, py = pins_um[chain.scan_out]
            x1, y1 = pts[-1]
            io_out = abs(px - x1) + abs(py - y1)

        io_sum = float((io_in or 0.0) + (io_out or 0.0))
        total_io += io_sum

        max_step = float(steps_sorted[-1]) if steps_sorted else 0.0
        p99 = float(_percentile(steps_sorted, 0.99)) if steps_sorted else 0.0

        per_chain.append(
            ChainMetrics(
                chain_idx=idx,
                scan_in=chain.scan_in,
                scan_out=chain.scan_out,
                cells=len(chain.cells),
                internal_um=internal,
                io_in_um=io_in,
                io_out_um=io_out,
                total_um=internal + io_sum,
                max_step_um=max_step,
                p99_step_um=p99,
            )
        )

    all_steps_sorted = sorted(all_steps)
    max_step_all = float(all_steps_sorted[-1]) if all_steps_sorted else 0.0
    p99_step_all = float(_percentile(all_steps_sorted, 0.99)) if all_steps_sorted else 0.0

    min_cells = min(chain_lens)
    max_cells = max(chain_lens)
    median_cells = _median_int(chain_lens)
    imbalance_percent = 0.0
    if min_cells > 0 and len(chain_lens) > 1:
        imbalance_percent = 100.0 * ((max_cells / min_cells) - 1.0)

    return Summary(
        chains=len(chain_lens),
        total_cells=sum(chain_lens),
        min_cells=min_cells,
        median_cells=median_cells,
        max_cells=max_cells,
        imbalance_percent=imbalance_percent,
        total_internal_um=total_internal,
        total_io_um=total_io,
        total_um=total_internal + total_io,
        max_step_um=max_step_all,
        p99_step_um=p99_step_all,
        per_chain=per_chain,
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Compute scan-chain Manhattan cost metrics from DEF + stitched Verilog."
        )
    )
    ap.add_argument("--verilog", type=Path, required=True)
    ap.add_argument("--def", dest="def_path", type=Path, required=True)
    ap.add_argument("--scan-in", default="scan_in_0")
    ap.add_argument("--scan-out", default="scan_out_0")
    ap.add_argument("--auto-chains", action="store_true", default=True)
    ap.add_argument("--scan-in-prefix", default="scan_in_")
    ap.add_argument("--scan-out-prefix", default="scan_out_")
    ap.add_argument("--max-chain-count", type=int, default=256)
    ap.add_argument("--out-json", type=Path)
    args = ap.parse_args(argv)

    s = compute_metrics(
        verilog_path=args.verilog,
        def_path=args.def_path,
        scan_in=args.scan_in,
        scan_out=args.scan_out,
        auto_chains=args.auto_chains,
        scan_in_prefix=args.scan_in_prefix,
        scan_out_prefix=args.scan_out_prefix,
        max_chain_count=args.max_chain_count,
    )

    print(
        "scan_chain_metrics:"
        f" chains={s.chains}"
        f" total_cells={s.total_cells}"
        f" min/med/max={s.min_cells}/{s.median_cells}/{s.max_cells}"
        f" imbalance={s.imbalance_percent:.3f}%"
        f" total_um={s.total_um:.3f}"
        f" (internal={s.total_internal_um:.3f}, io={s.total_io_um:.3f})"
        f" max_step_um={s.max_step_um:.3f}"
        f" p99_step_um={s.p99_step_um:.3f}"
    )
    for c in sorted(s.per_chain, key=lambda x: x.chain_idx):
        io_in = "NA" if c.io_in_um is None else f"{c.io_in_um:.3f}"
        io_out = "NA" if c.io_out_um is None else f"{c.io_out_um:.3f}"
        print(
            f"  chain_{c.chain_idx}: cells={c.cells} total_um={c.total_um:.3f}"
            f" (internal={c.internal_um:.3f}, io_in={io_in}, io_out={io_out})"
            f" max_step_um={c.max_step_um:.3f} p99_step_um={c.p99_step_um:.3f}"
        )

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(asdict(s), indent=2) + "\n")
        print(f"WROTE: {args.out_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

