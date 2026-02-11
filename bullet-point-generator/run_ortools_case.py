#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_units_and_diearea(def_path: Path) -> Tuple[int, Tuple[int, int, int, int]]:
    units = None
    die = None
    for raw in def_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if units is None and line.startswith("UNITS DISTANCE MICRONS"):
            toks = line.replace(";", "").split()
            units = int(toks[-1])
        if die is None and line.startswith("DIEAREA"):
            cleaned = (
                line.replace("DIEAREA", "")
                .replace("(", " ")
                .replace(")", " ")
                .replace(";", " ")
                .replace("\t", " ")
            )
            toks = [t for t in cleaned.split(" ") if t]
            if len(toks) >= 4:
                die = (int(toks[0]), int(toks[1]), int(toks[2]), int(toks[3]))
        if units is not None and die is not None:
            break
    if units is None:
        raise ValueError(f"Missing UNITS in {def_path}")
    if die is None:
        raise ValueError(f"Missing DIEAREA in {def_path}")
    return units, die


def _load_scanffs_tsv(tsv_path: Path) -> Tuple[List[str], List[int], List[int]]:
    names: List[str] = []
    xs: List[int] = []
    ys: List[int] = []
    lines = tsv_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines or not lines[0].startswith("name\t"):
        raise ValueError(f"Unexpected TSV header: {tsv_path}")
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        name, x_s, y_s = fields[0], fields[1], fields[2]
        names.append(name)
        xs.append(int(x_s))
        ys.append(int(y_s))
    if not names:
        raise ValueError(f"No scanffs parsed from {tsv_path}")
    return names, xs, ys


def _load_scanff_pins_tsv(
    tsv_path: Path,
) -> Tuple[List[str], List[int], List[int], List[int], List[int]]:
    names: List[str] = []
    in_xs: List[int] = []
    in_ys: List[int] = []
    out_xs: List[int] = []
    out_ys: List[int] = []
    lines = tsv_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines or not lines[0].startswith("name\t"):
        raise ValueError(f"Unexpected TSV header: {tsv_path}")
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 5:
            continue
        name, in_x_s, in_y_s, out_x_s, out_y_s = fields[0], fields[1], fields[2], fields[3], fields[4]
        names.append(name)
        in_xs.append(int(in_x_s))
        in_ys.append(int(in_y_s))
        out_xs.append(int(out_x_s))
        out_ys.append(int(out_y_s))
    if not names:
        raise ValueError(f"No scanffs parsed from {tsv_path}")
    return names, in_xs, in_ys, out_xs, out_ys


def _parse_corner(
    *,
    name: str,
    die_ll: Sequence[int],
    die_ur: Sequence[int],
) -> Tuple[int, int]:
    x0, y0 = int(die_ll[0]), int(die_ll[1])
    x1, y1 = int(die_ur[0]), int(die_ur[1])
    key = name.strip().upper()
    if key == "LL":
        return x0, y0
    if key == "UR":
        return x1, y1
    if key == "UL":
        return x0, y1
    if "," in key:
        xs, ys = key.split(",", 1)
        return int(xs), int(ys)
    raise ValueError(f"Unsupported corner '{name}'. Use LL/UL/UR or x,y.")


def _plot_edges_png(
    *,
    out_png: Path,
    edges: List[Tuple[Tuple[int, int], Tuple[int, int]]],
    diearea_dbu: Tuple[int, int, int, int],
    highlight_top_k: int = 50,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    x0, y0, x1, y1 = diearea_dbu

    if not edges:
        return

    dists = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for (a, b) in edges]
    idx_sorted = sorted(range(len(dists)), key=lambda i: dists[i], reverse=True)
    hi = set(idx_sorted[: max(0, highlight_top_k)])

    core_edges = [e for i, e in enumerate(edges) if i not in hi]
    hi_edges = [e for i, e in enumerate(edges) if i in hi]

    fig, ax = plt.subplots(figsize=(7, 7))
    if core_edges:
        ax.add_collection(
            LineCollection(
                core_edges,
                colors=(0, 0, 0, 0.15),
                linewidths=0.7,
            )
        )
    if hi_edges:
        ax.add_collection(
            LineCollection(
                hi_edges,
                colors="red",
                linewidths=1.0,
                path_effects=[pe.Stroke(linewidth=1.8, foreground="black"), pe.Normal()],
            )
        )

    # Start/end points.
    ax.scatter([edges[0][0][0]], [edges[0][0][1]], s=18, c="black", marker="o", zorder=10)
    ax.scatter([edges[-1][1][0]], [edges[-1][1][1]], s=18, c="black", marker="s", zorder=10)

    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("x (dbu)")
    ax.set_ylabel("y (dbu)")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=300)
    plt.close(fig)


@dataclass(frozen=True)
class OrtoolsResult:
    time_limit_s: float
    wall_s: float
    nodes_scanff: int
    cost_model: str
    total_cost_dbu: int
    total_cost_um: float
    begin_dbu: Tuple[int, int]
    end_dbu: Tuple[int, int]
    status: str


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run OR-Tools directed TSP path (ATSP) for bullet-point-3 (K=1)."
    )
    ap.add_argument("--case-id", required=True, help="E.g. 3c, 3d.")
    ap.add_argument("--testcase", required=True, help="Typically JPEG-REAL1.")
    ap.add_argument("--testcases-dir", type=Path, default=Path("bullet-point-generator/testcases"))
    ap.add_argument("--begin", default="LL")
    ap.add_argument("--end", default="LL")
    ap.add_argument("--time-limit-s", type=float, required=True)
    ap.add_argument("--log-search", action="store_true")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args(argv)

    root = _repo_root()
    testcases_dir = (root / args.testcases_dir).resolve()
    manifest_path = testcases_dir / args.testcase / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8", errors="ignore"))

    inputs = manifest.get("inputs", {})
    final_def = Path(inputs.get("final_def", manifest.get("final_def", "")))
    if not final_def.exists():
        raise FileNotFoundError(final_def)

    scanffs_tsv = testcases_dir / args.testcase / "scanffs.tsv"
    scanff_pins_tsv = testcases_dir / args.testcase / "scanff_pins.tsv"
    if not scanff_pins_tsv.exists() and not scanffs_tsv.exists():
        raise FileNotFoundError(scanff_pins_tsv)

    die = manifest.get("diearea_dbu")
    if die is None:
        raise ValueError(f"Missing diearea_dbu in {manifest_path}")
    begin = _parse_corner(name=args.begin, die_ll=die["ll"], die_ur=die["ur"])
    end = _parse_corner(name=args.end, die_ll=die["ll"], die_ur=die["ur"])

    cost_model = "pin_atsp"
    if scanff_pins_tsv.exists():
        scan_names, in_xs, in_ys, out_xs, out_ys = _load_scanff_pins_tsv(scanff_pins_tsv)
    else:
        # Fallback: symmetric (point-based) costs only. Regenerate testcases to
        # get pin-level scan_in/scan_out coordinates for ATSP.
        print(
            f"[WARN] Missing {scanff_pins_tsv}; falling back to symmetric point costs from {scanffs_tsv}."
        )
        scan_names, scan_xs, scan_ys = _load_scanffs_tsv(scanffs_tsv)
        in_xs, in_ys = scan_xs, scan_ys
        out_xs, out_ys = scan_xs, scan_ys
        cost_model = "point_stsp_fallback"
    units_per_micron, diearea_dbu = _read_units_and_diearea(final_def)

    # Node mapping:
    #   0           => BEGIN
    #   1..N        => scanffs (in TSV order)
    #   N+1         => END
    in_x_all = [begin[0], *in_xs, end[0]]
    in_y_all = [begin[1], *in_ys, end[1]]
    out_x_all = [begin[0], *out_xs, end[0]]
    out_y_all = [begin[1], *out_ys, end[1]]
    n = len(in_x_all)
    assert n == len(scan_names) + 2
    assert n == len(in_y_all) == len(out_x_all) == len(out_y_all)

    from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # type: ignore[import-not-found]

    manager = pywrapcp.RoutingIndexManager(n, 1, [0], [n - 1])
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(from_index: int, to_index: int) -> int:
        a = manager.IndexToNode(from_index)
        b = manager.IndexToNode(to_index)
        return abs(out_x_all[a] - in_x_all[b]) + abs(out_y_all[a] - in_y_all[b])

    transit_cb = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb)

    search = pywrapcp.DefaultRoutingSearchParameters()
    search.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search.time_limit.FromSeconds(int(math.ceil(args.time_limit_s)))
    search.log_search = bool(args.log_search)

    digits = "".join(ch for ch in args.case_id if ch.isdigit())
    if not digits:
        raise ValueError(f"--case-id '{args.case_id}' must contain leading digits (e.g. 3c).")
    bullet = int(digits[0])
    out_dir = (root / f"bullet-point-{bullet}" / args.case_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    sol = routing.SolveWithParameters(search)
    wall_s = time.perf_counter() - start

    if sol is None:
        res = OrtoolsResult(
            time_limit_s=args.time_limit_s,
            wall_s=wall_s,
            nodes_scanff=len(scan_names),
            cost_model=cost_model,
            total_cost_dbu=0,
            total_cost_um=0.0,
            begin_dbu=begin,
            end_dbu=end,
            status="NO_SOLUTION",
        )
        (out_dir / "metrics.json").write_text(json.dumps(asdict(res), indent=2) + "\n")
        print("NO_SOLUTION")
        return 2

    route_nodes: List[int] = []
    idx = routing.Start(0)
    while not routing.IsEnd(idx):
        route_nodes.append(manager.IndexToNode(idx))
        idx = sol.Value(routing.NextVar(idx))
    route_nodes.append(manager.IndexToNode(idx))

    order_names = [scan_names[node - 1] for node in route_nodes[1:-1]]
    (out_dir / "order.txt").write_text("\n".join(order_names) + "\n")

    cost_dbu = int(sol.ObjectiveValue())
    cost_um = float(cost_dbu) / float(units_per_micron)

    res = OrtoolsResult(
        time_limit_s=args.time_limit_s,
        wall_s=wall_s,
        nodes_scanff=len(scan_names),
        cost_model=cost_model,
        total_cost_dbu=cost_dbu,
        total_cost_um=cost_um,
        begin_dbu=begin,
        end_dbu=end,
        status="OK",
    )
    (out_dir / "metrics.json").write_text(json.dumps(asdict(res), indent=2) + "\n")

    if not args.no_plot:
        plot_edges: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
        idx = routing.Start(0)
        while not routing.IsEnd(idx):
            a = manager.IndexToNode(idx)
            next_idx = sol.Value(routing.NextVar(idx))
            b = manager.IndexToNode(next_idx)
            plot_edges.append(((out_x_all[a], out_y_all[a]), (in_x_all[b], in_y_all[b])))
            idx = next_idx
        _plot_edges_png(out_png=out_dir / "plot.png", edges=plot_edges, diearea_dbu=diearea_dbu)

    print(json.dumps(asdict(res), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
