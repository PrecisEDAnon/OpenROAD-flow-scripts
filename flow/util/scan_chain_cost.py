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
    p99_step_dbu: int
    p99_step_um: Optional[float]
    max_step_dbu: int
    max_step_um: Optional[float]
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
    out_pins_tsv: Path,
    max_chains: Optional[int],
    max_length: Optional[int],
    clock_mixing: str,
    scan_order_metric: Optional[str],
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
    if scan_order_metric:
        set_dft_args.append(f"-scan_order_metric {scan_order_metric}")
    tcl_lines.append(f"set_dft_config {' '.join(set_dft_args)}")
    if do_scan_replace:
        tcl_lines.append("scan_replace")
    tcl_lines += [
        "report_dft_plan -verbose",
        # Export per-instance scan in/out pin locations (DBU) for pin-based
        # scan-chain cost metrics (supports asymmetric costs).
        f"set __dft_pins_fh [open {_tcl_quote(out_pins_tsv)} w]",
        'puts $__dft_pins_fh "name\\tin_x\\tin_y\\tout_x\\tout_y"',
        "set __dft_block [ord::get_db_block]",
        "foreach __dft_inst [$__dft_block getInsts] {",
        "  set __dft_name [$__dft_inst getName]",
        "  set __dft_in_iterm NULL",
        "  foreach __dft_pin {SI SD SCD SCAN_IN SCANIN} {",
        "    set __dft_t [$__dft_inst findITerm $__dft_pin]",
        "    if { $__dft_t != \"NULL\" } { set __dft_in_iterm $__dft_t; break }",
        "  }",
        "  if { $__dft_in_iterm == \"NULL\" } { continue }",
        "  set __dft_out_iterm NULL",
        "  foreach __dft_pin {SO SCO SCAN_OUT SCANOUT Q QN Q_N} {",
        "    set __dft_t [$__dft_inst findITerm $__dft_pin]",
        "    if { $__dft_t != \"NULL\" } { set __dft_out_iterm $__dft_t; break }",
        "  }",
        "  if { $__dft_out_iterm == \"NULL\" } { continue }",
        "  lassign [$__dft_inst getLocation] __dft_fx __dft_fy",
        "  set __dft_in_x $__dft_fx",
        "  set __dft_in_y $__dft_fy",
        "  if { ![catch { set __dft_bb [$__dft_in_iterm getBBox] } __dft_err] } {",
        "    set __dft_in_x [$__dft_bb xMin]",
        "    set __dft_in_y [$__dft_bb yMin]",
        "  }",
        "  set __dft_out_x $__dft_fx",
        "  set __dft_out_y $__dft_fy",
        "  if { ![catch { set __dft_bb2 [$__dft_out_iterm getBBox] } __dft_err] } {",
        "    set __dft_out_x [$__dft_bb2 xMin]",
        "    set __dft_out_y [$__dft_bb2 yMin]",
        "  }",
        '  puts $__dft_pins_fh "${__dft_name}\\t${__dft_in_x}\\t${__dft_in_y}\\t${__dft_out_x}\\t${__dft_out_y}"',
        "}",
        "close $__dft_pins_fh",
        f"write_def {_tcl_quote(out_def)}",
        "exit",
    ]

    tmp_dir = Path.cwd() / "dft_artifacts" / "tmp_tcl"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix="scan_chain_cost_",
        suffix=".tcl",
        delete=False,
        dir=str(tmp_dir),
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


def parse_def_pins(
    def_path: Path, needed_pins: Iterable[str]
) -> Tuple[Optional[int], Dict[str, Tuple[int, int]]]:
    needed = set(needed_pins)
    coords: Dict[str, Tuple[int, int]] = {}
    units: Optional[int] = None

    in_pins = False
    place_re = re.compile(r"\+\s+(?:PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", re.IGNORECASE)
    pin_buf: List[str] = []

    with def_path.open() as f:
        for line in f:
            if units is None:
                m = re.match(r"^UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", line)
                if m:
                    units = int(m.group(1))

            stripped = line.lstrip()
            if stripped.startswith("PINS"):
                in_pins = True
                continue
            if stripped.startswith("END PINS"):
                in_pins = False
                if len(coords) == len(needed):
                    break
                continue
            if not in_pins:
                continue

            if not pin_buf:
                if not stripped.startswith("-"):
                    continue
                pin_buf = [stripped.rstrip("\n")]
            else:
                pin_buf.append(stripped.rstrip("\n"))

            if ";" not in stripped:
                continue

            rec = " ".join(pin_buf)
            pin_buf = []
            toks = rec.split()
            if len(toks) < 2 or toks[0] != "-":
                continue
            pin_name = toks[1]
            if pin_name not in needed:
                continue
            m = place_re.search(rec)
            if not m:
                continue
            coords[pin_name] = (int(m.group(1)), int(m.group(2)))
            if len(coords) == len(needed):
                break

    return units, coords


def parse_scanff_pins_tsv(path: Path) -> Dict[str, Tuple[int, int, int, int]]:
    pins: Dict[str, Tuple[int, int, int, int]] = {}
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        return pins
    start = 0
    if lines[0].startswith("name\t"):
        start = 1
    for raw in lines[start:]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        name, in_x, in_y, out_x, out_y = parts
        pins[name] = (int(in_x), int(in_y), int(out_x), int(out_y))
    return pins


def manhattan_path_dbu(
    order: Sequence[str],
    pins: Dict[str, Tuple[int, int, int, int]],
    *,
    begin: Optional[Tuple[int, int]] = None,
    end: Optional[Tuple[int, int]] = None,
) -> int:
    total = 0
    last: Optional[Tuple[int, int]] = None
    for idx, inst in enumerate(order):
        in_x, in_y, out_x, out_y = pins[inst]
        if idx == 0 and begin is not None:
            total += abs(begin[0] - in_x) + abs(begin[1] - in_y)
        if last is not None:
            total += abs(last[0] - in_x) + abs(last[1] - in_y)
        last = (out_x, out_y)
    if last is not None and end is not None:
        total += abs(last[0] - end[0]) + abs(last[1] - end[1])
    return total


def manhattan_steps_dbu(order: Sequence[str], pins: Dict[str, Tuple[int, int, int, int]]) -> List[int]:
    steps: List[int] = []
    for a, b in zip(order[:-1], order[1:]):
        _, _, a_out_x, a_out_y = pins[a]
        b_in_x, b_in_y, _, _ = pins[b]
        steps.append(abs(a_out_x - b_in_x) + abs(a_out_y - b_in_y))
    return steps


def _pctl_nearest_rank(sorted_vals: Sequence[int], p: float) -> int:
    if not sorted_vals:
        return 0
    # Nearest-rank percentile: https://en.wikipedia.org/wiki/Percentile#The_nearest-rank_method
    import math

    idx = max(0, min(len(sorted_vals) - 1, math.ceil(p * len(sorted_vals)) - 1))
    return int(sorted_vals[idx])


def nearest_neighbor_manhattan_path_dbu(
    order: Sequence[str],
    pins: Dict[str, Tuple[int, int, int, int]],
    *,
    begin: Optional[Tuple[int, int]] = None,
    end: Optional[Tuple[int, int]] = None,
) -> int:
    if not order:
        return 0
    remaining = set(order)
    total = 0

    # Pick the start node by begin->in cost when begin is available, otherwise
    # use the first node in the provided order.
    if begin is None:
        cur = order[0]
    else:
        cur = min(
            remaining,
            key=lambda inst: (
                abs(begin[0] - pins[inst][0]) + abs(begin[1] - pins[inst][1]),
                inst,
            ),
        )
        total += abs(begin[0] - pins[cur][0]) + abs(begin[1] - pins[cur][1])

    remaining.remove(cur)

    while remaining:
        _, _, cx, cy = pins[cur]

        def key(inst: str) -> Tuple[int, str]:
            in_x, in_y, _, _ = pins[inst]
            return (abs(in_x - cx) + abs(in_y - cy), inst)

        nxt = min(remaining, key=key)
        in_x, in_y, _, _ = pins[nxt]
        total += abs(in_x - cx) + abs(in_y - cy)
        remaining.remove(nxt)
        cur = nxt

    if end is not None:
        _, _, ox, oy = pins[cur]
        total += abs(ox - end[0]) + abs(oy - end[1])

    return total


def compute_chain_metrics(
    chain_name: str,
    order: Sequence[str],
    pins: Dict[str, Tuple[int, int, int, int]],
    units: Optional[int],
    compute_nearest_neighbor: bool,
    *,
    begin: Optional[Tuple[int, int]] = None,
    end: Optional[Tuple[int, int]] = None,
) -> ChainMetrics:
    steps_dbu = manhattan_steps_dbu(order, pins)
    steps_sorted = sorted(steps_dbu)
    p99_step_dbu = _pctl_nearest_rank(steps_sorted, 0.99)
    max_step_dbu = max(steps_dbu) if steps_dbu else 0

    manhattan_dbu = manhattan_path_dbu(order, pins, begin=begin, end=end)
    manhattan_um = (manhattan_dbu / units) if units else None

    avg_step_um: Optional[float]
    if units and len(order) > 1:
        avg_step_um = (manhattan_dbu / units) / (len(order) - 1)
    else:
        avg_step_um = None

    p99_step_um: Optional[float] = (p99_step_dbu / units) if units else None
    max_step_um: Optional[float] = (max_step_dbu / units) if units else None

    naive_lex_manhattan_um: Optional[float]
    naive_lex_ratio: Optional[float]
    if units and len(order) > 1:
        naive_lex_dbu = manhattan_path_dbu(sorted(order), pins, begin=begin, end=end)
        naive_lex_manhattan_um = naive_lex_dbu / units
        naive_lex_ratio = naive_lex_dbu / manhattan_dbu if manhattan_dbu else None
    else:
        naive_lex_manhattan_um = None
        naive_lex_ratio = None

    nearest_neighbor_manhattan_um: Optional[float]
    openroad_over_nn_ratio: Optional[float]
    if compute_nearest_neighbor and units and len(order) > 1:
        nn_dbu = nearest_neighbor_manhattan_path_dbu(order, pins, begin=begin, end=end)
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
        p99_step_dbu=p99_step_dbu,
        p99_step_um=p99_step_um,
        max_step_dbu=max_step_dbu,
        max_step_um=max_step_um,
        naive_lex_manhattan_um=naive_lex_manhattan_um,
        naive_lex_ratio=naive_lex_ratio,
        nearest_neighbor_manhattan_um=nearest_neighbor_manhattan_um,
        openroad_over_nn_ratio=openroad_over_nn_ratio,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compute a TSP-like scan-chain length metric from OpenROAD's "
            "`report_dft_plan -verbose` output and scan pin locations."
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
        "--scan-order-metric",
        type=lambda v: v.strip().upper().replace("-", "_"),
        default=None,
        help="Optional scan ordering metric: PLACEMENT or PIN_TO_NET (requires OpenROAD support).",
    )
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
    parser.add_argument("--scan-in-prefix", default="scan_in_")
    parser.add_argument("--scan-out-prefix", default="scan_out_")
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
        out_pins_tsv = tmp_dir / "scanff_pins.tsv"
        max_chains: Optional[int] = args.max_chains
        if max_chains is None and args.max_length is None:
            max_chains = 1

        openroad_output = run_openroad_plan(
            openroad_exe=openroad_exe,
            liberties=liberties,
            odb=odb,
            sdc=sdc,
            out_def=out_def,
            out_pins_tsv=out_pins_tsv,
            max_chains=max_chains,
            max_length=args.max_length,
            clock_mixing=args.clock_mixing,
            scan_order_metric=args.scan_order_metric,
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

        pins = parse_scanff_pins_tsv(out_pins_tsv)
        missing_pins = [inst for inst in needed if inst not in pins]
        if missing_pins:
            raise RuntimeError(
                f"Missing {len(missing_pins)}/{len(needed)} scan pin locations in TSV output. "
                f"First missing: {missing_pins[0]}"
            )

        # Optional begin/end port locations (DBU), inferred by ordinal.
        needed_ports: List[str] = []
        for i in range(len(chains)):
            needed_ports.append(f"{args.scan_in_prefix}{i}")
            needed_ports.append(f"{args.scan_out_prefix}{i}")
        _, port_xy = parse_def_pins(out_def, needed_ports)

        metrics = [
            compute_chain_metrics(
                name,
                order,
                pins,
                units,
                compute_nearest_neighbor=args.nearest_neighbor,
                begin=port_xy.get(f"{args.scan_in_prefix}{idx}"),
                end=port_xy.get(f"{args.scan_out_prefix}{idx}"),
            )
            for idx, (name, order) in enumerate(chains.items())
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
                f"p99_step_um={m.p99_step_um:.3f} "
                f"max_step_um={m.max_step_um:.3f} "
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
                coords_dbu={name: (pins[name][0], pins[name][1]) for name in needed},
                units_dbu_per_micron=units,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
