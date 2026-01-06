#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


SCAN_IN_PINS = ("SI", "SCD", "SCANIN", "SCAN_IN", "SCAN_DATA_IN")
SCAN_ENABLE_PINS = ("SE", "SCE", "SCAN_EN", "SCAN_ENABLE", "SCANENABLE")
SCAN_OUT_PINS = ("SO", "SCO", "SCANOUT", "SCAN_OUT", "SCAN_DATA_OUT")
FALLBACK_OUT_PINS = ("Q", "QN")
PASS_THROUGH_CELL_PREFIXES = ("BUF", "CLKBUF", "INV")


@dataclass(frozen=True)
class ScanCell:
    name: str
    scan_in_pin: str
    scan_in_net: str
    scan_enable_pin: str
    scan_enable_net: str
    port_nets: Dict[str, str]


@dataclass(frozen=True)
class ChainValidation:
    scan_in: str
    scan_out: str
    cells: int
    start_cell: Optional[str]
    end_cell: Optional[str]
    scan_out_source_net: Optional[str]
    broken_links: int
    errors: List[str]


@dataclass(frozen=True)
class ValidationSummary:
    scan_cells_found: int
    chains_found: int
    chain_cells: int
    start_cell: Optional[str]
    end_cell: Optional[str]
    scan_enable_net: Optional[str]
    scan_out_source_net: Optional[str]
    broken_links: int
    orphan_cells: int
    duplicate_cells: int
    chains: List[ChainValidation]
    errors: List[str]


def _tcl_quote(path: Path) -> str:
    return "{" + str(path) + "}"


def _normalize_verilog_ident(token: str) -> str:
    token = token.strip()
    if token.startswith("\\"):
        token = token[1:]
    return token


def _strip_trailing_delims(token: str) -> str:
    return token.rstrip(",;")


def _extract_simple_assignments(lines: Iterable[str]) -> Dict[str, str]:
    # Only handle: assign lhs = rhs;
    assigns: Dict[str, str] = {}
    assign_re = re.compile(r"^\s*assign\s+(\S+)\s*=\s*(\S+)\s*;\s*$")
    for line in lines:
        m = assign_re.match(line)
        if not m:
            continue
        lhs = _normalize_verilog_ident(_strip_trailing_delims(m.group(1)))
        rhs = _normalize_verilog_ident(_strip_trailing_delims(m.group(2)))
        assigns[lhs] = rhs
    return assigns


def _resolve_alias(assigns: Dict[str, str], net: str) -> str:
    cur = net
    seen: Set[str] = set()
    while cur in assigns and cur not in seen:
        seen.add(cur)
        cur = assigns[cur]
    return cur


def _is_pass_through_cell(cell_type: str) -> bool:
    return cell_type.startswith(PASS_THROUGH_CELL_PREFIXES)


def _parse_ports_from_verilog_lines(lines: Sequence[str]) -> Tuple[Set[str], Set[str]]:
    inputs: Set[str] = set()
    outputs: Set[str] = set()

    decl_re = re.compile(
        r"^\s*(input|output)\s+(?:wire\s+)?(?:reg\s+)?(?:signed\s+)?(?:\[[^\]]+\]\s+)?(.+?)\s*;\s*$"
    )

    for line in lines:
        m = decl_re.match(line)
        if not m:
            continue
        direction = m.group(1)
        rest = m.group(2)
        for token in rest.split(","):
            token = token.strip()
            if not token:
                continue
            name = _normalize_verilog_ident(_strip_trailing_delims(token))
            if direction == "input":
                inputs.add(name)
            else:
                outputs.add(name)

    return inputs, outputs

def parse_scan_cells_from_verilog(
    verilog_path: Path,
) -> Tuple[List[ScanCell], Dict[str, str], Dict[str, str]]:
    scan_cells: List[ScanCell] = []

    with verilog_path.open() as f:
        lines = f.readlines()

    assigns = _extract_simple_assignments(lines)
    driven_by: Dict[str, str] = dict(assigns)

    inst_start_re = re.compile(r"^\s*(\S+)\s+(\S+)\s*\(")
    # `.PORT(net)` where `net` is treated as a single Verilog token.
    port_re = re.compile(r"\.(\w+)\(\s*([^\)\s]+)\s*\)")

    in_inst = False
    inst_type: Optional[str] = None
    inst_name: Optional[str] = None
    buf: List[str] = []

    def flush_instance() -> None:
        nonlocal in_inst, inst_type, inst_name, buf
        if not in_inst or inst_name is None:
            in_inst = False
            inst_type = None
            inst_name = None
            buf = []
            return

        text = " ".join(buf)
        port_nets_raw = {m.group(1): m.group(2) for m in port_re.finditer(text)}
        port_nets = {
            pin: _normalize_verilog_ident(_strip_trailing_delims(net))
            for pin, net in port_nets_raw.items()
        }

        scan_in_pin = next((p for p in SCAN_IN_PINS if p in port_nets), None)
        scan_enable_pin = next((p for p in SCAN_ENABLE_PINS if p in port_nets), None)
        if scan_in_pin and scan_enable_pin:
            scan_cells.append(
                ScanCell(
                    name=_normalize_verilog_ident(inst_name),
                    scan_in_pin=scan_in_pin,
                    scan_in_net=port_nets[scan_in_pin],
                    scan_enable_pin=scan_enable_pin,
                    scan_enable_net=port_nets[scan_enable_pin],
                    port_nets=port_nets,
                )
            )
        elif inst_type and _is_pass_through_cell(inst_type):
            # Collapse simple pass-through combinational instances so we can validate
            # scan connectivity even after buffer insertion/resizing.
            in_net = port_nets.get("A") or port_nets.get("I")
            out_net = port_nets.get("Z") or port_nets.get("ZN")
            if in_net and out_net:
                existing = driven_by.get(out_net)
                if existing and existing != in_net:
                    raise RuntimeError(
                        f"Net '{out_net}' appears to have multiple pass-through drivers: "
                        f"'{existing}' and '{in_net}'."
                    )
                driven_by[out_net] = in_net

        in_inst = False
        inst_type = None
        inst_name = None
        buf = []

    for line in lines:
        if not in_inst:
            m = inst_start_re.match(line)
            if not m:
                continue
            cell_type = m.group(1)
            if cell_type in ("module", "assign", "endmodule"):
                continue
            inst_type = cell_type
            inst_name = m.group(2)
            in_inst = True
            buf = [line]
            if ");" in line:
                flush_instance()
            continue

        buf.append(line)
        if ");" in line:
            flush_instance()

    flush_instance()
    return scan_cells, assigns, driven_by


def _cell_output_nets_for_stitching(cell: ScanCell) -> List[str]:
    nets: List[str] = []
    for pin in SCAN_OUT_PINS + FALLBACK_OUT_PINS:
        net = cell.port_nets.get(pin)
        if not net:
            continue
        if net not in nets:
            nets.append(net)
    return nets


def reconstruct_chain(
    scan_cells: Sequence[ScanCell],
    *,
    scan_in_net: str,
    scan_out_source_net: str,
    driven_by: Dict[str, str],
) -> Tuple[List[str], List[str], int]:
    name_to_cell = {c.name: c for c in scan_cells}

    def root_driver(net: str) -> str:
        cur = net
        seen: Set[str] = set()
        while cur in driven_by and cur not in seen:
            seen.add(cur)
            cur = driven_by[cur]
        return cur

    si_root_to_cells: Dict[str, List[str]] = {}
    for c in scan_cells:
        si_root = root_driver(c.scan_in_net)
        si_root_to_cells.setdefault(si_root, []).append(c.name)

    errors: List[str] = []
    broken_links = 0

    start_candidates = si_root_to_cells.get(scan_in_net, [])
    if len(start_candidates) != 1:
        errors.append(
            f"Expected exactly 1 scan cell driven by {scan_in_net} on scan-in; "
            f"found {len(start_candidates)}."
        )
        return [], errors, broken_links

    chain: List[str] = []
    visited: Set[str] = set()

    cur_name = start_candidates[0]
    while True:
        if cur_name in visited:
            errors.append(f"Loop detected at scan cell '{cur_name}'.")
            break
        visited.add(cur_name)
        chain.append(cur_name)

        cur_cell = name_to_cell[cur_name]
        out_candidates = _cell_output_nets_for_stitching(cur_cell)
        next_nets = [n for n in out_candidates if n in si_root_to_cells]

        if len(next_nets) > 1:
            errors.append(
                f"Ambiguous scan stitch: cell '{cur_name}' has multiple outputs feeding scan inputs: "
                f"{', '.join(next_nets[:8])}{'...' if len(next_nets) > 8 else ''}"
            )
            broken_links += 1
            break

        if len(next_nets) == 1:
            next_net = next_nets[0]
            dst_cells = si_root_to_cells.get(next_net, [])
            if len(dst_cells) != 1:
                errors.append(
                    f"Expected net '{next_net}' to feed exactly 1 scan cell SI; found {len(dst_cells)}."
                )
                broken_links += 1
                break
            cur_name = dst_cells[0]
            continue

        # No next: treat as end-of-chain and validate scan-out.
        scan_out_root = root_driver(scan_out_source_net)
        if scan_out_root not in out_candidates:
            errors.append(
                f"End-of-chain mismatch: last cell '{cur_name}' does not drive scan_out "
                f"(expected '{scan_out_root}'; outputs are {', '.join(out_candidates)})."
            )
            broken_links += 1
        break

    return chain, errors, broken_links


def run_openroad_write_verilog(
    *,
    openroad_exe: Path,
    liberties: Sequence[Path],
    odb: Path,
    sdc: Optional[Path],
    out_verilog: Path,
    max_chains: Optional[int],
    max_length: Optional[int],
    clock_mixing: str,
    do_scan_replace: bool,
    do_execute_dft_plan: bool,
    ensure_ports: bool,
    verbose: bool,
) -> str:
    tcl_lines: List[str] = [
        *[f"read_liberty {_tcl_quote(lib)}" for lib in liberties],
        f"read_db {_tcl_quote(odb)}",
    ]
    if sdc and sdc.exists():
        tcl_lines.append(f"read_sdc {_tcl_quote(sdc)}")

    if ensure_ports:
        tcl_lines += [
            "proc dft_ensure_scan_port {port_name io_type} {",
            "  set block [ord::get_db_block]",
            "  set bterm [$block findBTerm $port_name]",
            "  if { $bterm != \"NULL\" } {",
            "    return",
            "  }",
            "  set net [$block findNet $port_name]",
            "  if { $net == \"NULL\" } {",
            "    set net [odb::dbNet_create $block $port_name]",
            "    $net setSigType SCAN",
            "  }",
            "  set bterm [odb::dbBTerm_create $net $port_name]",
            "  $bterm setSigType SCAN",
            "  $bterm setIoType $io_type",
            "}",
            "dft_ensure_scan_port \"scan_enable_0\" INPUT",
            "dft_ensure_scan_port \"scan_in_0\" INPUT",
            "dft_ensure_scan_port \"scan_out_0\" OUTPUT",
        ]

    set_dft_args = [f"-clock_mixing {clock_mixing}"]
    if max_length is not None:
        set_dft_args.append(f"-max_length {max_length}")
    if max_chains is not None:
        set_dft_args.append(f"-max_chains {max_chains}")
    tcl_lines.append(f"set_dft_config {' '.join(set_dft_args)}")
    if do_scan_replace:
        tcl_lines.append("scan_replace")
    if do_execute_dft_plan:
        tcl_lines.append("execute_dft_plan")

    tcl_lines += [
        f"write_verilog {_tcl_quote(out_verilog)}",
        "exit",
    ]

    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix="scan_chain_validate_",
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


def validate_netlist(
    verilog_path: Path,
    *,
    scan_in: str,
    scan_out: str,
    scan_enable: str,
    auto_chains: bool,
    scan_in_prefix: str,
    scan_out_prefix: str,
) -> ValidationSummary:
    scan_cells, assigns, driven_by = parse_scan_cells_from_verilog(verilog_path)
    input_ports, output_ports = _parse_ports_from_verilog_lines(verilog_path.read_text().splitlines())

    scan_enable_net = _normalize_verilog_ident(scan_enable)

    chain_validations: List[ChainValidation] = []
    all_chain_cells: List[str] = []
    errors: List[str] = []
    broken_links_total = 0

    if auto_chains:
        def ordinal(name: str, prefix: str) -> Optional[int]:
            if not name.startswith(prefix):
                return None
            suffix = name[len(prefix) :]
            if not suffix.isdigit():
                return None
            return int(suffix)

        scan_in_ports = {
            ordinal(name, scan_in_prefix): name
            for name in input_ports
            if ordinal(name, scan_in_prefix) is not None
        }
        scan_out_ports = {
            ordinal(name, scan_out_prefix): name
            for name in output_ports
            if ordinal(name, scan_out_prefix) is not None
        }

        if not scan_in_ports:
            errors.append(
                f"No scan-in ports found with prefix '{scan_in_prefix}' in Verilog inputs."
            )
        if not scan_out_ports:
            errors.append(
                f"No scan-out ports found with prefix '{scan_out_prefix}' in Verilog outputs."
            )

        ords_in = set(scan_in_ports.keys())
        ords_out = set(scan_out_ports.keys())
        only_in = sorted(o for o in ords_in - ords_out if o is not None)
        only_out = sorted(o for o in ords_out - ords_in if o is not None)
        if only_in:
            errors.append(
                f"Missing scan-out ports for ordinals: {', '.join(map(str, only_in[:16]))}"
                f"{'...' if len(only_in) > 16 else ''}"
            )
        if only_out:
            errors.append(
                f"Missing scan-in ports for ordinals: {', '.join(map(str, only_out[:16]))}"
                f"{'...' if len(only_out) > 16 else ''}"
            )

        for idx in sorted(o for o in ords_in & ords_out if o is not None):
            scan_in_name = scan_in_ports[idx]
            scan_out_name = scan_out_ports[idx]
            scan_out_source = _resolve_alias(assigns, scan_out_name)
            chain, chain_errors, broken_links = reconstruct_chain(
                scan_cells,
                scan_in_net=_normalize_verilog_ident(scan_in_name),
                scan_out_source_net=_normalize_verilog_ident(scan_out_source),
                driven_by=driven_by,
            )
            broken_links_total += broken_links
            chain_validations.append(
                ChainValidation(
                    scan_in=scan_in_name,
                    scan_out=scan_out_name,
                    cells=len(chain),
                    start_cell=chain[0] if chain else None,
                    end_cell=chain[-1] if chain else None,
                    scan_out_source_net=_normalize_verilog_ident(scan_out_source),
                    broken_links=broken_links,
                    errors=chain_errors,
                )
            )
            all_chain_cells.extend(chain)
            errors.extend(chain_errors)
    else:
        scan_out_source = _resolve_alias(assigns, scan_out)
        chain, chain_errors, broken_links = reconstruct_chain(
            scan_cells,
            scan_in_net=_normalize_verilog_ident(scan_in),
            scan_out_source_net=_normalize_verilog_ident(scan_out_source),
            driven_by=driven_by,
        )
        broken_links_total += broken_links
        chain_validations.append(
            ChainValidation(
                scan_in=_normalize_verilog_ident(scan_in),
                scan_out=_normalize_verilog_ident(scan_out),
                cells=len(chain),
                start_cell=chain[0] if chain else None,
                end_cell=chain[-1] if chain else None,
                scan_out_source_net=_normalize_verilog_ident(scan_out_source),
                broken_links=broken_links,
                errors=chain_errors,
            )
        )
        all_chain_cells.extend(chain)
        errors.extend(chain_errors)

    # Cross-chain checks: duplicates and orphans.
    visited: Set[str] = set()
    duplicate_cells = 0
    for name in all_chain_cells:
        if name in visited:
            duplicate_cells += 1
        visited.add(name)

    orphan_cells = max(0, len(scan_cells) - len(visited))
    if orphan_cells:
        errors.append(
            f"Orphan scan cells: visited {len(visited)}/{len(scan_cells)}; {orphan_cells} not in any chain."
        )
    if duplicate_cells:
        errors.append(
            f"Duplicate scan cells across chains: {duplicate_cells} duplicate occurrence(s)."
        )

    def root_driver(net: str) -> str:
        cur = net
        seen: Set[str] = set()
        while cur in driven_by and cur not in seen:
            seen.add(cur)
            cur = driven_by[cur]
        return cur

    enable_roots = {root_driver(c.scan_enable_net) for c in scan_cells}
    enable_net_value = next(iter(enable_roots)) if len(enable_roots) == 1 else None

    if enable_net_value is None:
        errors.append(
            f"Scan enable is not uniform across scan cells: {sorted(enable_roots)[:8]}"
            f"{'...' if len(enable_roots) > 8 else ''}"
        )
    elif enable_net_value != scan_enable_net:
        errors.append(
            f"Scan enable net mismatch: scan cells use '{enable_net_value}', expected '{scan_enable_net}'."
        )

    total_chain_cells = len(visited)
    chains_found = sum(1 for c in chain_validations if c.cells > 0)

    if auto_chains and chains_found != len(chain_validations):
        errors.append(
            f"Expected to validate {len(chain_validations)} chain(s) from ports; "
            f"reconstructed {chains_found}."
        )

    return ValidationSummary(
        scan_cells_found=len(scan_cells),
        chains_found=chains_found,
        chain_cells=total_chain_cells,
        start_cell=chain_validations[0].start_cell
        if len(chain_validations) == 1
        else None,
        end_cell=chain_validations[0].end_cell if len(chain_validations) == 1 else None,
        scan_enable_net=enable_net_value,
        scan_out_source_net=root_driver(chain_validations[0].scan_out_source_net)
        if len(chain_validations) == 1 and chain_validations[0].scan_out_source_net
        else None,
        broken_links=broken_links_total,
        orphan_cells=orphan_cells,
        duplicate_cells=duplicate_cells,
        chains=chain_validations,
        errors=errors,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate scan-chain stitching correctness from a gate-level Verilog netlist."
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--verilog", type=Path, help="Gate-level Verilog to validate.")
    input_group.add_argument("--odb", type=Path, help="Write Verilog from this ODB first.")

    parser.add_argument("--openroad", type=Path, help="Required with --odb.")
    parser.add_argument(
        "--liberty",
        type=Path,
        action="append",
        help="Liberty file to load (repeatable; required with --odb).",
    )
    parser.add_argument("--sdc", type=Path, default=None, help="Optional; used with --odb.")
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
    parser.add_argument("--scan-replace", action="store_true")
    parser.add_argument("--execute-dft-plan", action="store_true")
    parser.add_argument(
        "--ensure-ports",
        action="store_true",
        help="Create scan ports if missing (idempotent); only used with --odb.",
    )
    parser.add_argument("--scan-in", default="scan_in_0")
    parser.add_argument("--scan-out", default="scan_out_0")
    parser.add_argument("--scan-enable", default="scan_enable_0")
    parser.add_argument(
        "--auto-chains",
        action="store_true",
        help="Auto-detect and validate all scan_in_N/scan_out_N chains from Verilog ports.",
    )
    parser.add_argument("--scan-in-prefix", default="scan_in_")
    parser.add_argument("--scan-out-prefix", default="scan_out_")
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--verbose-openroad", action="store_true")
    args = parser.parse_args()

    verilog_path: Path
    if args.verilog:
        verilog_path = args.verilog.resolve()
        if not verilog_path.exists():
            raise FileNotFoundError(verilog_path)
    else:
        odb = args.odb.resolve()
        if not odb.exists():
            raise FileNotFoundError(odb)
        if not args.openroad or not args.liberty:
            raise ValueError("--openroad and at least one --liberty are required with --odb.")
        openroad_exe = args.openroad.resolve()
        liberties = [p.resolve() for p in args.liberty]
        if not openroad_exe.exists():
            raise FileNotFoundError(openroad_exe)
        for lib in liberties:
            if not lib.exists():
                raise FileNotFoundError(lib)

        with tempfile.TemporaryDirectory(prefix="scan_chain_validate_", dir=os.getcwd()) as td:
            tmp_dir = Path(td)
            verilog_path = tmp_dir / "design.v"
            max_chains: Optional[int] = args.max_chains
            if max_chains is None and args.max_length is None:
                max_chains = 1

            run_openroad_write_verilog(
                openroad_exe=openroad_exe,
                liberties=liberties,
                odb=odb,
                sdc=args.sdc.resolve() if args.sdc else None,
                out_verilog=verilog_path,
                max_chains=max_chains,
                max_length=args.max_length,
                clock_mixing=args.clock_mixing,
                do_scan_replace=args.scan_replace,
                do_execute_dft_plan=args.execute_dft_plan,
                ensure_ports=args.ensure_ports,
                verbose=args.verbose_openroad,
            )

            summary = validate_netlist(
                verilog_path,
                scan_in=args.scan_in,
                scan_out=args.scan_out,
                scan_enable=args.scan_enable,
                auto_chains=args.auto_chains,
                scan_in_prefix=args.scan_in_prefix,
                scan_out_prefix=args.scan_out_prefix,
            )

            print(f"scan_cells_found={summary.scan_cells_found}")
            print(f"chains_found={summary.chains_found}")
            print(f"chain_cells={summary.chain_cells}")
            if summary.chains and len(summary.chains) > 1:
                for c in summary.chains[:50]:
                    print(
                        f"chain scan_in={c.scan_in} scan_out={c.scan_out} "
                        f"cells={c.cells} start_cell={c.start_cell} end_cell={c.end_cell} "
                        f"broken_links={c.broken_links}"
                    )
            else:
                if summary.start_cell:
                    print(f"start_cell={summary.start_cell}")
                if summary.end_cell:
                    print(f"end_cell={summary.end_cell}")
                if summary.scan_out_source_net:
                    print(f"scan_out_source_net={summary.scan_out_source_net}")
            if summary.scan_enable_net:
                print(f"scan_enable_net={summary.scan_enable_net}")
            print(f"broken_links={summary.broken_links}")
            print(f"orphan_cells={summary.orphan_cells}")
            print(f"duplicate_cells={summary.duplicate_cells}")
            for e in summary.errors[:50]:
                print(f"ERROR: {e}")

            if args.out_json:
                payload = asdict(summary)
                args.out_json.parent.mkdir(parents=True, exist_ok=True)
                args.out_json.write_text(
                    __import__("json").dumps(payload, indent=2, sort_keys=True)
                )

            return 0 if not summary.errors else 2

    # --verilog path (no OpenROAD)
    summary = validate_netlist(
        verilog_path,
        scan_in=args.scan_in,
        scan_out=args.scan_out,
        scan_enable=args.scan_enable,
        auto_chains=args.auto_chains,
        scan_in_prefix=args.scan_in_prefix,
        scan_out_prefix=args.scan_out_prefix,
    )

    print(f"scan_cells_found={summary.scan_cells_found}")
    print(f"chains_found={summary.chains_found}")
    print(f"chain_cells={summary.chain_cells}")
    if summary.chains and len(summary.chains) > 1:
        for c in summary.chains[:50]:
            print(
                f"chain scan_in={c.scan_in} scan_out={c.scan_out} "
                f"cells={c.cells} start_cell={c.start_cell} end_cell={c.end_cell} "
                f"broken_links={c.broken_links}"
            )
    else:
        if summary.start_cell:
            print(f"start_cell={summary.start_cell}")
        if summary.end_cell:
            print(f"end_cell={summary.end_cell}")
        if summary.scan_out_source_net:
            print(f"scan_out_source_net={summary.scan_out_source_net}")
    if summary.scan_enable_net:
        print(f"scan_enable_net={summary.scan_enable_net}")
    print(f"broken_links={summary.broken_links}")
    print(f"orphan_cells={summary.orphan_cells}")
    print(f"duplicate_cells={summary.duplicate_cells}")
    for e in summary.errors[:50]:
        print(f"ERROR: {e}")

    if args.out_json:
        payload = asdict(summary)
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(__import__("json").dumps(payload, indent=2, sort_keys=True))

    return 0 if not summary.errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
