#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ChainMetrics:
    name: str
    cells: int
    manhattan_dbu: int
    manhattan_um: Optional[float]
    avg_step_um: Optional[float]
    naive_lex_manhattan_um: Optional[float]
    naive_lex_ratio: Optional[float]
    nearest_neighbor_manhattan_um: Optional[float]
    openroad_over_nn_ratio: Optional[float]


def _tcl_quote(path: Path) -> str:
    return "{" + str(path) + "}"


def _write_scan_svg(
    out_svg: Path,
    *,
    chains: Dict[str, List[str]],
    pins_dbu: Dict[str, Tuple[int, int, int, int]],
    units_dbu_per_micron: int,
) -> None:
    def to_um(x_dbu: int, y_dbu: int) -> Tuple[float, float]:
        return (x_dbu / units_dbu_per_micron, y_dbu / units_dbu_per_micron)

    all_pts_um: List[Tuple[float, float]] = []
    for si_x, si_y, so_x, so_y in pins_dbu.values():
        all_pts_um.append(to_um(si_x, si_y))
        all_pts_um.append(to_um(so_x, so_y))

    xs = [x for x, _ in all_pts_um]
    ys = [y for _, y in all_pts_um]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    span_x = max_x - min_x
    span_y = max_y - min_y
    margin = max(10.0, 0.02 * max(span_x, span_y, 1.0))

    vb_w = span_x + 2 * margin
    vb_h = span_y + 2 * margin

    def to_svg_xy(x_um: float, y_um: float) -> Tuple[float, float]:
        # SVG Y grows down; flip so "lower-left" stays lower-left.
        x = x_um - (min_x - margin)
        y = (max_y + margin) - y_um
        return x, y

    colors = [
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
        "#9467bd",
        "#8c564b",
        "#e377c2",
        "#7f7f7f",
        "#bcbd22",
        "#17becf",
    ]

    lines: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {vb_w:.3f} {vb_h:.3f}" '
        'width="1200" height="1200" preserveAspectRatio="xMidYMid meet">',
        f'<rect x="0" y="0" width="{vb_w:.3f}" height="{vb_h:.3f}" '
        'fill="white" stroke="#ddd" stroke-width="0.5"/>',
    ]

    for idx, (chain_name, order) in enumerate(sorted(chains.items())):
        if len(order) < 2:
            continue
        color = colors[idx % len(colors)]
        stroke_w = max(0.15, 0.0008 * max(vb_w, vb_h))
        for src, dst in zip(order, order[1:]):
            _, _, so_x, so_y = pins_dbu[src]
            si_x, si_y, _, _ = pins_dbu[dst]
            x1, y1 = to_svg_xy(*to_um(so_x, so_y))
            x2, y2 = to_svg_xy(*to_um(si_x, si_y))
            lines.append(
                f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
                f'stroke="{color}" stroke-width="{stroke_w:.3f}" stroke-linecap="round" />'
            )

        start_si_x, start_si_y, _, _ = pins_dbu[order[0]]
        _, _, end_so_x, end_so_y = pins_dbu[order[-1]]
        sx, sy = to_svg_xy(*to_um(start_si_x, start_si_y))
        ex, ey = to_svg_xy(*to_um(end_so_x, end_so_y))
        r = max(0.8, 0.003 * max(vb_w, vb_h))
        lines.append(f'<circle cx="{sx:.3f}" cy="{sy:.3f}" r="{r:.3f}" fill="#2ca02c"/>')
        lines.append(f'<circle cx="{ex:.3f}" cy="{ey:.3f}" r="{r:.3f}" fill="#d62728"/>')
        lines.append(
            f'<text x="8" y="{18 + 16*idx}" font-size="12" fill="{color}">'
            f"{chain_name} ({len(order)} cells)</text>"
        )

    lines.append("</svg>")
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text("\n".join(lines) + "\n")


def run_openroad_plan(
    *,
    openroad_exe: Path,
    liberties: Sequence[Path],
    odb: Path,
    sdc: Path,
    max_chains: Optional[int],
    max_length: Optional[int],
    clock_mixing: str,
    do_scan_replace: bool,
    verbose: bool,
) -> str:
    tcl_lines: List[str] = [
        *[f"read_liberty {_tcl_quote(lib)}" for lib in liberties],
        f"read_db {_tcl_quote(odb)}",
    ]
    if sdc.exists():
        tcl_lines.append(f"read_sdc {_tcl_quote(sdc)}")
    set_dft_args = [f"-clock_mixing {clock_mixing}"]
    if max_length is not None:
        set_dft_args.append(f"-max_length {max_length}")
    if max_chains is not None:
        set_dft_args.append(f"-max_chains {max_chains}")
    tcl_lines.append(f"set_dft_config {' '.join(set_dft_args)}")
    if do_scan_replace:
        tcl_lines.append("scan_replace")
    tcl_lines += [
        "report_dft_plan_pins -verbose",
        "exit",
    ]

    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix="scan_chain_cost_",
        suffix=".tcl",
        delete=False,
        dir=os.getcwd(),
    ) as tcl_file:
        tcl_path = Path(tcl_file.name)
        tcl_file.write("\n".join(tcl_lines) + "\n")

    try:
        proc = subprocess.run(
            [str(openroad_exe), "-exit", str(tcl_path)],
            cwd=os.getcwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"OpenROAD failed (exit {proc.returncode}). Output:\n{proc.stdout}"
            )
        if verbose:
            print(proc.stdout, end="")
        return proc.stdout
    finally:
        try:
            tcl_path.unlink()
        except FileNotFoundError:
            pass


def parse_report_dft_plan_pins(
    openroad_output: str,
) -> Tuple[Optional[int], Dict[str, List[str]], Dict[str, Tuple[int, int, int, int]]]:
    dbu_per_micron: Optional[int] = None
    pins: Dict[str, Tuple[int, int, int, int]] = {}
    cells_by_chain: Dict[str, List[Tuple[int, str]]] = {}

    in_block = False
    for line in openroad_output.splitlines():
        line = line.strip()
        if line == "DFT_PLAN_PINS_BEGIN":
            in_block = True
            continue
        if line == "DFT_PLAN_PINS_END":
            break
        if not in_block or not line:
            continue

        if line.startswith("DFT_DBU_PER_UM "):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    dbu_per_micron = int(parts[1])
                except ValueError:
                    dbu_per_micron = None
            continue

        if line.startswith("DFT_CHAIN "):
            parts = line.split()
            if len(parts) >= 2:
                cells_by_chain.setdefault(parts[1], [])
            continue

        if line.startswith("DFT_CELL "):
            parts = line.split()
            if len(parts) < 8:
                continue
            chain = parts[1]
            try:
                idx = int(parts[2])
                inst = parts[3]
                si_x = int(parts[4])
                si_y = int(parts[5])
                so_x = int(parts[6])
                so_y = int(parts[7])
            except ValueError:
                continue
            cells_by_chain.setdefault(chain, []).append((idx, inst))
            pins[inst] = (si_x, si_y, so_x, so_y)

    chains: Dict[str, List[str]] = {}
    for chain, items in cells_by_chain.items():
        items.sort(key=lambda t: t[0])
        chains[chain] = [inst for _, inst in items]

    return dbu_per_micron, chains, pins


def scan_edge_cost_dbu(
    src: str, dst: str, pins_dbu: Dict[str, Tuple[int, int, int, int]]
) -> int:
    _, _, so_x, so_y = pins_dbu[src]
    si_x, si_y, _, _ = pins_dbu[dst]
    return abs(so_x - si_x) + abs(so_y - si_y)


def scan_path_cost_dbu(
    order: Sequence[str], pins_dbu: Dict[str, Tuple[int, int, int, int]]
) -> int:
    total = 0
    for src, dst in zip(order, order[1:]):
        total += scan_edge_cost_dbu(src, dst, pins_dbu)
    return total


def nearest_neighbor_scan_path_cost_dbu(
    cells: Sequence[str],
    pins_dbu: Dict[str, Tuple[int, int, int, int]],
    *,
    start: Optional[str] = None,
) -> int:
    if not cells:
        return 0
    if start is None:
        start = cells[0]
    if start not in pins_dbu:
        raise KeyError(start)

    remaining = set(cells)
    remaining.remove(start)
    cur = start
    total = 0

    while remaining:

        def key(inst: str) -> Tuple[int, str]:
            return (scan_edge_cost_dbu(cur, inst, pins_dbu), inst)

        nxt = min(remaining, key=key)
        total += scan_edge_cost_dbu(cur, nxt, pins_dbu)
        remaining.remove(nxt)
        cur = nxt

    return total


def compute_chain_metrics(
    chain_name: str,
    order: Sequence[str],
    pins_dbu: Dict[str, Tuple[int, int, int, int]],
    units: Optional[int],
    compute_nearest_neighbor: bool,
) -> ChainMetrics:
    manhattan_dbu = scan_path_cost_dbu(order, pins_dbu)
    manhattan_um = (manhattan_dbu / units) if units else None

    avg_step_um: Optional[float]
    if units and len(order) > 1:
        avg_step_um = (manhattan_dbu / units) / (len(order) - 1)
    else:
        avg_step_um = None

    naive_lex_manhattan_um: Optional[float]
    naive_lex_ratio: Optional[float]
    if units and len(order) > 1:
        naive_lex_dbu = scan_path_cost_dbu(sorted(order), pins_dbu)
        naive_lex_manhattan_um = naive_lex_dbu / units
        naive_lex_ratio = naive_lex_dbu / manhattan_dbu if manhattan_dbu else None
    else:
        naive_lex_manhattan_um = None
        naive_lex_ratio = None

    nearest_neighbor_manhattan_um: Optional[float]
    openroad_over_nn_ratio: Optional[float]
    if compute_nearest_neighbor and units and len(order) > 1:
        nn_dbu = nearest_neighbor_scan_path_cost_dbu(order, pins_dbu, start=order[0])
        nearest_neighbor_manhattan_um = nn_dbu / units
        openroad_over_nn_ratio = (manhattan_dbu / nn_dbu) if nn_dbu else None
    else:
        nearest_neighbor_manhattan_um = None
        openroad_over_nn_ratio = None

    return ChainMetrics(
        name=chain_name,
        cells=len(order),
        manhattan_dbu=manhattan_dbu,
        manhattan_um=manhattan_um,
        avg_step_um=avg_step_um,
        naive_lex_manhattan_um=naive_lex_manhattan_um,
        naive_lex_ratio=naive_lex_ratio,
        nearest_neighbor_manhattan_um=nearest_neighbor_manhattan_um,
        openroad_over_nn_ratio=openroad_over_nn_ratio,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compute a scan-chain length proxy from OpenROAD's "
            "`report_dft_plan_pins -verbose` output (scan-out -> scan-in pin-to-pin)."
        )
    )
    parser.add_argument("--openroad", required=True, type=Path)
    parser.add_argument(
        "--liberty",
        required=True,
        type=Path,
        action="append",
        help="Liberty file to load (repeatable).",
    )
    parser.add_argument("--odb", required=True, type=Path)
    parser.add_argument(
        "--sdc",
        required=True,
        type=Path,
        help="Used so `report_dft_plan_pins` can infer clock domains; still runs if missing.",
    )
    parser.add_argument(
        "--max-chains",
        type=int,
        default=None,
        help="Maximum number of scan chains (defaults to 1 unless --max-length is set).",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=None,
        help="Maximum scan chain length in bits (enables multiple chains unless capped by --max-chains).",
    )
    parser.add_argument("--clock-mixing", default="clock_mix")
    parser.add_argument(
        "--scan-replace",
        action="store_true",
        help="Run `scan_replace` before reporting the plan (useful for no-DFT ODBs).",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Write machine-readable metrics JSON to this path.",
    )
    parser.add_argument(
        "--out-svg",
        type=Path,
        default=None,
        help="Write an SVG visualization of the scan ordering (start=green, end=red).",
    )
    parser.add_argument(
        "--verbose-openroad",
        action="store_true",
        help="Print the full OpenROAD output (includes the full chain listing).",
    )
    parser.add_argument(
        "--nearest-neighbor",
        action="store_true",
        help="Compute a simple nearest-neighbor TSP heuristic for comparison.",
    )
    args = parser.parse_args()

    openroad_exe = args.openroad.resolve()
    liberties = [p.resolve() for p in args.liberty]
    odb = args.odb.resolve()
    sdc = args.sdc.resolve()

    for path in (openroad_exe, odb, *liberties):
        if not path.exists():
            raise FileNotFoundError(path)

    max_chains: Optional[int] = args.max_chains
    if max_chains is None and args.max_length is None:
        max_chains = 1

    openroad_output = run_openroad_plan(
        openroad_exe=openroad_exe,
        liberties=liberties,
        odb=odb,
        sdc=sdc,
        max_chains=max_chains,
        max_length=args.max_length,
        clock_mixing=args.clock_mixing,
        do_scan_replace=args.scan_replace,
        verbose=args.verbose_openroad,
    )

    units, chains, pins = parse_report_dft_plan_pins(openroad_output)
    if not chains:
        print("No scan chains found in `report_dft_plan_pins -verbose` output.")
        return 2

    needed = [inst for order in chains.values() for inst in order]
    dupes = [name for name, count in Counter(needed).items() if count > 1]
    if dupes:
        raise RuntimeError(
            "Duplicate scan cells in `report_dft_plan_pins -verbose` output. "
            f"First duplicate: {dupes[0]}"
        )

    missing = [inst for inst in needed if inst not in pins]
    if missing:
        raise RuntimeError(
            f"Missing {len(missing)}/{len(needed)} chain instances in pin report output. "
            f"First missing: {missing[0]}"
        )

    metrics = [
        compute_chain_metrics(
            name,
            order,
            pins,
            units,
            compute_nearest_neighbor=args.nearest_neighbor,
        )
        for name, order in chains.items()
    ]
    metrics.sort(key=lambda m: m.name)

    total_um = (
        sum(m.manhattan_um for m in metrics if m.manhattan_um is not None)
        if units
        else None
    )

    print(f"Chains: {len(metrics)}")
    if units:
        print(f"DBU per micron: {units}")
    for m in metrics:
        if m.manhattan_um is None:
            print(f"{m.name}: cells={m.cells} manhattan_dbu={m.manhattan_dbu}")
            continue
        print(
            f"{m.name}: cells={m.cells} "
            f"manhattan_um={m.manhattan_um:.3f} "
            f"avg_step_um={m.avg_step_um:.3f} "
            f"naive_lex_um={m.naive_lex_manhattan_um:.3f} "
            f"naive_lex_ratio={m.naive_lex_ratio:.3f}"
            + (
                f" nn_um={m.nearest_neighbor_manhattan_um:.3f} "
                f"openroad_over_nn={m.openroad_over_nn_ratio:.3f}"
                if m.nearest_neighbor_manhattan_um is not None
                and m.openroad_over_nn_ratio is not None
                else ""
            )
        )
    if total_um is not None:
        print(f"total_manhattan_um={total_um:.3f}")
    edges = sum(max(0, len(order) - 1) for order in chains.values())
    print(f"total_cells={len(needed)} total_edges={edges}")

    if args.out_json:
        payload = {
            "units_dbu_per_micron": units,
            "chains": [asdict(m) for m in metrics],
            "total_manhattan_um": total_um,
        }
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(payload, indent=2, sort_keys=True))

    if args.out_svg:
        if not units:
            raise RuntimeError("DBU-per-micron missing; cannot write SVG.")
        _write_scan_svg(
            args.out_svg.resolve(),
            chains=chains,
            pins_dbu=pins,
            units_dbu_per_micron=units,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
