#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence


@dataclass(frozen=True)
class Plan:
    chains: Dict[str, List[str]]


def _tcl_quote(path: Path) -> str:
    return "{" + str(path) + "}"


def run_report_dft_plan(
    *,
    openroad_exe: Path,
    liberties: Sequence[Path],
    odb: Path,
    sdc: Optional[Path],
    chain_count: int,
    max_imbalance: float,
    clock_mixing: str,
    scan_order_metric: str,
    scan_order_solver: str,
    scan_replace: bool,
    tmp_dir: Path,
) -> str:
    tcl: List[str] = []
    for lib in liberties:
        tcl.append(f"read_liberty {_tcl_quote(lib)}")
    tcl.append(f"read_db {_tcl_quote(odb)}")
    if sdc and sdc.exists():
        tcl.append(f"read_sdc {_tcl_quote(sdc)}")

    tcl.append(
        "set_dft_config "
        + " ".join(
            [
                f"-chain_count {chain_count}",
                f"-max_imbalance {max_imbalance}",
                f"-clock_mixing {clock_mixing}",
                f"-scan_order_metric {scan_order_metric}",
                f"-scan_order_solver {scan_order_solver}",
            ]
        )
    )
    if scan_replace:
        tcl.append("scan_replace")
    tcl += [
        "report_dft_plan -verbose",
        "exit",
    ]

    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix="dft_demo_plan_",
        suffix=".tcl",
        delete=False,
        dir=str(tmp_dir),
    ) as tf:
        tcl_path = Path(tf.name)
        tf.write("\n".join(tcl) + "\n")

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
        return proc.stdout
    finally:
        try:
            tcl_path.unlink()
        except FileNotFoundError:
            pass


def parse_report_dft_plan_verbose(stdout: str) -> Plan:
    chains: Dict[str, List[str]] = {}
    current: Optional[str] = None
    chain_re = re.compile(r"^Scan chain '([^']+)' has (\d+) cells")
    for line in stdout.splitlines():
        m = chain_re.match(line)
        if m:
            current = m.group(1)
            chains[current] = []
            continue
        if current is None:
            continue
        if line.startswith("  "):
            tok = line.strip().split()[0]
            chains[current].append(tok)
    return Plan(chains=chains)


@dataclass(frozen=True)
class ConstraintMeta:
    mode: str
    selected_cells: List[str]
    chain_count: int
    max_imbalance: float


def write_constraints(
    *,
    out_path: Path,
    mode: str,
    selected: List[str],
) -> None:
    lines: List[str] = [
        "# Auto-generated scan constraints for demo",
    ]
    if mode == "path":
        lines.append("path demo_path " + " ".join(selected))
        lines.append("assign chain_0 demo_path")
    elif mode == "exclude":
        lines.append("exclude " + " ".join(selected))
    else:
        raise ValueError(f"unknown mode: {mode}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate a small scan ordering constraints file from a design's DFT plan."
    )
    ap.add_argument("--openroad", type=Path, default=Path("tools/OpenROAD/build/bin/openroad"))
    ap.add_argument("--liberty", action="append", type=Path, required=True, help="Repeatable.")
    ap.add_argument("--odb", type=Path, required=True)
    ap.add_argument("--sdc", type=Path)
    ap.add_argument("--chain-count", type=int, default=1)
    ap.add_argument("--max-imbalance", type=float, default=2.0)
    ap.add_argument("--clock-mixing", default="no_mix")
    ap.add_argument("--scan-order-metric", default="PLACEMENT")
    ap.add_argument("--scan-order-solver", default="HEURISTIC")
    ap.add_argument("--scan-replace", action="store_true")
    ap.add_argument("--mode", choices=("path", "exclude"), required=True)
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--out-meta", type=Path)
    args = ap.parse_args(argv)

    out_path = args.out.resolve()
    stdout = run_report_dft_plan(
        openroad_exe=args.openroad.resolve(),
        liberties=[p.resolve() for p in args.liberty],
        odb=args.odb.resolve(),
        sdc=args.sdc.resolve() if args.sdc else None,
        chain_count=args.chain_count,
        max_imbalance=args.max_imbalance,
        clock_mixing=args.clock_mixing,
        scan_order_metric=args.scan_order_metric,
        scan_order_solver=args.scan_order_solver,
        scan_replace=args.scan_replace,
        tmp_dir=out_path.parent / "tmp_tcl",
    )

    plan = parse_report_dft_plan_verbose(stdout)
    if not plan.chains:
        raise RuntimeError("No scan chains found in report_dft_plan output.")

    chain0 = sorted(plan.chains.keys())[0]
    cells = plan.chains[chain0]
    if len(cells) < args.count:
        raise RuntimeError(
            f"Not enough scan cells in plan (have {len(cells)}, need {args.count})."
        )

    selected = cells[: args.count]
    write_constraints(out_path=out_path, mode=args.mode, selected=selected)
    print(f"WROTE: {out_path}")

    if args.out_meta:
        meta = ConstraintMeta(
            mode=args.mode,
            selected_cells=selected,
            chain_count=args.chain_count,
            max_imbalance=args.max_imbalance,
        )
        args.out_meta.parent.mkdir(parents=True, exist_ok=True)
        args.out_meta.write_text(json.dumps(asdict(meta), indent=2) + "\n")
        print(f"WROTE: {args.out_meta}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
