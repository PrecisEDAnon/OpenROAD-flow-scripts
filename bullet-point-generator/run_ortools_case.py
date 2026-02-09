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


def _plot_order_png(
    *,
    out_png: Path,
    order_xy: List[Tuple[int, int]],
    diearea_dbu: Tuple[int, int, int, int],
    highlight_top_k: int = 50,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    x0, y0, x1, y1 = diearea_dbu

    pts = order_xy
    edges = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    dists = [
        abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])
        for i in range(len(pts) - 1)
    ]
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
    ax.scatter([pts[0][0]], [pts[0][1]], s=18, c="black", marker="o", zorder=10)
    ax.scatter([pts[-1][0]], [pts[-1][1]], s=18, c="black", marker="s", zorder=10)

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
    total_cost_dbu: int
    total_cost_um: float
    begin_dbu: Tuple[int, int]
    end_dbu: Tuple[int, int]
    status: str


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run OR-Tools TSP path for bullet-point-3 (K=1).")
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
    if not scanffs_tsv.exists():
        raise FileNotFoundError(scanffs_tsv)

    die = manifest.get("diearea_dbu")
    if die is None:
        raise ValueError(f"Missing diearea_dbu in {manifest_path}")
    begin = _parse_corner(name=args.begin, die_ll=die["ll"], die_ur=die["ur"])
    end = _parse_corner(name=args.end, die_ll=die["ll"], die_ur=die["ur"])

    scan_names, scan_xs, scan_ys = _load_scanffs_tsv(scanffs_tsv)
    units_per_micron, diearea_dbu = _read_units_and_diearea(final_def)

    # Node mapping:
    #   0           => BEGIN
    #   1..N        => scanffs (in TSV order)
    #   N+1         => END
    xs = [begin[0], *scan_xs, end[0]]
    ys = [begin[1], *scan_ys, end[1]]
    n = len(xs)
    assert n == len(scan_names) + 2

    from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # type: ignore[import-not-found]

    manager = pywrapcp.RoutingIndexManager(n, 1, [0], [n - 1])
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(from_index: int, to_index: int) -> int:
        a = manager.IndexToNode(from_index)
        b = manager.IndexToNode(to_index)
        return abs(xs[a] - xs[b]) + abs(ys[a] - ys[b])

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
        total_cost_dbu=cost_dbu,
        total_cost_um=cost_um,
        begin_dbu=begin,
        end_dbu=end,
        status="OK",
    )
    (out_dir / "metrics.json").write_text(json.dumps(asdict(res), indent=2) + "\n")

    if not args.no_plot:
        order_xy = [(xs[node], ys[node]) for node in route_nodes]
        _plot_order_png(out_png=out_dir / "plot.png", order_xy=order_xy, diearea_dbu=diearea_dbu)

    print(json.dumps(asdict(res), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

