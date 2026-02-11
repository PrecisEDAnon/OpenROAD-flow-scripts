#!/usr/bin/env python3
"""
Scan ordering via OR-Tools (ATSP path).

This is a drop-in external solver for ORFS' `DFT_SCAN_SOLVER_BIN` interface.
It reads the same TSV format as `scan_opt_next.py` (including pin-level
in/out coordinates) and writes a whitespace-separated per-chain ordering:

  chain_name inst0 inst1 inst2 ...

Notes:
  - Uses directed arc costs: |out(src)-in(dst)| (Manhattan).
  - If begin/end metadata is present for a chain, it is used as fixed
    start/end nodes in the path.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Node:
    name: str
    in_x: int
    in_y: int
    out_x: int
    out_y: int


def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _parse_tsv(
    path: Path,
) -> Tuple[Dict[str, List[Node]], Dict[str, Optional[Tuple[int, int]]], Dict[str, Optional[Tuple[int, int]]]]:
    chains: Dict[str, List[Node]] = {}
    begins: Dict[str, Optional[Tuple[int, int]]] = {}
    ends: Dict[str, Optional[Tuple[int, int]]] = {}

    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue

            if line.startswith("#"):
                parts = line.split("\t")
                if len(parts) >= 3 and parts[0] == "#" and parts[1] == "chain":
                    chain = parts[2]
                    i = 3
                    while i + 2 < len(parts):
                        key = parts[i]
                        if key == "begin":
                            begins[chain] = (int(parts[i + 1]), int(parts[i + 2]))
                            i += 3
                            continue
                        if key == "end":
                            ends[chain] = (int(parts[i + 1]), int(parts[i + 2]))
                            i += 3
                            continue
                        i += 1
                continue

            parts = line.split("\t")
            if len(parts) == 4:
                chain, name, xs, ys = parts
                x = int(xs)
                y = int(ys)
                chains.setdefault(chain, []).append(Node(name=name, in_x=x, in_y=y, out_x=x, out_y=y))
                continue
            if len(parts) == 6:
                chain, name, in_xs, in_ys, out_xs, out_ys = parts
                chains.setdefault(chain, []).append(
                    Node(
                        name=name,
                        in_x=int(in_xs),
                        in_y=int(in_ys),
                        out_x=int(out_xs),
                        out_y=int(out_ys),
                    )
                )
                continue
            raise ValueError(f"Bad TSV line (expected 4 or 6 columns): {raw.rstrip()}")

    for chain in chains.keys():
        begins.setdefault(chain, None)
        ends.setdefault(chain, None)
    return chains, begins, ends


def _default_begin_end(nodes: Sequence[Node]) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    start_idx = min(range(len(nodes)), key=lambda i: (nodes[i].in_x + nodes[i].in_y, nodes[i].name))
    begin = (nodes[start_idx].in_x, nodes[start_idx].in_y)
    end_idx = max(
        range(len(nodes)),
        key=lambda i: (_manhattan((nodes[i].out_x, nodes[i].out_y), begin), nodes[i].name),
    )
    end = (nodes[end_idx].out_x, nodes[end_idx].out_y)
    return begin, end


def _solve_chain(
    nodes: Sequence[Node],
    *,
    begin: Optional[Tuple[int, int]],
    end: Optional[Tuple[int, int]],
    time_limit_s: float,
    seed: int,
) -> List[str]:
    if not nodes:
        return []
    if len(nodes) == 1:
        return [nodes[0].name]

    if begin is None or end is None:
        begin2, end2 = _default_begin_end(nodes)
        begin = begin or begin2
        end = end or end2

    # Lazy import so the script is harmless without ortools installed.
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # type: ignore[import-not-found]

    in_x_all = [begin[0], *[n.in_x for n in nodes], end[0]]
    in_y_all = [begin[1], *[n.in_y for n in nodes], end[1]]
    out_x_all = [begin[0], *[n.out_x for n in nodes], end[0]]
    out_y_all = [begin[1], *[n.out_y for n in nodes], end[1]]

    node_count = len(in_x_all)
    mgr = pywrapcp.RoutingIndexManager(node_count, 1, [0], [node_count - 1])
    routing = pywrapcp.RoutingModel(mgr)

    def dist_cb(from_index: int, to_index: int) -> int:
        a = mgr.IndexToNode(from_index)
        b = mgr.IndexToNode(to_index)
        return abs(out_x_all[a] - in_x_all[b]) + abs(out_y_all[a] - in_y_all[b])

    transit_cb = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb)

    search = pywrapcp.DefaultRoutingSearchParameters()
    search.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search.time_limit.FromSeconds(max(1, int(round(time_limit_s))))
    search.random_seed = int(seed) if seed is not None else 0

    sol = routing.SolveWithParameters(search)
    if sol is None:
        raise RuntimeError("OR-Tools: NO_SOLUTION")

    order: List[str] = []
    idx = routing.Start(0)
    while not routing.IsEnd(idx):
        node = mgr.IndexToNode(idx)
        if 1 <= node <= len(nodes):
            order.append(nodes[node - 1].name)
        idx = sol.Value(routing.NextVar(idx))
    return order


def _write_solution(path: Path, solution: Dict[str, List[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for chain in sorted(solution.keys()):
            order = solution[chain]
            f.write(chain)
            for name in order:
                f.write(" ")
                f.write(name)
            f.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--time-limit-s", type=float, default=15.0)
    ap.add_argument("--seed", type=int, default=0)
    # Compatibility with scan_opt_next CLI (ignored by OR-Tools solver).
    ap.add_argument("--max-2opt-iters", type=int, default=0)
    ap.add_argument("--disable-2opt", action="store_true")
    args = ap.parse_args(argv)

    chains, begins, ends = _parse_tsv(args.input)

    solution: Dict[str, List[str]] = {}
    for chain, nodes in chains.items():
        solution[chain] = _solve_chain(
            nodes,
            begin=begins.get(chain),
            end=ends.get(chain),
            time_limit_s=args.time_limit_s,
            seed=args.seed,
        )

    _write_solution(args.output, solution)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

