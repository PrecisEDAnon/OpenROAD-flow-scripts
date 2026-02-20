#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from scan_chain_plot import parse_def_placements, reconstruct_chains_from_verilog
from scan_chain_validate import ValidationSummary, validate_netlist


@dataclass(frozen=True)
class ChainStepMetrics:
    chain: str
    scan_in: str
    scan_out: str
    cells: int
    total_step_um: float
    max_step_um: float
    p99_step_um: float
    io_in_um: Optional[float]
    io_out_um: Optional[float]


@dataclass(frozen=True)
class RunMetrics:
    tag: str
    requested_chain_count: int
    max_imbalance: float
    openroad_runtime_s: Optional[float]
    scan_cells_found: int
    chains_found: int
    min_chain_len: Optional[int]
    median_chain_len: Optional[int]
    max_chain_len: Optional[int]
    total_internal_um: Optional[float]
    total_io_um: Optional[float]
    total_um: Optional[float]
    max_step_um: Optional[float]
    p99_step_um: Optional[float]
    chains: List[ChainStepMetrics]
    validation: ValidationSummary


def _tcl_quote(path: Path) -> str:
    return "{" + str(path) + "}"


def _load_plot_summary(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_int_list(s: str) -> List[int]:
    out: List[int] = []
    for tok in s.split(","):
        tok = tok.strip()
        if tok:
            out.append(int(tok))
    if not out:
        raise ValueError("empty list")
    return out


def _parse_float_list(s: str) -> List[float]:
    out: List[float] = []
    for tok in s.split(","):
        tok = tok.strip()
        if tok:
            out.append(float(tok))
    if not out:
        raise ValueError("empty list")
    return out


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


def run_openroad_execute_dft_plan(
    *,
    openroad_exe: Path,
    liberties: Sequence[Path],
    odb: Path,
    sdc: Optional[Path],
    out_def: Path,
    out_verilog: Path,
    out_odb: Optional[Path],
    chain_count: int,
    max_imbalance: float,
    clock_mixing: str,
    polarity_mode: str,
    scan_order_metric: Optional[str],
    scan_order_solver: str,
    ucla_major_loops: Optional[int],
    scanopt_rounds: Optional[int],
    scanopt_seed: Optional[int],
    scanopt_time_limit: Optional[float],
    insert_lockup: int,
    scan_replace: bool,
    constraints_file: Optional[Path],
    io_placer_h: Optional[str],
    io_placer_v: Optional[str],
    out_log: Path,
    echo_openroad: bool,
) -> float:
    tcl: List[str] = []
    for lib in liberties:
        tcl.append(f"read_liberty {_tcl_quote(lib)}")
    tcl.append(f"read_db {_tcl_quote(odb)}")
    if sdc and sdc.exists():
        tcl.append(f"read_sdc {_tcl_quote(sdc)}")

    tcl += [
        "proc ensure_scan_port {port_name io_type} {",
        "  set block [ord::get_db_block]",
        "  set bterm [$block findBTerm $port_name]",
        "  if { $bterm != \"NULL\" } { return }",
        "  set net [$block findNet $port_name]",
        "  if { $net == \"NULL\" } {",
        "    set net [odb::dbNet_create $block $port_name]",
        "    $net setSigType SCAN",
        "  }",
        "  set bterm [odb::dbBTerm_create $net $port_name]",
        "  $bterm setSigType SCAN",
        "  $bterm setIoType $io_type",
        "}",
        "proc place_pin_safe {pin_name layer_name x_dbu y_dbu} {",
        "  if { $layer_name == \"\" } { return }",
        "  if { [info commands place_pin] == \"\" } { return }",
        "  set db [ord::get_db]",
        "  set tech [$db getTech]",
        "  set mgrid [$tech getManufacturingGrid]",
        "  if { $mgrid == \"\" || $mgrid <= 0 } { set mgrid 1 }",
        "  set x_dbu [expr {int(round(double($x_dbu) / $mgrid)) * $mgrid}]",
        "  set y_dbu [expr {int(round(double($y_dbu) / $mgrid)) * $mgrid}]",
        "  set x_um [ord::dbu_to_microns $x_dbu]",
        "  set y_um [ord::dbu_to_microns $y_dbu]",
        "  catch { place_pin -pin_name $pin_name -layer $layer_name -location [list $x_um $y_um] }",
        "}",
        f"set chain_count {chain_count}",
        "set scan_enable_name \"scan_enable_0\"",
        "ensure_scan_port $scan_enable_name INPUT",
        "for { set i 0 } { $i < $chain_count } { incr i } {",
        "  ensure_scan_port \"scan_in_${i}\" INPUT",
        "  ensure_scan_port \"scan_out_${i}\" OUTPUT",
        "}",
    ]

    if io_placer_h and io_placer_v:
        tcl += [
            f"set ::env(IO_PLACER_H) [list {io_placer_h}]",
            f"set ::env(IO_PLACER_V) [list {io_placer_v}]",
        ]

    tcl += [
        "if { [info exists ::env(IO_PLACER_H)] && [info exists ::env(IO_PLACER_V)] } {",
        "  set block [ord::get_db_block]",
        "  set die_rect [$block getDieArea]",
        "  set xMin [$die_rect xMin]",
        "  set yMin [$die_rect yMin]",
        "  set xMax [$die_rect xMax]",
        "  set yMax [$die_rect yMax]",
        "  set mid_x [expr {($xMin + $xMax) / 2}]",
        "  set h_layer [lindex $::env(IO_PLACER_H) 0]",
        "  set v_layer [lindex $::env(IO_PLACER_V) 0]",
        "  set enable_layer $v_layer",
        "  if { $enable_layer == \"\" } { set enable_layer $h_layer }",
        "  place_pin_safe $scan_enable_name $enable_layer $mid_x $yMin",
        "  set io_layer $h_layer",
        "  if { $io_layer == \"\" } { set io_layer $v_layer }",
        "  for { set i 0 } { $i < $chain_count } { incr i } {",
        "    set y [expr {$yMin + int((($i + 1.0) / ($chain_count + 1.0)) * ($yMax - $yMin))}]",
        "    place_pin_safe \"scan_in_${i}\" $io_layer $xMin $y",
        "    place_pin_safe \"scan_out_${i}\" $io_layer $xMax $y",
        "  }",
        "}",
    ]

    set_dft_args: List[str] = [
        f"-chain_count {chain_count}",
        f"-max_imbalance {max_imbalance}",
        f"-clock_mixing {clock_mixing}",
        f"-polarity_mode {polarity_mode}",
        f"-scan_order_solver {scan_order_solver}",
        "-scan_enable_name_pattern scan_enable_{}",
        "-scan_in_name_pattern scan_in_{}",
        "-scan_out_name_pattern scan_out_{}",
    ]
    if scan_order_metric:
        set_dft_args.append(f"-scan_order_metric {scan_order_metric}")
    if ucla_major_loops is not None:
        set_dft_args.append(f"-ucla_major_loops {ucla_major_loops}")
    if scanopt_rounds is not None:
        set_dft_args.append(f"-scanopt_rounds {scanopt_rounds}")
    if scanopt_seed is not None:
        set_dft_args.append(f"-scanopt_seed {scanopt_seed}")
    if scanopt_time_limit is not None:
        set_dft_args.append(f"-scanopt_time_limit {scanopt_time_limit}")
    # Default to not inserting lockups. This keeps scan planning focused on
    # stitching order/cost and avoids requiring lockup cell configuration when
    # using clock/edge mixing modes.
    set_dft_args.append(f"-insert_lockup {int(insert_lockup)}")
    if constraints_file:
        set_dft_args.append(
            f"-scan_order_constraints_file {_tcl_quote(constraints_file)}"
        )

    tcl += [
        "set_dft_config " + " ".join(set_dft_args),
    ]

    if scan_replace:
        tcl.append("scan_replace")

    tcl += [
        "execute_dft_plan",
        f"write_def {_tcl_quote(out_def)}",
        f"write_verilog {_tcl_quote(out_verilog)}",
    ]
    if out_odb is not None:
        tcl.append(f"write_db {_tcl_quote(out_odb)}")
    tcl += [
        "exit",
    ]

    tmp_dir = out_log.parent / "tmp_tcl"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix="dft_preplaced_",
        suffix=".tcl",
        delete=False,
        dir=str(tmp_dir),
    ) as tf:
        tcl_path = Path(tf.name)
        tf.write("\n".join(tcl) + "\n")

    try:
        out_log.parent.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        with out_log.open("w", encoding="utf-8") as log_f:
            proc = subprocess.Popen(
                [str(openroad_exe), "-exit", str(tcl_path)],
                cwd=os.getcwd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                log_f.write(line)
                if echo_openroad:
                    print(line, end="")
            proc.wait()
        runtime_s = time.perf_counter() - start
        if proc.returncode != 0:
            raise RuntimeError(
                f"OpenROAD failed (exit {proc.returncode}). See log: {out_log}"
            )
        return runtime_s
    finally:
        try:
            tcl_path.unlink()
        except FileNotFoundError:
            pass


def compute_metrics(
    *,
    tag: str,
    requested_chain_count: int,
    max_imbalance: float,
    openroad_runtime_s: Optional[float],
    verilog_path: Path,
    def_path: Path,
    plot_summary_path: Optional[Path] = None,
) -> RunMetrics:
    validation = validate_netlist(
        verilog_path,
        scan_in="scan_in_0",
        scan_out="scan_out_0",
        scan_enable="scan_enable_0",
        auto_chains=True,
        scan_in_prefix="scan_in_",
        scan_out_prefix="scan_out_",
        max_chain_count=requested_chain_count,
    )

    chains, _errors = reconstruct_chains_from_verilog(
        verilog_path,
        scan_in="scan_in_0",
        scan_out="scan_out_0",
        auto_chains=True,
        scan_in_prefix="scan_in_",
        scan_out_prefix="scan_out_",
        max_chain_count=requested_chain_count,
    )

    chain_lengths = sorted([len(c.cells) for c in chains if c.cells])
    min_len = chain_lengths[0] if chain_lengths else None
    max_len = chain_lengths[-1] if chain_lengths else None
    median_len = int(statistics.median(chain_lengths)) if chain_lengths else None

    per_chain: List[ChainStepMetrics] = []
    total_internal_um: Optional[float] = None
    total_io_um: Optional[float] = None
    total_um: Optional[float] = None
    max_step_all: Optional[float] = None
    p99_step_all: Optional[float] = None

    plot_summary = _load_plot_summary(plot_summary_path) if plot_summary_path else None
    if plot_summary is not None:
        try:
            total_internal_um = float(plot_summary["total_internal_cost_um"])
            total_io_um = float(plot_summary["total_io_cost_um"])
            total_um = float(plot_summary["total_cost_um"])
            max_step_all = float(plot_summary["max_step_um"])
            p99_step_all = float(plot_summary["p99_step_um"])

            per_chain = []
            for i, c in enumerate(plot_summary.get("chains", [])):
                if not isinstance(c, dict):
                    continue
                per_chain.append(
                    ChainStepMetrics(
                        chain=str(c.get("chain") or c.get("scan_out") or f"chain_{i}"),
                        scan_in=str(c.get("scan_in") or ""),
                        scan_out=str(c.get("scan_out") or ""),
                        cells=int(c.get("scan_cells") or 0),
                        total_step_um=float(c.get("internal_cost_um") or 0.0),
                        max_step_um=float(c.get("max_internal_step_um") or 0.0),
                        p99_step_um=float(c.get("p99_internal_step_um") or 0.0),
                        io_in_um=float(c.get("io_in_um") or 0.0),
                        io_out_um=float(c.get("io_out_um") or 0.0),
                    )
                )
        except (KeyError, TypeError, ValueError):
            # Fall back to DEF-based metrics below.
            per_chain = []
            plot_summary = None

    if plot_summary is None:
        placements_um, pins_um, _diearea = parse_def_placements(def_path)

        all_edges: List[float] = []
        total_internal_um_f = 0.0
        total_io_um_f = 0.0

        for idx, chain in enumerate(chains):
            if not chain.cells:
                continue
            pts = [placements_um[c] for c in chain.cells]
            steps = [
                abs(x1 - x0) + abs(y1 - y0)
                for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:])
            ]
            steps_sorted = sorted(steps)
            total_step = float(sum(steps))
            max_step = float(steps_sorted[-1]) if steps_sorted else 0.0
            p99 = float(_percentile(steps_sorted, 0.99)) if steps_sorted else 0.0
            all_edges.extend(steps)
            total_internal_um_f += total_step

            io_in = None
            io_out = None
            if chain.scan_in in pins_um and pts:
                px, py = pins_um[chain.scan_in]
                x0, y0 = pts[0]
                io_in = abs(px - x0) + abs(py - y0)
                all_edges.append(io_in)
            if chain.scan_out in pins_um and pts:
                px, py = pins_um[chain.scan_out]
                x1, y1 = pts[-1]
                io_out = abs(px - x1) + abs(py - y1)
                all_edges.append(io_out)

            total_io_um_f += float((io_in or 0.0) + (io_out or 0.0))

            per_chain.append(
                ChainStepMetrics(
                    chain=f"chain_{idx}",
                    scan_in=chain.scan_in,
                    scan_out=chain.scan_out,
                    cells=len(chain.cells),
                    total_step_um=total_step,
                    max_step_um=max_step,
                    p99_step_um=p99,
                    io_in_um=io_in,
                    io_out_um=io_out,
                )
            )

        all_edges_sorted = sorted(all_edges)
        max_step_all = float(all_edges_sorted[-1]) if all_edges_sorted else None
        p99_step_all = (
            float(_percentile(all_edges_sorted, 0.99)) if all_edges_sorted else None
        )
        total_internal_um = total_internal_um_f
        total_io_um = total_io_um_f
        total_um = total_internal_um_f + total_io_um_f

    return RunMetrics(
        tag=tag,
        requested_chain_count=requested_chain_count,
        max_imbalance=max_imbalance,
        openroad_runtime_s=openroad_runtime_s,
        scan_cells_found=validation.scan_cells_found,
        chains_found=validation.chains_found,
        min_chain_len=min_len,
        median_chain_len=median_len,
        max_chain_len=max_len,
        total_internal_um=total_internal_um,
        total_io_um=total_io_um,
        total_um=total_um,
        max_step_um=max_step_all,
        p99_step_um=p99_step_all,
        chains=per_chain,
        validation=validation,
    )


def write_md_row(m: RunMetrics) -> str:
    runtime = "" if m.openroad_runtime_s is None else f"{m.openroad_runtime_s:.3f}"
    total_um = "" if m.total_um is None else f"{m.total_um:.3f}"
    internal_um = "" if m.total_internal_um is None else f"{m.total_internal_um:.3f}"
    io_um = "" if m.total_io_um is None else f"{m.total_io_um:.3f}"
    min_len = "" if m.min_chain_len is None else str(m.min_chain_len)
    med_len = "" if m.median_chain_len is None else str(m.median_chain_len)
    max_len = "" if m.max_chain_len is None else str(m.max_chain_len)
    max_step = "" if m.max_step_um is None else f"{m.max_step_um:.3f}"
    p99_step = "" if m.p99_step_um is None else f"{m.p99_step_um:.3f}"
    return (
        f"| {m.tag} | {m.requested_chain_count} | {m.max_imbalance:g} | {runtime} | "
        f"{total_um} | {internal_um} | {io_um} | {m.chains_found} | "
        f"{min_len} | {med_len} | {max_len} | {max_step} | {p99_step} |"
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Run OpenROAD execute_dft_plan from a pre-placed ODB, then validate and "
            "report 'no big jumps' metrics with plots."
        )
    )
    ap.add_argument("--openroad", type=Path, default=Path("tools/OpenROAD/build/bin/openroad"))
    ap.add_argument("--liberty", action="append", type=Path, required=True, help="Repeatable.")
    ap.add_argument("--odb", type=Path, required=True)
    ap.add_argument("--sdc", type=Path)
    ap.add_argument(
        "--out-prefix",
        type=Path,
        default=Path("dft_preplaced"),
        help="Prefix for generated .def/.v/.json and plots (in cwd by default).",
    )
    ap.add_argument(
        "--chain-counts",
        type=str,
        default="1,2,3",
        help="Comma-separated list (exact chain_count runs).",
    )
    ap.add_argument(
        "--max-imbalances",
        type=str,
        default="2",
        help="Comma-separated list (percent).",
    )
    ap.add_argument("--clock-mixing", default="no_mix")
    ap.add_argument("--polarity-mode", default="strict", choices=["mid", "strict"])
    ap.add_argument("--scan-order-metric", default=None)
    ap.add_argument("--scan-order-solver", default="SCANOPT")
    ap.add_argument(
        "--ucla-major-loops",
        type=int,
        default=100,
        help="Iteration budget for UCLA SCANOPT (ignored for ILS/HEURISTIC).",
    )
    ap.add_argument("--scanopt-rounds", type=int, default=500000)
    ap.add_argument("--scanopt-seed", type=int, default=1)
    ap.add_argument(
        "--scanopt-time-limit",
        type=float,
        default=15.0,
        help="Total budget (seconds) across all chains.",
    )
    ap.add_argument(
        "--insert-lockup",
        type=int,
        choices=[0, 1],
        default=0,
        help="Enable lockup insertion when mixing clock domains (0/1).",
    )
    ap.add_argument(
        "--scan-replace",
        action="store_true",
        help="Run OpenROAD scan_replace before execute_dft_plan (for ODBs without scan flops).",
    )
    ap.add_argument("--constraints-file", type=Path)
    ap.add_argument("--io-placer-h", default=None, help="E.g. metal5 (optional).")
    ap.add_argument("--io-placer-v", default=None, help="E.g. metal6 (optional).")
    ap.add_argument(
        "--echo-openroad",
        action="store_true",
        help="Stream OpenROAD output to stdout (also writes .openroad.log).",
    )
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--no-plots", action="store_true")
    ap.add_argument("--out-md", type=Path)
    ap.add_argument("--out-json", type=Path)
    args = ap.parse_args(argv)

    openroad_exe: Path = args.openroad.resolve()
    if not openroad_exe.exists():
        raise FileNotFoundError(openroad_exe)

    liberties = [p.resolve() for p in args.liberty]
    odb = args.odb.resolve()
    sdc = args.sdc.resolve() if args.sdc else None

    chain_counts = _parse_int_list(args.chain_counts)
    max_imbalances = _parse_float_list(args.max_imbalances)

    out_prefix = args.out_prefix
    if not out_prefix.is_absolute():
        out_prefix = (Path.cwd() / out_prefix).resolve()

    md_lines: List[str] = [
        "| tag | chain_count | max_imbalance | openroad_s | total_um | internal_um | io_um | chains | min_len | median | max_len | max_step_um | p99_step_um |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    all_runs: List[RunMetrics] = []
    plot_py = Path(__file__).with_name("scan_chain_plot.py")
    plot_or_py = Path(__file__).with_name("scan_chain_plot_openroad.py")

    for k in chain_counts:
        for imb in max_imbalances:
            tag = f"k{k}_imb{imb:g}"
            metric = args.scan_order_metric or "DEFAULT"
            print(
                f"=== execute_dft_plan: {tag} "
                f"(solver={args.scan_order_solver}, metric={metric}, clock_mixing={args.clock_mixing}, polarity_mode={args.polarity_mode}) ==="
            )
            out_def = out_prefix.with_name(f"{out_prefix.name}_{tag}.def")
            out_v = out_prefix.with_name(f"{out_prefix.name}_{tag}.v")
            out_odb = out_prefix.with_name(f"{out_prefix.name}_{tag}.odb")
            out_log = out_prefix.with_name(f"{out_prefix.name}_{tag}.openroad.log")

            if args.skip_existing and out_def.exists() and out_v.exists():
                print(f"SKIP (outputs exist): {out_def} {out_v}")
                openroad_runtime_s: Optional[float] = None
            else:
                openroad_runtime_s = run_openroad_execute_dft_plan(
                    openroad_exe=openroad_exe,
                    liberties=liberties,
                    odb=odb,
                    sdc=sdc,
                    out_def=out_def,
                    out_verilog=out_v,
                    out_odb=out_odb,
                    chain_count=k,
                    max_imbalance=imb,
                    clock_mixing=args.clock_mixing,
                    polarity_mode=args.polarity_mode,
                    scan_order_metric=args.scan_order_metric,
                    scan_order_solver=args.scan_order_solver,
                    ucla_major_loops=args.ucla_major_loops,
                    scanopt_rounds=args.scanopt_rounds,
                    scanopt_seed=args.scanopt_seed,
                    scanopt_time_limit=args.scanopt_time_limit,
                    insert_lockup=args.insert_lockup,
                    scan_replace=args.scan_replace,
                    constraints_file=args.constraints_file,
                    io_placer_h=args.io_placer_h,
                    io_placer_v=args.io_placer_v,
                    out_log=out_log,
                    echo_openroad=args.echo_openroad,
                )

            out_plot = out_prefix.with_name(f"{out_prefix.name}_{tag}.png")
            out_plot_json = out_prefix.with_name(f"{out_prefix.name}_{tag}.plot.json")
            plot_summary_path: Optional[Path] = None

            if out_odb.exists():
                try:
                    out_plot_json.unlink()
                except FileNotFoundError:
                    pass
                plot_cmd = [
                    str(openroad_exe),
                    "-python",
                    "-exit",
                    str(plot_or_py),
                    "--odb",
                    str(out_odb),
                    "--out",
                    str(out_plot),
                    "--out-json",
                    str(out_plot_json),
                ]
                if args.no_plots:
                    plot_cmd.append("--no-plot")
                if args.constraints_file and args.constraints_file.exists():
                    plot_cmd += ["--constraints-file", str(args.constraints_file)]
                try:
                    subprocess.run(
                        plot_cmd,
                        check=True,
                        text=True,
                    )
                    if out_plot_json.exists():
                        plot_summary_path = out_plot_json
                except subprocess.CalledProcessError as e:
                    print(
                        f"[WARN] scan_chain_plot_openroad failed (exit {e.returncode}); "
                        f"falling back to DEF-based metrics for {tag}."
                    )

            m = compute_metrics(
                tag=tag,
                requested_chain_count=k,
                max_imbalance=imb,
                openroad_runtime_s=openroad_runtime_s,
                verilog_path=out_v,
                def_path=out_def,
                plot_summary_path=plot_summary_path,
            )
            all_runs.append(m)
            md_lines.append(write_md_row(m))

            if not args.no_plots:
                if not out_odb.exists():
                    subprocess.run(
                        [
                            os.environ.get("PYTHON_EXE", "python3"),
                            str(plot_py),
                            "--auto-chains",
                            "--combined",
                            "--max-chain-count",
                            str(k),
                            "--verilog",
                            str(out_v),
                            "--def",
                            str(out_def),
                            "--out",
                            str(out_plot),
                        ],
                        check=True,
                        text=True,
                    )

    md = "\n".join(md_lines) + "\n"
    if args.out_md:
        args.out_md.write_text(md)
        print(f"WROTE: {args.out_md}")
    else:
        print(md, end="")

    if args.out_json:
        args.out_json.write_text(
            json.dumps([asdict(r) for r in all_runs], indent=2) + "\n"
        )
        print(f"WROTE: {args.out_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
