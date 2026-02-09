#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import random
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class BaseInputs:
    base_run_dir: Path
    placed_odb: Path
    base_sdc: Path
    final_def: Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tcl_quote(path: Path) -> str:
    return "{" + str(path) + "}"


def _run_openroad(*, openroad: Path, tcl_lines: Sequence[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = log_path.parent / "tmp_tcl"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="bpgen_", suffix=".tcl", delete=False, dir=str(tmp_dir)
    ) as tf:
        tcl_path = Path(tf.name)
        tf.write("\n".join(tcl_lines) + "\n")
    try:
        proc = subprocess.run(
            [str(openroad), "-exit", str(tcl_path)],
            cwd=str(_repo_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        log_path.write_text(proc.stdout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"OpenROAD failed (exit {proc.returncode}). See log: {log_path}"
            )
    finally:
        try:
            tcl_path.unlink()
        except FileNotFoundError:
            pass


def _read_diearea_ll_ur(def_path: Path) -> Tuple[int, int, int, int]:
    for line in def_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.lstrip().startswith("DIEAREA"):
            cleaned = (
                line.replace("(", " ")
                .replace(")", " ")
                .replace(";", " ")
                .replace("\t", " ")
            )
            toks = [t for t in cleaned.split(" ") if t]
            if len(toks) >= 5:
                return int(toks[1]), int(toks[2]), int(toks[3]), int(toks[4])
    raise ValueError(f"Could not parse DIEAREA from {def_path}")


def _write_min_clock_sdc(
    *,
    out_sdc: Path,
    base_sdc: Path,
    extra_clock_ports: Sequence[str],
) -> None:
    period = None
    for line in base_sdc.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip().startswith("create_clock") and "-period" in line:
            toks = line.strip().split()
            for i, tok in enumerate(toks):
                if tok == "-period" and i + 1 < len(toks):
                    period = toks[i + 1]
                    break
            if period is not None:
                break
    if period is None:
        period = "5.5"

    lines = [
        "###############################################################################",
        "# Minimal SDC for DFT-only scan planning",
        "###############################################################################",
        "current_design jpeg_encoder",
        f"create_clock -name clk -period {period} [get_ports {{clk}}]",
    ]
    for port in extra_clock_ports:
        lines.append(f"create_clock -name {port} -period {period} [get_ports {{{port}}}]")
    out_sdc.parent.mkdir(parents=True, exist_ok=True)
    out_sdc.write_text("\n".join(lines) + "\n")


def _write_manifest(path: Path, data: Dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def _chunk(items: List[str], chunk_size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), chunk_size):
        yield items[i : i + chunk_size]


def _write_group_constraints(
    *,
    out_path: Path,
    group1: List[str],
    group2: List[str],
    chunk_size: int = 200,
) -> None:
    lines: List[str] = []
    lines.append("# Auto-generated scan constraints (groups + before).")
    lines.append("# Note: chain begin/end + chain_count are supplied by the runner.")

    g1_chunks = list(_chunk(group1, chunk_size))
    for idx, chunk in enumerate(g1_chunks):
        lines.append("group G1_%d %s" % (idx, " ".join(chunk)))
    lines.append(
        "group GROUP1 %s" % (" ".join([f"G1_{i}" for i in range(len(g1_chunks))]))
    )

    g2_chunks = list(_chunk(group2, chunk_size))
    for idx, chunk in enumerate(g2_chunks):
        lines.append("group G2_%d %s" % (idx, " ".join(chunk)))
    lines.append(
        "group GROUP2 %s" % (" ".join([f"G2_{i}" for i in range(len(g2_chunks))]))
    )

    lines.append("before GROUP1 GROUP2")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")


def _default_base_inputs(base_run_dir: Path) -> BaseInputs:
    return BaseInputs(
        base_run_dir=base_run_dir,
        placed_odb=base_run_dir / "3_5_place_dp.odb",
        base_sdc=base_run_dir / "3_place.sdc",
        final_def=base_run_dir / "6_final.def",
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate JPEG-REAL1 testcases + variants (A..F) for bullet-point-3..7."
    )
    ap.add_argument("--base-run-dir", type=Path, required=True)
    ap.add_argument(
        "--out-dir", type=Path, default=Path("bullet-point-generator/testcases")
    )
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--openroad", type=Path, required=True)
    ap.add_argument("--liberty", type=Path, required=True)
    args = ap.parse_args(argv)

    root = _repo_root()
    base = _default_base_inputs(args.base_run_dir.resolve())

    for p in (base.placed_odb, base.base_sdc, base.final_def, args.openroad, args.liberty):
        if not p.exists():
            raise FileNotFoundError(p)

    out_root = args.out_dir
    if not out_root.is_absolute():
        out_root = (root / out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    die_x0, die_y0, die_x1, die_y1 = _read_diearea_ll_ur(base.final_def)

    # -------------------------------------------------------------------------
    # JPEG-REAL1: scan_replace applied to a real placed ODB.
    # -------------------------------------------------------------------------
    real1_dir = out_root / "JPEG-REAL1"
    real1_dir.mkdir(parents=True, exist_ok=True)
    base_scan_odb = real1_dir / "jpeg_real1_scan.odb"
    scan_list_tsv = real1_dir / "scanffs.tsv"
    sdc_min = real1_dir / "dft.sdc"
    _write_min_clock_sdc(out_sdc=sdc_min, base_sdc=base.base_sdc, extra_clock_ports=[])

    tcl = [
        f"read_liberty {_tcl_quote(args.liberty)}",
        f"read_db {_tcl_quote(base.placed_odb)}",
        f"read_sdc {_tcl_quote(sdc_min)}",
        # Ensure scan ports exist before scan_replace.
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
        "ensure_scan_port scan_enable_0 INPUT",
        "ensure_scan_port scan_in_0 INPUT",
        "ensure_scan_port scan_out_0 OUTPUT",
        "set_dft_config -chain_count 1 -max_imbalance 2 -clock_mixing no_mix "
        "-scan_in_name_pattern scan_in_{} -scan_out_name_pattern scan_out_{} "
        "-scan_enable_name_pattern scan_enable_{}",
        "scan_replace",
        # Emit scanff placements list for downstream selection.
        f"set fp [open {_tcl_quote(scan_list_tsv)} w]",
        "puts $fp \"name\\tx\\ty\\tmaster\"",
        "set block [ord::get_db_block]",
        "foreach inst [$block getInsts] {",
        "  set master [$inst getMaster]",
        "  if {[$master findMTerm SCD] == \"NULL\" && [$master findMTerm SI] == \"NULL\"} { continue }",
        "  lassign [$inst getLocation] x y",
        "  puts $fp \"[$inst getName]\\t$x\\t$y\\t[$master getName]\"",
        "}",
        "close $fp",
        f"write_db {_tcl_quote(base_scan_odb)}",
        "exit",
    ]
    _run_openroad(openroad=args.openroad, tcl_lines=tcl, log_path=real1_dir / "make.log")

    _write_manifest(
        real1_dir / "manifest.json",
        {
            "name": "JPEG-REAL1",
            "base_run_dir": str(base.base_run_dir),
            "inputs": {
                "odb": str(base_scan_odb),
                "sdc": str(sdc_min),
                "final_def": str(base.final_def),
            },
            "diearea_dbu": {"ll": [die_x0, die_y0], "ur": [die_x1, die_y1]},
            "notes": "scan_replace applied to a real placed ODB; placement coords preserved.",
        },
    )

    scanffs: List[Tuple[str, int, int, str]] = []
    for line in scan_list_tsv.read_text(encoding="utf-8").splitlines()[1:]:
        name, xs, ys, master = line.split("\t")
        scanffs.append((name, int(xs), int(ys), master))
    if not scanffs:
        raise RuntimeError(f"No scanffs found; see {real1_dir}/make.log")

    scanffs_sorted = sorted(scanffs, key=lambda t: (t[1], t[0]))
    n = len(scanffs_sorted)
    half = n // 2
    fifth = n // 5

    # -------------------------------------------------------------------------
    # Polarity variants (A/B): recreate half the scanffs as negedge scan flops.
    # -------------------------------------------------------------------------
    def make_polarity_variant(name: str, pick_names: List[str]) -> None:
        case_dir = out_root / name
        case_dir.mkdir(parents=True, exist_ok=True)
        out_odb = case_dir / f"{name.lower()}_scan.odb"
        out_sdc = case_dir / "dft.sdc"
        _write_min_clock_sdc(out_sdc=out_sdc, base_sdc=base.base_sdc, extra_clock_ports=[])

        picked_path = case_dir / "picked_scanffs.txt"
        picked_path.write_text("\n".join(pick_names) + "\n")

        tcl_lines = [
            f"read_liberty {_tcl_quote(args.liberty)}",
            f"read_db {_tcl_quote(base_scan_odb)}",
            f"read_sdc {_tcl_quote(out_sdc)}",
            "set db [ord::get_db]",
            "set block [ord::get_db_block]",
            "set neg_master [$db findMaster sky130_fd_sc_hd__sdfrtn_1]",
            "if { $neg_master == \"NULL\" } { error \"Missing master sky130_fd_sc_hd__sdfrtn_1\" }",
            f"set fp [open {_tcl_quote(picked_path)} r]",
            "set picked [split [read $fp] \"\\n\"]",
            "close $fp",
            "set replaced 0",
            "foreach inst_name $picked {",
            "  if { $inst_name == \"\" } { continue }",
            "  set inst [$block findInst $inst_name]",
            "  if { $inst == \"NULL\" } { continue }",
            "  set old_master [$inst getMaster]",
            "  if {[$old_master findMTerm SCD] == \"NULL\" && [$old_master findMTerm SI] == \"NULL\"} { continue }",
            "  lassign [$inst getLocation] x y",
            "  set orient [$inst getOrient]",
            "  set status [$inst getPlacementStatus]",
            "  array unset nets",
            "  foreach it [$inst getITerms] {",
            "    set pin [[$it getMTerm] getName]",
            "    set net [$it getNet]",
            "    if { $net != \"NULL\" } { set nets($pin) [$net getName] }",
            "  }",
            "  set clk_net_name \"\"",
            "  if {[info exists nets(CLK)]} { set clk_net_name $nets(CLK) }",
            "  odb::dbInst_destroy $inst",
            "  set new_inst [odb::dbInst_create $block $neg_master $inst_name NULL]",
            "  $new_inst setLocation $x $y",
            "  $new_inst setOrient $orient",
            "  $new_inst setPlacementStatus $status",
            "  foreach it2 [$new_inst getITerms] {",
            "    set pin [[$it2 getMTerm] getName]",
            "    if {[info exists nets($pin)]} {",
            "      set net_obj [$block findNet $nets($pin)]",
            "      if {$net_obj != \"NULL\"} { odb::dbITerm_connect $it2 $net_obj }",
            "    }",
            "  }",
            "  if { $clk_net_name != \"\" } {",
            "    set it_clk_n [$new_inst findITerm CLK_N]",
            "    if { $it_clk_n != \"NULL\" } {",
            "      set net_obj [$block findNet $clk_net_name]",
            "      if {$net_obj != \"NULL\"} { odb::dbITerm_connect $it_clk_n $net_obj }",
            "    }",
            "  }",
            "  incr replaced",
            "}",
            "puts \"replaced_negedge=$replaced\"",
            f"write_db {_tcl_quote(out_odb)}",
            "exit",
        ]
        _run_openroad(
            openroad=args.openroad, tcl_lines=tcl_lines, log_path=case_dir / "make.log"
        )
        _write_manifest(
            case_dir / "manifest.json",
            {
                "name": name,
                "base": "JPEG-REAL1",
                "inputs": {"odb": str(out_odb), "sdc": str(out_sdc), "final_def": str(base.final_def)},
                "diearea_dbu": {"ll": [die_x0, die_y0], "ur": [die_x1, die_y1]},
                "negedge_master": "sky130_fd_sc_hd__sdfrtn_1",
                "negedge_count": len(pick_names),
            },
        )

    a_pick = [t[0] for t in scanffs_sorted[:half]]
    make_polarity_variant("JPEG-REAL1A", a_pick)

    rng = random.Random(args.seed)
    b_pick = [t[0] for t in scanffs_sorted]
    rng.shuffle(b_pick)
    b_pick = sorted(b_pick[:half])
    make_polarity_variant("JPEG-REAL1B", b_pick)

    # -------------------------------------------------------------------------
    # Clock variants (C/D): add clk2 port+net, rewire half scanffs to it.
    # -------------------------------------------------------------------------
    def make_clock_variant(name: str, move_to_clk2: List[str]) -> None:
        case_dir = out_root / name
        case_dir.mkdir(parents=True, exist_ok=True)
        out_odb = case_dir / f"{name.lower()}_scan.odb"
        out_sdc = case_dir / "dft.sdc"
        _write_min_clock_sdc(out_sdc=out_sdc, base_sdc=base.base_sdc, extra_clock_ports=["clk2"])

        picked_path = case_dir / "clk2_scanffs.txt"
        picked_path.write_text("\n".join(move_to_clk2) + "\n")

        tcl_lines = [
            f"read_liberty {_tcl_quote(args.liberty)}",
            f"read_db {_tcl_quote(base_scan_odb)}",
            f"read_sdc {_tcl_quote(out_sdc)}",
            "set block [ord::get_db_block]",
            "set clk2_net [$block findNet clk2]",
            "if { $clk2_net == \"NULL\" } { set clk2_net [odb::dbNet_create $block clk2] }",
            "set clk2_bterm [$block findBTerm clk2]",
            "if { $clk2_bterm == \"NULL\" } {",
            "  set clk2_bterm [odb::dbBTerm_create $clk2_net clk2]",
            "  $clk2_bterm setIoType INPUT",
            "}",
            f"set fp [open {_tcl_quote(picked_path)} r]",
            "set picked [split [read $fp] \"\\n\"]",
            "close $fp",
            "set moved 0",
            "foreach inst_name $picked {",
            "  if { $inst_name == \"\" } { continue }",
            "  set inst [$block findInst $inst_name]",
            "  if { $inst == \"NULL\" } { continue }",
            "  set master [$inst getMaster]",
            "  if {[$master findMTerm SCD] == \"NULL\" && [$master findMTerm SI] == \"NULL\"} { continue }",
            "  set it_clk [$inst findITerm CLK]",
            "  if { $it_clk == \"NULL\" } { continue }",
            "  odb::dbITerm_connect $it_clk $clk2_net",
            "  incr moved",
            "}",
            "puts \"moved_to_clk2=$moved\"",
            f"write_db {_tcl_quote(out_odb)}",
            "exit",
        ]
        _run_openroad(
            openroad=args.openroad, tcl_lines=tcl_lines, log_path=case_dir / "make.log"
        )
        _write_manifest(
            case_dir / "manifest.json",
            {
                "name": name,
                "base": "JPEG-REAL1",
                "inputs": {"odb": str(out_odb), "sdc": str(out_sdc), "final_def": str(base.final_def)},
                "diearea_dbu": {"ll": [die_x0, die_y0], "ur": [die_x1, die_y1]},
                "clk2_count": len(move_to_clk2),
            },
        )

    c_move = [t[0] for t in scanffs_sorted[half:]]
    make_clock_variant("JPEG-REAL1C", c_move)

    rng = random.Random(args.seed + 1)
    d_move = [t[0] for t in scanffs_sorted]
    rng.shuffle(d_move)
    d_move = sorted(d_move[:half])
    make_clock_variant("JPEG-REAL1D", d_move)

    # -------------------------------------------------------------------------
    # Group variants (E/F): constraints-only (use base ODB), GROUP1 before GROUP2.
    # -------------------------------------------------------------------------
    def make_group_variant(name: str, group1_names: List[str]) -> None:
        case_dir = out_root / name
        case_dir.mkdir(parents=True, exist_ok=True)
        group1 = sorted(group1_names)
        group1_set = set(group1)
        group2 = sorted([t[0] for t in scanffs_sorted if t[0] not in group1_set])
        constraints = case_dir / "constraints_groups_before.txt"
        _write_group_constraints(out_path=constraints, group1=group1, group2=group2)
        _write_manifest(
            case_dir / "manifest.json",
            {
                "name": name,
                "base": "JPEG-REAL1",
                "inputs": {
                    "odb": str(base_scan_odb),
                    "sdc": str(sdc_min),
                    "final_def": str(base.final_def),
                },
                "diearea_dbu": {"ll": [die_x0, die_y0], "ur": [die_x1, die_y1]},
                "constraints_file": str(constraints),
                "group1_size": len(group1),
                "group2_size": len(group2),
            },
        )

    e_group1 = [t[0] for t in scanffs_sorted[:fifth]]
    make_group_variant("JPEG-REAL1E", e_group1)

    rng = random.Random(args.seed + 2)
    f_group1 = [t[0] for t in scanffs_sorted]
    rng.shuffle(f_group1)
    f_group1 = f_group1[:fifth]
    make_group_variant("JPEG-REAL1F", f_group1)

    print(f"WROTE testcases under: {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
