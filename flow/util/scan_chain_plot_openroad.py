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

import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

import networkx as nx

from openroad import Design, Tech  # type: ignore[import-not-found]


SCAN_DATA_IN_PINS = ("SCD", "SI")
SCAN_ENABLE_PINS = ("SCE", "SE")
SCAN_DATA_OUT_PINS = ("Q", "QN", "Q_N")


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


def _node_id(kind: str, name: str) -> str:
    return f"{kind}::{name}"


def _add_inst_node(G: nx.Graph, inst) -> str:
    name = inst.getName()
    nid = _node_id("inst", name)
    if nid not in G:
        x, y = _inst_xy(inst)
        master = inst.getMaster().getName() if inst.getMaster() is not None else ""
        G.add_node(nid, kind="inst", name=name, x=x, y=y, master=master)
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
        return ("inst", inst)

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
    scan_cells: int
    cost_dbu: int
    cost_um: float


@dataclass(frozen=True)
class PlotSummary:
    chains_found: int
    total_cost_dbu: int
    total_cost_um: float
    worst_cost_um: float
    best_cost_um: float
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Plot scan chains from a stitched ODB (OpenROAD -python).")
    ap.add_argument("--odb", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="Output PNG path.")
    ap.add_argument("--out-json", type=Path, default=None, help="Optional JSON with per-chain costs.")
    ap.add_argument("--scan-in-prefix", default="scan_in_")
    ap.add_argument("--scan-out-prefix", default="scan_out_")
    ap.add_argument("--dpi", type=int, default=300)
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

    G = nx.Graph()

    # Edges from each scanff to its predecessor (scan_in_* BTerm or previous scanff).
    missing_pred = 0
    for inst in scanffs:
        in_it = _find_iterm(inst, SCAN_DATA_IN_PINS)
        if in_it is None:
            continue
        src = _trace_net_source(
            in_it.getNet(),
            allow_input_bterms=True,
            visited_insts={inst.getName()},
        )
        if src is None:
            missing_pred += 1
            continue
        dst_node = _add_inst_node(G, inst)
        if src[0] == "inst":
            src_node = _add_inst_node(G, src[1])
        else:
            src_node = _add_bterm_node(G, src[1])
        G.add_edge(src_node, dst_node)

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
        if src is None or src[0] != "inst":
            missing_scan_out += 1
            continue
        src_node = _add_inst_node(G, src[1])
        dst_node = _add_bterm_node(G, bt)
        G.add_edge(src_node, dst_node)

    components = [G.subgraph(c).copy() for c in nx.connected_components(G) if len(c) > 1]
    # Keep only components that contain a scan_out_* terminal (actual chains).
    chains = []
    for comp in components:
        names = [comp.nodes[n].get("name", "") for n in comp.nodes]
        if any(n.startswith(args.scan_out_prefix) for n in names):
            chains.append(comp)

    chains.sort(key=lambda sg: _chain_label(sg.nodes, args.scan_out_prefix))

    print(f"Found {len(chains)} chains. (scanffs={len(scanffs)}, missing_pred={missing_pred}, missing_scan_out={missing_scan_out})")

    # Costs.
    chain_costs: List[ChainCost] = []
    total_cost_dbu = 0
    for sg in chains:
        cost = 0
        scan_cells = sum(1 for n in sg.nodes if sg.nodes[n].get("kind") == "inst")
        for u, v in sg.edges:
            x0, y0 = sg.nodes[u]["x"], sg.nodes[u]["y"]
            x1, y1 = sg.nodes[v]["x"], sg.nodes[v]["y"]
            cost += int(abs(x0 - x1) + abs(y0 - y1))
        total_cost_dbu += cost
        label = _chain_label(sg.nodes, args.scan_out_prefix)
        chain_costs.append(
            ChainCost(
                chain=label,
                scan_cells=scan_cells,
                cost_dbu=cost,
                cost_um=float(cost) / float(dbu_per_micron),
            )
        )

    costs_um = [c.cost_um for c in chain_costs] if chain_costs else [0.0]
    summary = PlotSummary(
        chains_found=len(chains),
        total_cost_dbu=total_cost_dbu,
        total_cost_um=float(total_cost_dbu) / float(dbu_per_micron),
        worst_cost_um=max(costs_um),
        best_cost_um=min(costs_um),
        chains=chain_costs,
    )

    # Plot.
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
        for u, v in sg.edges:
            seg = [(sg.nodes[u]["x"], sg.nodes[u]["y"]), (sg.nodes[v]["x"], sg.nodes[v]["y"])]
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
