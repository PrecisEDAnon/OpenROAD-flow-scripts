#!/usr/bin/env python3
"""
Scan-chain visualization helper (OpenROAD Python / ODB-based).

This is intended to be run via:

  openroad -python -exit flow/util/scan_chain_plot_openroad.py --odb <file.odb> --out <plot.png>

It traces scan chains from the stitched database by walking connectivity from each
scan flop's scan-data input pin back to its source (previous scan flop output or
scan_in_* port), and from scan_out_* ports back to the last scan flop.

The plot style is intentionally similar to the matplotlib/networkx "highlighter"
script used during development: colored core edges with a black outline, and
black dashed edges at chain ends.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import networkx as nx

from openroad import Design, Tech  # type: ignore[import-not-found]


# Pin candidates (match ORFS cost script + common scan naming).
SCAN_DATA_IN_PINS = ("SCD", "SI", "SD", "SCAN_IN", "SCANIN")
SCAN_ENABLE_PINS = ("SCE", "SE")
SCAN_DATA_OUT_PINS = ("SO", "SCO", "SCAN_OUT", "SCANOUT", "Q", "QN", "Q_N")


def _find_iterm(inst, names: Sequence[str]):
    for name in names:
        it = inst.findITerm(name)
        if it is not None:
            return it
    return None


def _is_scanff(inst) -> bool:
    if _find_iterm(inst, SCAN_DATA_IN_PINS) is None:
        return False
    if _find_iterm(inst, SCAN_DATA_OUT_PINS) is None:
        return False
    # Some libraries may not expose scan-enable as a pin; don't hard-require it.
    return True


def _is_passthrough(inst) -> bool:
    master = inst.getMaster()
    if master is None:
        return False
    ms = master.getName().lower()
    return ("buf" in ms) or ("inv" in ms)


def _bbox_center_xy(bbox) -> Tuple[int, int]:
    return int((bbox.xMin() + bbox.xMax()) // 2), int((bbox.yMin() + bbox.yMax()) // 2)


def _inst_xy(inst) -> Tuple[int, int]:
    return _bbox_center_xy(inst.getBBox())


def _bterm_xy(bterm) -> Tuple[int, int]:
    return _bbox_center_xy(bterm.getBBox())


def _iterm_xy(iterm) -> Tuple[int, int]:
    bbox = iterm.getBBox()
    return int(bbox.xMin()), int(bbox.yMin())


def _node_id(kind: str, name: str) -> str:
    return f"{kind}::{name}"


def _add_inst_node(G: nx.Graph, inst) -> str:
    name = inst.getName()
    nid = _node_id("inst", name)
    if nid not in G:
        x, y = _inst_xy(inst)
        master = inst.getMaster().getName() if inst.getMaster() is not None else ""
        clk_net, clk_edge = _clock_domain(inst)
        G.add_node(
            nid,
            kind="inst",
            name=name,
            x=x,
            y=y,
            master=master,
            clk_net=clk_net,
            clk_edge=clk_edge,
        )
    return nid


def _add_bterm_node(G: nx.Graph, bterm) -> str:
    name = bterm.getName()
    nid = _node_id("bterm", name)
    if nid not in G:
        x, y = _bterm_xy(bterm)
        G.add_node(nid, kind="bterm", name=name, x=x, y=y, io=bterm.getIoType())
    return nid


def _pick_driver_iterm(net, *, prefer_scanff: bool) -> Optional[object]:
    # Prefer output-driving instance terminals.
    drivers = [it for it in net.getITerms() if it.getIoType() == "OUTPUT"]
    if not drivers:
        return None
    if prefer_scanff:
        for it in drivers:
            if _is_scanff(it.getInst()):
                return it
    # Fall back to any driver.
    return drivers[0]


def _trace_net_source(
    net,
    *,
    allow_input_bterms: bool,
    visited_insts: Set[str],
) -> Optional[Tuple[str, object]]:
    if net is None:
        return None

    if allow_input_bterms:
        for bt in net.getBTerms():
            if bt.getIoType() == "INPUT":
                return ("bterm", bt)

    drv = _pick_driver_iterm(net, prefer_scanff=True)
    if drv is None:
        return None

    inst = drv.getInst()
    if inst is None:
        return None
    inst_name = inst.getName()
    if inst_name in visited_insts:
        return None
    visited_insts.add(inst_name)

    if _is_scanff(inst):
        return ("iterm", drv)

    if not _is_passthrough(inst):
        return None

    # Trace through a simple buffer/inverter-like cell via its input.
    in_it = inst.findITerm("A") or inst.findITerm("I")
    if in_it is None:
        return None
    return _trace_net_source(
        in_it.getNet(),
        allow_input_bterms=allow_input_bterms,
        visited_insts=visited_insts,
    )


@dataclass(frozen=True)
class ChainCost:
    chain: str
    scan_in: str
    scan_out: str
    scan_cells: int
    cost_dbu: int
    cost_um: float
    internal_cost_dbu: int
    internal_cost_um: float
    io_in_dbu: int
    io_in_um: float
    io_out_dbu: int
    io_out_um: float
    max_internal_step_dbu: int
    max_internal_step_um: float
    p99_internal_step_dbu: int
    p99_internal_step_um: float


@dataclass(frozen=True)
class PlotSummary:
    chains_found: int
    total_cost_dbu: int
    total_cost_um: float
    worst_cost_um: float
    best_cost_um: float
    max_step_dbu: int
    max_step_um: float
    p99_step_dbu: int
    p99_step_um: float
    total_internal_cost_dbu: int
    total_internal_cost_um: float
    total_io_cost_dbu: int
    total_io_cost_um: float
    cost_model: str
    chains: List[ChainCost]


def _chain_label(nodes: Iterable[str], scan_out_prefix: str) -> str:
    # Prefer the scan_out_* name as the chain label.
    outs = []
    for nid in nodes:
        if nid.startswith("bterm::"):
            name = nid.split("::", 1)[1]
            if name.startswith(scan_out_prefix):
                outs.append(name)
    if outs:
        return sorted(outs)[0]
    return "chain"


def _palette() -> List[str]:
    # Matplotlib tab10-ish colors.
    return ["red", "blue", "purple", "orange", "green", "brown", "pink", "gray", "olive", "cyan"]


def _clock_domain(inst) -> Tuple[str, str]:
    clk_n = inst.findITerm("CLK_N")
    if clk_n is not None and clk_n.getNet() is not None:
        return clk_n.getNet().getName(), "falling"
    clk = inst.findITerm("CLK")
    if clk is not None and clk.getNet() is not None:
        return clk.getNet().getName(), "rising"
    return "", "unknown"


def _marker_for_edge(edge: str) -> str:
    if edge == "rising":
        return "^"
    if edge == "falling":
        return "v"
    return "o"


def _try_int(s: str) -> Optional[int]:
    try:
        return int(s)
    except ValueError:
        return None


def _parse_constraint_groups(path: Path) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    if not path.exists():
        return groups
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        toks = line.split()
        if not toks:
            continue
        cmd = toks[0].lower()
        if cmd not in {"group", "path"}:
            continue

        # `group [<name>] [<priority>] <inst/group...>`
        idx = 1
        name = ""
        if idx < len(toks):
            maybe_int = _try_int(toks[idx])
            if maybe_int is None:
                name = toks[idx]
                idx += 1
        if idx < len(toks):
            maybe_int = _try_int(toks[idx])
            if maybe_int is not None and 0 <= maybe_int <= 127:
                idx += 1  # ignore priority

        members = toks[idx:]
        if not name or not members:
            continue
        groups[name] = members
    return groups


def _expand_group(
    name: str,
    groups: Dict[str, List[str]],
    *,
    visiting: Optional[Set[str]] = None,
) -> Set[str]:
    if visiting is None:
        visiting = set()
    if name in visiting:
        return set()
    visiting.add(name)
    out: Set[str] = set()
    for m in groups.get(name, []):
        if m in groups:
            out |= _expand_group(m, groups, visiting=visiting)
        else:
            out.add(m)
    visiting.remove(name)
    return out


def _percentile(sorted_vals: List[int], p: float) -> int:
    if not sorted_vals:
        return 0
    if p <= 0.0:
        return sorted_vals[0]
    if p >= 1.0:
        return sorted_vals[-1]
    idx = int(math.ceil(p * len(sorted_vals))) - 1
    idx = max(0, min(idx, len(sorted_vals) - 1))
    return sorted_vals[idx]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Plot scan chains from a stitched ODB (OpenROAD -python).")
    ap.add_argument("--odb", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="Output PNG path.")
    ap.add_argument("--out-json", type=Path, default=None, help="Optional JSON with per-chain costs.")
    ap.add_argument(
        "--constraints-file",
        type=Path,
        default=None,
        help="Optional scan-order constraints file (used only for highlighting groups).",
    )
    ap.add_argument(
        "--group-prefix",
        default="GROUP",
        help="Highlight groups whose name starts with this prefix.",
    )
    ap.add_argument(
        "--no-node-markers",
        action="store_true",
        help="Disable clock/polarity/group markers overlay.",
    )
    ap.add_argument(
        "--legend",
        action="store_true",
        help="Render a small legend (auto-enabled for <=4 clocks).",
    )
    ap.add_argument("--scan-in-prefix", default="scan_in_")
    ap.add_argument("--scan-out-prefix", default="scan_out_")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--no-plot", action="store_true", help="Skip writing the PNG (still computes JSON).")
    args = ap.parse_args(argv)

    tech = Tech()
    design = Design(tech)
    design.readDb(str(args.odb))
    block = design.getBlock()

    dbu_per_micron = int(block.getDbUnitsPerMicron())
    die = block.getBBox()
    die_x0, die_y0, die_x1, die_y1 = die.xMin(), die.yMin(), die.xMax(), die.yMax()

    scanffs = []
    for inst in block.getInsts():
        if _is_scanff(inst):
            scanffs.append(inst)

    scanff_xy: Dict[str, Tuple[int, int]] = {inst.getName(): _inst_xy(inst) for inst in scanffs}

    G = nx.Graph()

    # Edges from each scanff to its predecessor (scan_in_* BTerm or previous scanff).
    missing_pred = 0
    for inst in scanffs:
        in_it = _find_iterm(inst, SCAN_DATA_IN_PINS)
        if in_it is None:
            continue
        dst_xy = _iterm_xy(in_it)
        src = _trace_net_source(
            in_it.getNet(),
            allow_input_bterms=True,
            visited_insts={inst.getName()},
        )
        if src is None:
            missing_pred += 1
            continue
        dst_node = _add_inst_node(G, inst)
        if src[0] == "iterm":
            src_it = src[1]
            src_inst = src_it.getInst()
            if src_inst is None:
                missing_pred += 1
                continue
            src_node = _add_inst_node(G, src_inst)
            src_xy = _iterm_xy(src_it)
            G.add_edge(
                src_node,
                dst_node,
                src_xy=src_xy,
                dst_xy=dst_xy,
                src_kind="inst",
                dst_kind="inst",
            )
        else:
            src_bt = src[1]
            src_node = _add_bterm_node(G, src_bt)
            src_xy = _bterm_xy(src_bt)
            G.add_edge(
                src_node,
                dst_node,
                src_xy=src_xy,
                dst_xy=dst_xy,
                src_kind="bterm",
                dst_kind="inst",
            )

    # Edges from scan_out_* ports back to the last scanff in that chain.
    scan_out_bterms = [bt for bt in block.getBTerms() if bt.getName().startswith(args.scan_out_prefix)]
    missing_scan_out = 0
    for bt in scan_out_bterms:
        net = bt.getNet()
        src = _trace_net_source(
            net,
            allow_input_bterms=False,
            visited_insts=set(),
        )
        if src is None or src[0] != "iterm":
            missing_scan_out += 1
            continue
        src_it = src[1]
        src_inst = src_it.getInst()
        if src_inst is None:
            missing_scan_out += 1
            continue
        src_node = _add_inst_node(G, src_inst)
        dst_node = _add_bterm_node(G, bt)
        G.add_edge(
            src_node,
            dst_node,
            src_xy=_iterm_xy(src_it),
            dst_xy=_bterm_xy(bt),
            src_kind="inst",
            dst_kind="bterm",
        )

    components = [G.subgraph(c).copy() for c in nx.connected_components(G) if len(c) > 1]
    # Keep only components that contain a scan_out_* terminal (actual chains).
    chains = []
    for comp in components:
        names = [comp.nodes[n].get("name", "") for n in comp.nodes]
        if any(n.startswith(args.scan_out_prefix) for n in names):
            chains.append(comp)

    chains.sort(key=lambda sg: _chain_label(sg.nodes, args.scan_out_prefix))

    print(f"Found {len(chains)} chains. (scanffs={len(scanffs)}, missing_pred={missing_pred}, missing_scan_out={missing_scan_out})")

    # Costs (pin-based, asymmetric): Manhattan(scan_out -> scan_in) + endpoints.
    chain_costs: List[ChainCost] = []
    total_cost_dbu = 0
    total_internal_cost_dbu = 0
    total_io_cost_dbu = 0
    all_edge_lens_dbu: List[int] = []
    for sg in chains:
        cost = 0
        internal_cost = 0
        internal_steps: List[int] = []
        io_in_dbu = 0
        io_out_dbu = 0

        scan_in_ports = [
            sg.nodes[n].get("name", "")
            for n in sg.nodes
            if sg.nodes[n].get("kind") == "bterm"
            and sg.nodes[n].get("name", "").startswith(args.scan_in_prefix)
        ]
        scan_out_ports = [
            sg.nodes[n].get("name", "")
            for n in sg.nodes
            if sg.nodes[n].get("kind") == "bterm"
            and sg.nodes[n].get("name", "").startswith(args.scan_out_prefix)
        ]
        scan_in_name = sorted(scan_in_ports)[0] if scan_in_ports else ""
        scan_out_name = sorted(scan_out_ports)[0] if scan_out_ports else ""

        scan_cells = sum(1 for n in sg.nodes if sg.nodes[n].get("kind") == "inst")
        for u, v, ed in sg.edges(data=True):
            src_xy = ed.get("src_xy")
            dst_xy = ed.get("dst_xy")
            if src_xy is None or dst_xy is None:
                src_xy = (sg.nodes[u]["x"], sg.nodes[u]["y"])
                dst_xy = (sg.nodes[v]["x"], sg.nodes[v]["y"])
            step = int(abs(src_xy[0] - dst_xy[0]) + abs(src_xy[1] - dst_xy[1]))
            cost += step
            all_edge_lens_dbu.append(step)

            src_kind = ed.get("src_kind", "")
            dst_kind = ed.get("dst_kind", "")
            if src_kind == "inst" and dst_kind == "inst":
                internal_cost += step
                internal_steps.append(step)
            else:
                # Endpoint edges: attribute to scan_in_* or scan_out_* when possible.
                bterm_node = None
                if sg.nodes[u].get("kind") == "bterm":
                    bterm_node = u
                elif sg.nodes[v].get("kind") == "bterm":
                    bterm_node = v
                if bterm_node is not None:
                    bname = sg.nodes[bterm_node].get("name", "")
                    if bname.startswith(args.scan_in_prefix):
                        io_in_dbu += step
                    elif bname.startswith(args.scan_out_prefix):
                        io_out_dbu += step
        total_cost_dbu += cost
        total_internal_cost_dbu += internal_cost
        total_io_cost_dbu += cost - internal_cost
        label = _chain_label(sg.nodes, args.scan_out_prefix)

        internal_steps_sorted = sorted(internal_steps)
        max_internal_step_dbu = internal_steps_sorted[-1] if internal_steps_sorted else 0
        p99_internal_step_dbu = _percentile(internal_steps_sorted, 0.99) if internal_steps_sorted else 0
        chain_costs.append(
            ChainCost(
                chain=label,
                scan_in=scan_in_name,
                scan_out=scan_out_name,
                scan_cells=scan_cells,
                cost_dbu=cost,
                cost_um=float(cost) / float(dbu_per_micron),
                internal_cost_dbu=internal_cost,
                internal_cost_um=float(internal_cost) / float(dbu_per_micron),
                io_in_dbu=io_in_dbu,
                io_in_um=float(io_in_dbu) / float(dbu_per_micron),
                io_out_dbu=io_out_dbu,
                io_out_um=float(io_out_dbu) / float(dbu_per_micron),
                max_internal_step_dbu=max_internal_step_dbu,
                max_internal_step_um=float(max_internal_step_dbu) / float(dbu_per_micron),
                p99_internal_step_dbu=p99_internal_step_dbu,
                p99_internal_step_um=float(p99_internal_step_dbu) / float(dbu_per_micron),
            )
        )

    costs_um = [c.cost_um for c in chain_costs] if chain_costs else [0.0]
    all_edge_lens_dbu_sorted = sorted(all_edge_lens_dbu)
    max_step_dbu = all_edge_lens_dbu_sorted[-1] if all_edge_lens_dbu_sorted else 0
    p99_step_dbu = _percentile(all_edge_lens_dbu_sorted, 0.99) if all_edge_lens_dbu_sorted else 0
    summary = PlotSummary(
        chains_found=len(chains),
        total_cost_dbu=total_cost_dbu,
        total_cost_um=float(total_cost_dbu) / float(dbu_per_micron),
        worst_cost_um=max(costs_um),
        best_cost_um=min(costs_um),
        max_step_dbu=max_step_dbu,
        max_step_um=float(max_step_dbu) / float(dbu_per_micron),
        p99_step_dbu=p99_step_dbu,
        p99_step_um=float(p99_step_dbu) / float(dbu_per_micron),
        total_internal_cost_dbu=total_internal_cost_dbu,
        total_internal_cost_um=float(total_internal_cost_dbu) / float(dbu_per_micron),
        total_io_cost_dbu=total_io_cost_dbu,
        total_io_cost_um=float(total_io_cost_dbu) / float(dbu_per_micron),
        cost_model="pin_atsp",
        chains=chain_costs,
    )

    # Plot.
    if args.no_plot:
        if args.out_json is not None:
            args.out_json.parent.mkdir(parents=True, exist_ok=True)
            args.out_json.write_text(json.dumps(asdict(summary), indent=2) + "\n")
        return 0

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D

    dpi = max(72, args.dpi)
    canvas_px = 1600
    fig_w = canvas_px / dpi
    fig_h = canvas_px / dpi
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)

    colors = _palette()
    for idx, sg in enumerate(chains):
        color = colors[idx % len(colors)]

        core_segments = []
        end_segments = []
        for u, v, ed in sg.edges(data=True):
            src_xy = ed.get("src_xy")
            dst_xy = ed.get("dst_xy")
            if src_xy is None or dst_xy is None:
                src_xy = (sg.nodes[u]["x"], sg.nodes[u]["y"])
                dst_xy = (sg.nodes[v]["x"], sg.nodes[v]["y"])
            seg = [src_xy, dst_xy]
            if sg.degree[u] == 1 or sg.degree[v] == 1:
                end_segments.append(seg)
            else:
                core_segments.append(seg)

        if core_segments:
            lc = LineCollection(
                core_segments,
                colors=color,
                linewidths=1.2,
                alpha=0.85,
                zorder=2,
            )
            lc.set_path_effects([pe.Stroke(linewidth=2.6, foreground="black"), pe.Normal()])
            ax.add_collection(lc)

        if end_segments:
            lc_end = LineCollection(
                end_segments,
                colors="black",
                linewidths=1.2,
                linestyles="dashed",
                alpha=0.85,
                zorder=3,
            )
            ax.add_collection(lc_end)

            # Highlight the scanff endpoints (nodes adjacent to scan ports).
            end_nodes = set()
            for u, v in sg.edges:
                if sg.degree[u] == 1 or sg.degree[v] == 1:
                    end_nodes.add(u)
                    end_nodes.add(v)
            for n in end_nodes:
                if sg.degree[n] == 1:
                    continue
                ax.scatter(
                    [sg.nodes[n]["x"]],
                    [sg.nodes[n]["y"]],
                    s=10,
                    c=color,
                    zorder=10,
                    path_effects=[
                        pe.Stroke(linewidth=4, foreground="black"),
                        pe.Stroke(linewidth=3, foreground=color),
                    ],
                )

    # Optional overlay markers for clock domains / polarity and constraint groups.
    highlight_groups: Dict[str, Set[str]] = {}
    if args.constraints_file is not None:
        groups = _parse_constraint_groups(args.constraints_file)
        if groups:
            prefix = args.group_prefix.upper()
            for gname in sorted(groups.keys()):
                if gname.upper().startswith(prefix):
                    highlight_groups[gname] = _expand_group(gname, groups)

    if not args.no_node_markers:
        clock_nets = sorted({(_clock_domain(inst)[0] or "?") for inst in scanffs})
        edge_types = sorted({_clock_domain(inst)[1] for inst in scanffs})
        clk_to_color: Dict[str, str] = {}
        for i, clk in enumerate(clock_nets):
            clk_to_color[clk] = colors[i % len(colors)]

        has_clk_or_edge_variation = (len(clock_nets) > 1) or (len(edge_types) > 1)

        if has_clk_or_edge_variation:
            buckets: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
            for inst in scanffs:
                clk, edge = _clock_domain(inst)
                clk = clk or "?"
                key = (clk, edge)
                buckets.setdefault(key, []).append(scanff_xy.get(inst.getName(), _inst_xy(inst)))

            # Clock/polarity markers for all scan flops.
            for (clk, edge), pts in buckets.items():
                if not pts:
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                ax.scatter(
                    xs,
                    ys,
                    s=3,
                    c=clk_to_color.get(clk, "gray"),
                    marker=_marker_for_edge(edge),
                    alpha=0.35,
                    linewidths=0.0,
                    zorder=4,
                )

        # Group overlays: outline selected groups.
        if highlight_groups:
            group_colors = _palette()
            for gi, (gname, members) in enumerate(highlight_groups.items()):
                pts = [scanff_xy[n] for n in members if n in scanff_xy]
                if not pts:
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                ax.scatter(
                    xs,
                    ys,
                    s=18,
                    facecolors="none",
                    edgecolors=group_colors[gi % len(group_colors)],
                    linewidths=0.7,
                    marker="o",
                    zorder=12,
                )

        # Legend (keep small, and only when helpful).
        want_legend = bool(args.legend) or (
            len(clock_nets) <= 4 and (has_clk_or_edge_variation or highlight_groups)
        )
        if want_legend:
            handles: List[object] = []
            if len(clock_nets) > 1:
                for clk in clock_nets[:6]:
                    handles.append(
                        Line2D(
                            [0],
                            [0],
                            marker="o",
                            color="w",
                            label=f"clk={clk}",
                            markerfacecolor=clk_to_color[clk],
                            markersize=6,
                        )
                    )
            if len(edge_types) > 1:
                for edge in edge_types:
                    handles.append(
                        Line2D(
                            [0],
                            [0],
                            marker=_marker_for_edge(edge),
                            color="black",
                            label=f"edge={edge}",
                            linestyle="None",
                            markersize=6,
                        )
                    )
            if highlight_groups:
                group_colors = _palette()
                for gi, gname in enumerate(list(highlight_groups.keys())[:4]):
                    handles.append(
                        Line2D(
                            [0],
                            [0],
                            marker="o",
                            color=group_colors[gi % len(group_colors)],
                            label=gname,
                            markerfacecolor="none",
                            linestyle="None",
                            markersize=7,
                        )
                    )
            if handles:
                ax.legend(
                    handles=handles,
                    loc="upper right",
                    frameon=True,
                    fontsize=6,
                    handlelength=1.0,
                    borderpad=0.4,
                    labelspacing=0.3,
                )

    ax.set_xlim(die_x0, die_x1)
    ax.set_ylim(die_y0, die_y1)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    # Die outline.
    ax.plot(
        [die_x0, die_x1, die_x1, die_x0, die_x0],
        [die_y0, die_y0, die_y1, die_y1, die_y0],
        color="black",
        linewidth=0.8,
        alpha=0.8,
        zorder=1,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=dpi)
    plt.close(fig)

    print(
        f"Chain Costs (µm): worst={summary.worst_cost_um:.3f}, best={summary.best_cost_um:.3f}, total={summary.total_cost_um:.3f}"
    )

    if args.out_json is not None:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(asdict(summary), indent=2) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
