#!/usr/bin/env python3
"""
ScanOpt-next (reference implementation, dependency-free).

Reads a TSV describing scan cells and their (x,y) locations per chain and writes
an ordered scan sequence per chain.

Input TSV (tab-separated):
  chain_name  inst_name  x_dbu  y_dbu

Output (whitespace-separated), one chain per line:
  chain_name inst0 inst1 inst2 ...

This is intended as a simple, reviewer-friendly baseline solver that can be
swapped out via `DFT_SCAN_SOLVER_BIN` in `flow/scripts/dft_scan_pre_global_route.tcl`.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Point:
    name: str
    x: int
    y: int


def _manhattan(xs: List[int], ys: List[int], a: int, b: int) -> int:
    return abs(xs[a] - xs[b]) + abs(ys[a] - ys[b])


def _choose_start(names: List[str], xs: List[int], ys: List[int]) -> int:
    best_idx = 0
    best_key = (xs[0] + ys[0], names[0])
    for i in range(1, len(names)):
        key = (xs[i] + ys[i], names[i])
        if key < best_key:
            best_key = key
            best_idx = i
    return best_idx


def _nearest_neighbor_order(names: List[str], xs: List[int], ys: List[int]) -> List[int]:
    n = len(names)
    if n <= 1:
        return list(range(n))

    start = _choose_start(names, xs, ys)
    visited = [False] * n
    visited[start] = True
    order = [start]
    cur = start

    for _ in range(n - 1):
        cx = xs[cur]
        cy = ys[cur]
        best = -1
        best_dist = 0
        best_name = ""
        for i in range(n):
            if visited[i]:
                continue
            dist = abs(xs[i] - cx) + abs(ys[i] - cy)
            if best < 0 or dist < best_dist or (dist == best_dist and names[i] < best_name):
                best = i
                best_dist = dist
                best_name = names[i]
        if best < 0:
            break
        visited[best] = True
        order.append(best)
        cur = best

    return order


def _random_2opt(
    order: List[int],
    xs: List[int],
    ys: List[int],
    rng: random.Random,
    iters: int,
) -> List[int]:
    n = len(order)
    if n < 4 or iters <= 0:
        return order

    for _ in range(iters):
        i = rng.randrange(0, n - 3)
        j = rng.randrange(i + 2, n - 1)

        a = order[i]
        b = order[i + 1]
        c = order[j]
        d = order[j + 1]

        old = _manhattan(xs, ys, a, b) + _manhattan(xs, ys, c, d)
        new = _manhattan(xs, ys, a, c) + _manhattan(xs, ys, b, d)
        if new >= old:
            continue

        # Reverse the segment (i+1 .. j).
        order[i + 1 : j + 1] = reversed(order[i + 1 : j + 1])

    return order


def solve_chain(points: List[Point], *, seed: int, max_2opt_iters: int, disable_2opt: bool) -> List[str]:
    if not points:
        return []
    if len(points) <= 2:
        return [p.name for p in points]

    names = [p.name for p in points]
    xs = [p.x for p in points]
    ys = [p.y for p in points]

    order = _nearest_neighbor_order(names, xs, ys)

    if not disable_2opt and max_2opt_iters > 0 and len(order) >= 4:
        iters = min(max_2opt_iters, max(0, 20 * len(order)))
        order = _random_2opt(order, xs, ys, rng=random.Random(seed), iters=iters)

    return [names[i] for i in order]


def read_tsv(path: Path) -> Dict[str, List[Point]]:
    chains: Dict[str, List[Point]] = {}
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 4:
                raise ValueError(f"Bad TSV line (expected 4 tab-separated columns): {raw.rstrip()}")
            chain, name, xs, ys = parts
            chains.setdefault(chain, []).append(Point(name=name, x=int(xs), y=int(ys)))
    return chains


def write_solution(path: Path, solution: Dict[str, List[str]]) -> None:
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
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-2opt-iters", type=int, default=20000)
    ap.add_argument("--disable-2opt", action="store_true")
    args = ap.parse_args(argv)

    chains = read_tsv(args.input)

    solution: Dict[str, List[str]] = {}
    for chain, points in chains.items():
        solution[chain] = solve_chain(
            points,
            seed=args.seed,
            max_2opt_iters=args.max_2opt_iters,
            disable_2opt=args.disable_2opt,
        )

    write_solution(args.output, solution)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
