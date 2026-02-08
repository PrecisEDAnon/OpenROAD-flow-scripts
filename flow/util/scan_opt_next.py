#!/usr/bin/env python3
"""
ScanOpt-next (reference implementation).

Reads a TSV describing scan cells and their (x,y) locations per chain and writes
an ordered scan sequence per chain.

Input TSV (tab-separated):
  chain_name  inst_name  x_dbu  y_dbu

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
class Point:
    name: str
    x: int
    y: int


def manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def rotate_order_to_drop_worst_edge(points: List[Point], order: List[int]) -> List[int]:
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
        a = points[i]
        b = points[j]
        return abs(a.x - b.x) + abs(a.y - b.y)

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
    best = min(candidates, key=lambda k: points[order[k + 1]].name)
    return order[best + 1 :] + order[: best + 1]


def choose_start(points: List[Point]) -> int:
    # Lower-leftmost by x+y, tie-break by name for determinism.
    best_idx = 0
    best_key = (points[0].x + points[0].y, points[0].name)
    for idx, p in enumerate(points[1:], start=1):
        key = (p.x + p.y, p.name)
        if key < best_key:
            best_key = key
            best_idx = idx
    return best_idx


def nearest_neighbor_order(points: List[Point]) -> List[int]:
    n = len(points)
    if n <= 1:
        return list(range(n))

    coords = np.empty((n, 2), dtype=np.int64)
    for i, p in enumerate(points):
        coords[i, 0] = p.x
        coords[i, 1] = p.y

    start = choose_start(points)
    unvisited = np.ones(n, dtype=bool)
    order: List[int] = []
    cur = start
    for _ in range(n):
        order.append(cur)
        unvisited[cur] = False
        if not unvisited.any():
            break
        idxs = np.nonzero(unvisited)[0]
        dx = np.abs(coords[idxs, 0] - coords[cur, 0])
        dy = np.abs(coords[idxs, 1] - coords[cur, 1])
        dist = dx + dy
        cur = int(idxs[int(dist.argmin())])
    return order


def path_cost(points: List[Point], order: List[int]) -> int:
    if len(order) <= 1:
        return 0
    total = 0
    for a, b in zip(order[:-1], order[1:]):
        total += manhattan((points[a].x, points[a].y), (points[b].x, points[b].y))
    return total


def random_2opt(
    points: List[Point],
    order: List[int],
    rng: np.random.Generator,
    iters: int,
) -> List[int]:
    n = len(order)
    if n < 4 or iters <= 0:
        return order

    coords = np.empty((n, 2), dtype=np.int64)
    for i, idx in enumerate(order):
        p = points[idx]
        coords[i, 0] = p.x
        coords[i, 1] = p.y

    def seg_dist(i: int, j: int) -> int:
        return int(abs(coords[i, 0] - coords[j, 0]) + abs(coords[i, 1] - coords[j, 1]))

    best = order[:]
    best_cost = path_cost(points, best)

    for _ in range(iters):
        i = int(rng.integers(0, n - 3))
        j = int(rng.integers(i + 2, n - 1))
        a, b = i, i + 1
        c, d = j, j + 1

        old = seg_dist(a, b) + seg_dist(c, d)
        new = seg_dist(a, c) + seg_dist(b, d)
        if new >= old:
            continue

        # Apply reversal of segment (b..c).
        best[b : c + 1] = reversed(best[b : c + 1])
        coords[b : c + 1] = coords[b : c + 1][::-1]
        best_cost -= old - new

    return best


def solve_chain(
    chain: str,
    points: List[Point],
    rng: np.random.Generator,
    max_2opt_iters: int,
    enable_2opt: bool,
) -> List[str]:
    if len(points) <= 2:
        return [p.name for p in points]

    order = nearest_neighbor_order(points)

    if enable_2opt and max_2opt_iters > 0:
        # Scale iterations sublinearly to avoid huge runtimes on big chains.
        iters = min(max_2opt_iters, max(0, 20 * len(points)))
        order = random_2opt(points, order, rng=rng, iters=iters)

    # Choose the best path break to avoid an obviously bad "jump" edge.
    order = rotate_order_to_drop_worst_edge(points, order)

    return [points[i].name for i in order]


def read_tsv(path: Path) -> Dict[str, List[Point]]:
    chains: Dict[str, List[Point]] = {}
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 4:
                raise ValueError(f"Bad TSV line (expected 4 columns): {raw.rstrip()}")
            chain, name, xs, ys = parts
            p = Point(name=name, x=int(xs), y=int(ys))
            chains.setdefault(chain, []).append(p)
    return chains


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

    chains = read_tsv(args.input)
    rng = np.random.default_rng(args.seed)

    solution: Dict[str, List[str]] = {}
    for chain, points in chains.items():
        solution[chain] = solve_chain(
            chain,
            points,
            rng=rng,
            max_2opt_iters=args.max_2opt_iters,
            enable_2opt=not args.disable_2opt,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_solution(args.output, solution)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
