#!/usr/bin/env python3
"""
ScanOpt-next (reference implementation).

Reads a TSV describing scan cells and their pin locations per chain and writes
an ordered scan sequence per chain.

Input TSV (tab-separated):
  (v2, preferred) chain_name  inst_name  in_x_dbu  in_y_dbu  out_x_dbu  out_y_dbu
  (v1, legacy)    chain_name  inst_name  x_dbu  y_dbu

Optional comment metadata (lines starting with '#'):
  # chain <chain_name> begin <x> <y> end <x> <y>

Output (whitespace-separated), one chain per line:
  chain_name inst0 inst1 inst2 ...

This is intentionally dependency-light (NumPy only) and serves as a bundled
"solver" that can be swapped out via DFT_SCAN_SOLVER_BIN.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np


@dataclass(frozen=True)
class Node:
    name: str
    in_x: int
    in_y: int
    out_x: int
    out_y: int


def manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def rotate_order_to_drop_worst_edge(nodes: List[Node], order: List[int]) -> List[int]:
    """
    If endpoints are unconstrained, we can choose the path "break" freely.

    Treating an order as a cycle, the path corresponds to dropping the closure
    edge (last->first). If some internal edge is worse than that closure, rotate
    the ordering so the worst internal edge becomes the dropped closure edge.
    """
    n = len(order)
    if n < 2:
        return order

    def dist(i: int, j: int) -> int:
        a = nodes[i]
        b = nodes[j]
        return abs(a.out_x - b.in_x) + abs(a.out_y - b.in_y)

    closure = dist(order[-1], order[0])
    max_internal = -1
    candidates: List[int] = []
    for k in range(n - 1):
        c = dist(order[k], order[k + 1])
        if c > max_internal:
            max_internal = c
            candidates = [k]
        elif c == max_internal:
            candidates.append(k)

    if max_internal <= closure or not candidates:
        return order

    # Deterministic tie-break: pick the cut that yields the lexicographically
    # smallest new start cell name.
    best = min(candidates, key=lambda k: nodes[order[k + 1]].name)
    return order[best + 1 :] + order[: best + 1]


def choose_start(nodes: List[Node]) -> int:
    # Lower-leftmost by in_x+in_y, tie-break by name for determinism.
    best_idx = 0
    best_key = (nodes[0].in_x + nodes[0].in_y, nodes[0].name)
    for idx, p in enumerate(nodes[1:], start=1):
        key = (p.in_x + p.in_y, p.name)
        if key < best_key:
            best_key = key
            best_idx = idx
    return best_idx


def nearest_neighbor_order(
    nodes: List[Node],
    *,
    begin: Tuple[int, int] | None,
) -> List[int]:
    n = len(nodes)
    if n <= 1:
        return list(range(n))

    in_coords = np.empty((n, 2), dtype=np.int64)
    out_coords = np.empty((n, 2), dtype=np.int64)
    for i, node in enumerate(nodes):
        in_coords[i, 0] = node.in_x
        in_coords[i, 1] = node.in_y
        out_coords[i, 0] = node.out_x
        out_coords[i, 1] = node.out_y

    if begin is None:
        start = choose_start(nodes)
    else:
        bx, by = begin
        d = np.abs(in_coords[:, 0] - bx) + np.abs(in_coords[:, 1] - by)
        min_dist = int(d.min())
        candidates = np.nonzero(d == min_dist)[0]
        start = int(min(candidates, key=lambda i: nodes[int(i)].name))
    unvisited = np.ones(n, dtype=bool)
    order: List[int] = []
    cur = start
    for _ in range(n):
        order.append(cur)
        unvisited[cur] = False
        if not unvisited.any():
            break
        idxs = np.nonzero(unvisited)[0]
        dx = np.abs(out_coords[cur, 0] - in_coords[idxs, 0])
        dy = np.abs(out_coords[cur, 1] - in_coords[idxs, 1])
        dist = dx + dy
        min_dist = int(dist.min())
        cand = idxs[np.nonzero(dist == min_dist)[0]]
        cur = int(min(cand, key=lambda i: nodes[int(i)].name))
    return order


def path_cost(
    nodes: List[Node],
    order: List[int],
    *,
    begin: Tuple[int, int] | None,
    end: Tuple[int, int] | None,
) -> int:
    if len(order) <= 0:
        return 0
    total = 0
    if begin is not None:
        b = nodes[order[0]]
        total += abs(begin[0] - b.in_x) + abs(begin[1] - b.in_y)
    for a, b in zip(order[:-1], order[1:]):
        na = nodes[a]
        nb = nodes[b]
        total += abs(na.out_x - nb.in_x) + abs(na.out_y - nb.in_y)
    if end is not None:
        a = nodes[order[-1]]
        total += abs(a.out_x - end[0]) + abs(a.out_y - end[1])
    return total


def random_2opt(
    nodes: List[Node],
    order: List[int],
    rng: np.random.Generator,
    iters: int,
    *,
    begin: Tuple[int, int] | None,
    end: Tuple[int, int] | None,
) -> List[int]:
    n = len(order)
    if n < 4 or iters <= 0:
        return order

    best = order[:]
    best_cost = path_cost(nodes, best, begin=begin, end=end)

    for _ in range(iters):
        i = int(rng.integers(0, n - 3))
        j = int(rng.integers(i + 2, n - 1))

        cand = best[:]
        cand[i : j + 1] = reversed(cand[i : j + 1])
        cand_cost = path_cost(nodes, cand, begin=begin, end=end)
        if cand_cost < best_cost:
            best = cand
            best_cost = cand_cost

    return best


def solve_chain(
    chain: str,
    nodes: List[Node],
    rng: np.random.Generator,
    max_2opt_iters: int,
    enable_2opt: bool,
    *,
    begin: Tuple[int, int] | None,
    end: Tuple[int, int] | None,
) -> List[str]:
    if len(nodes) <= 2:
        return [p.name for p in nodes]

    order = nearest_neighbor_order(nodes, begin=begin)

    if enable_2opt and max_2opt_iters > 0:
        # Scale iterations sublinearly to avoid huge runtimes on big chains.
        iters = min(max_2opt_iters, max(0, 20 * len(nodes)))
        order = random_2opt(nodes, order, rng=rng, iters=iters, begin=begin, end=end)

    # Choose the best path break to avoid an obviously bad "jump" edge, but only
    # when both endpoints are unconstrained.
    if begin is None and end is None:
        order = rotate_order_to_drop_worst_edge(nodes, order)

    return [nodes[i].name for i in order]


def read_tsv(
    path: Path,
) -> Tuple[Dict[str, List[Node]], Dict[str, Tuple[int, int] | None], Dict[str, Tuple[int, int] | None]]:
    chains: Dict[str, List[Node]] = {}
    begins: Dict[str, Tuple[int, int] | None] = {}
    ends: Dict[str, Tuple[int, int] | None] = {}
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
                node = Node(name=name, in_x=x, in_y=y, out_x=x, out_y=y)
                chains.setdefault(chain, []).append(node)
                continue
            if len(parts) == 6:
                chain, name, in_xs, in_ys, out_xs, out_ys = parts
                node = Node(
                    name=name,
                    in_x=int(in_xs),
                    in_y=int(in_ys),
                    out_x=int(out_xs),
                    out_y=int(out_ys),
                )
                chains.setdefault(chain, []).append(node)
                continue

            raise ValueError(
                f"Bad TSV line (expected 4 or 6 columns, got {len(parts)}): {raw.rstrip()}"
            )

    for chain in chains.keys():
        begins.setdefault(chain, None)
        ends.setdefault(chain, None)
    return chains, begins, ends


def write_solution(path: Path, solution: Dict[str, List[str]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for chain in sorted(solution.keys()):
            order = solution[chain]
            f.write(chain)
            for name in order:
                f.write(" ")
                f.write(name)
            f.write("\n")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-2opt-iters", type=int, default=20000)
    ap.add_argument("--disable-2opt", action="store_true")
    args = ap.parse_args(argv)

    chains, begins, ends = read_tsv(args.input)
    rng = np.random.default_rng(args.seed)

    solution: Dict[str, List[str]] = {}
    for chain, nodes in chains.items():
        solution[chain] = solve_chain(
            chain,
            nodes,
            rng=rng,
            max_2opt_iters=args.max_2opt_iters,
            enable_2opt=not args.disable_2opt,
            begin=begins.get(chain),
            end=ends.get(chain),
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_solution(args.output, solution)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
