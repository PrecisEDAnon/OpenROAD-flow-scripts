#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


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
    coords_dbu: Dict[str, Tuple[int, int]],
    units_dbu_per_micron: int,
) -> None:
    coords_um = {
        inst: (x / units_dbu_per_micron, y / units_dbu_per_micron)
        for inst, (x, y) in coords_dbu.items()
    }

    xs = [x for x, _ in coords_um.values()]
    ys = [y for _, y in coords_um.values()]
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
        if not order:
            continue
        color = colors[idx % len(colors)]
        pts: List[str] = []
        for inst in order:
            x_um, y_um = coords_um[inst]
            x, y = to_svg_xy(x_um, y_um)
            pts.append(f"{x:.3f},{y:.3f}")

        stroke_w = max(0.15, 0.0008 * max(vb_w, vb_h))
        lines.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="{stroke_w:.3f}" '
            f'stroke-linejoin="round" stroke-linecap="round" points="{" ".join(pts)}"/>'
        )

        sx, sy = to_svg_xy(*coords_um[order[0]])
        ex, ey = to_svg_xy(*coords_um[order[-1]])
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
    out_def: Path,
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
        "report_dft_plan -verbose",
        f"write_def {_tcl_quote(out_def)}",
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


def parse_report_dft_plan_verbose(openroad_output: str) -> Dict[str, List[str]]:
    chains: Dict[str, List[str]] = {}
    current: Optional[str] = None

    chain_re = re.compile(r"^Scan chain '([^']+)' has (\d+) cells")
    for line in openroad_output.splitlines():
        m = chain_re.match(line)
        if m:
            current = m.group(1)
            chains[current] = []
            continue
        if current is None:
            continue
        if line.startswith("  "):
            chains[current].append(line.strip().split()[0])

    return chains


def parse_def_units_and_coords(
    def_path: Path, needed_insts: Iterable[str]
) -> Tuple[Optional[int], Dict[str, Tuple[int, int]]]:
    needed = set(needed_insts)
    coords: Dict[str, Tuple[int, int]] = {}
    units: Optional[int] = None

    in_components = False
    place_re = re.compile(
        r"\+\s+(?:PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", re.IGNORECASE
    )
    component_buf: List[str] = []

    with def_path.open() as f:
        for line in f:
            if units is None:
                m = re.match(r"^UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", line)
                if m:
                    units = int(m.group(1))

            stripped = line.lstrip()
            if stripped.startswith("COMPONENTS"):
                in_components = True
                continue
            if stripped.startswith("END COMPONENTS"):
                in_components = False
                if len(coords) == len(needed):
                    break
                continue
            if not in_components:
                continue

            if not component_buf:
                if not stripped.startswith("-"):
                    continue
                component_buf = [stripped.rstrip("\n")]
            else:
                component_buf.append(stripped.rstrip("\n"))

            if ";" not in stripped:
                continue

            component = " ".join(component_buf)
            component_buf = []

            # First two tokens are: - <inst> <master> ...
            tokens = component.split()
            if len(tokens) < 3 or tokens[0] != "-":
                continue
            inst_name = tokens[1]
            if inst_name not in needed:
                continue

            m = place_re.search(component)
            if not m:
                continue
            coords[inst_name] = (int(m.group(1)), int(m.group(2)))
            if len(coords) == len(needed):
                break

    return units, coords


def manhattan_path_dbu(order: Sequence[str], coords: Dict[str, Tuple[int, int]]) -> int:
    total = 0
    last_xy: Optional[Tuple[int, int]] = None
    for inst in order:
        xy = coords[inst]
        if last_xy is not None:
            total += abs(xy[0] - last_xy[0]) + abs(xy[1] - last_xy[1])
        last_xy = xy
    return total


def nearest_neighbor_manhattan_path_dbu(
    order: Sequence[str], coords: Dict[str, Tuple[int, int]], *, start: Optional[str] = None
) -> int:
    if not order:
        return 0
    if start is None:
        start = order[0]
    if start not in coords:
        raise KeyError(start)

    remaining = set(order)
    remaining.remove(start)
    cur = start
    total = 0

    while remaining:
        cx, cy = coords[cur]

        def key(inst: str) -> Tuple[int, str]:
            x, y = coords[inst]
            return (abs(x - cx) + abs(y - cy), inst)

        nxt = min(remaining, key=key)
        x, y = coords[nxt]
        total += abs(x - cx) + abs(y - cy)
        remaining.remove(nxt)
        cur = nxt

    return total


def compute_chain_metrics(
    chain_name: str,
    order: Sequence[str],
    coords: Dict[str, Tuple[int, int]],
    units: Optional[int],
    compute_nearest_neighbor: bool,
) -> ChainMetrics:
    manhattan_dbu = manhattan_path_dbu(order, coords)
    manhattan_um = (manhattan_dbu / units) if units else None

    avg_step_um: Optional[float]
    if units and len(order) > 1:
        avg_step_um = (manhattan_dbu / units) / (len(order) - 1)
    else:
        avg_step_um = None

    naive_lex_manhattan_um: Optional[float]
    naive_lex_ratio: Optional[float]
    if units and len(order) > 1:
        naive_lex_dbu = manhattan_path_dbu(sorted(order), coords)
        naive_lex_manhattan_um = naive_lex_dbu / units
        naive_lex_ratio = naive_lex_dbu / manhattan_dbu if manhattan_dbu else None
    else:
        naive_lex_manhattan_um = None
        naive_lex_ratio = None

    nearest_neighbor_manhattan_um: Optional[float]
    openroad_over_nn_ratio: Optional[float]
    if compute_nearest_neighbor and units and len(order) > 1:
        nn_dbu = nearest_neighbor_manhattan_path_dbu(order, coords, start=order[0])
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
            "Compute a TSP-like scan-chain length metric from OpenROAD's "
            "`report_dft_plan -verbose` output and instance origins."
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
        help="Used so `report_dft_plan` can infer clock domains; still runs if missing.",
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

    with tempfile.TemporaryDirectory(prefix="scan_chain_cost_", dir=os.getcwd()) as td:
        tmp_dir = Path(td)
        out_def = tmp_dir / "design.def"
        max_chains: Optional[int] = args.max_chains
        if max_chains is None and args.max_length is None:
            max_chains = 1

        openroad_output = run_openroad_plan(
            openroad_exe=openroad_exe,
            liberties=liberties,
            odb=odb,
            sdc=sdc,
            out_def=out_def,
            max_chains=max_chains,
            max_length=args.max_length,
            clock_mixing=args.clock_mixing,
            do_scan_replace=args.scan_replace,
            verbose=args.verbose_openroad,
        )

        chains = parse_report_dft_plan_verbose(openroad_output)
        if not chains:
            print("No scan chains found in `report_dft_plan -verbose` output.")
            return 2

        needed = [inst for order in chains.values() for inst in order]
        dupes = [name for name, count in Counter(needed).items() if count > 1]
        if dupes:
            raise RuntimeError(
                "Duplicate scan cells in `report_dft_plan -verbose` output. "
                f"First duplicate: {dupes[0]}"
            )
        units, coords = parse_def_units_and_coords(out_def, needed)

        missing = [inst for inst in needed if inst not in coords]
        if missing:
            raise RuntimeError(
                f"Missing {len(missing)}/{len(needed)} chain instances in DEF output. "
                f"First missing: {missing[0]}"
            )

        metrics = [
            compute_chain_metrics(
                name,
                order,
                coords,
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
            print(f"DEF units: {units} DBU per micron")
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
                raise RuntimeError("DEF units missing; cannot write SVG.")
            _write_scan_svg(
                args.out_svg.resolve(),
                chains=chains,
                coords_dbu=coords,
                units_dbu_per_micron=units,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
