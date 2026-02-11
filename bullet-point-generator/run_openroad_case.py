#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8", errors="ignore"))


def _write_constraints(
    *,
    out_path: Path,
    chain_count: int,
    begin_xy: Tuple[int, int],
    end_xy: Tuple[int, int],
    extra_lines: Optional[List[str]] = None,
) -> None:
    bx, by = begin_xy
    ex, ey = end_xy
    lines = ["# Auto-generated constraints (chain endpoints + optional extras)"]
    if extra_lines:
        lines.extend(extra_lines)
    for k in range(chain_count):
        lines.append(f"chain chain_{k} begin {bx} {by} end {ex} {ey}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")


def _die_corners(die_ll: Sequence[int], die_ur: Sequence[int]):
    x0, y0 = int(die_ll[0]), int(die_ll[1])
    x1, y1 = int(die_ur[0]), int(die_ur[1])
    return (x0, y0), (x1, y1), (x0, y1)


def _parse_corner(
    *,
    name: str,
    die_ll: Sequence[int],
    die_ur: Sequence[int],
) -> Tuple[int, int]:
    ll, ur, ul = _die_corners(die_ll, die_ur)
    key = name.strip().upper()
    if key == "LL":
        return ll
    if key == "UR":
        return ur
    if key == "UL":
        return ul
    # Accept "x,y"
    if "," in key:
        xs, ys = key.split(",", 1)
        return int(xs), int(ys)
    raise ValueError(f"Unsupported corner '{name}'. Use LL/UL/UR or x,y.")


def _load_constraint_extras_from_testcase(manifest: dict) -> List[str]:
    extras: List[str] = []
    path = manifest.get("constraints_file")
    if not path:
        return extras
    p = Path(path)
    if not p.exists():
        return extras
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Runner owns chain begin/end so it can match K.
        if line.lower().startswith("chain "):
            continue
        extras.append(line)
    return extras


@dataclass(frozen=True)
class CaseResult:
    ok: bool
    wall_s: float
    out_dir: Path


def _stream_run(cmd: Sequence[str], *, out_log: Path) -> int:
    out_log.parent.mkdir(parents=True, exist_ok=True)
    with out_log.open("w", encoding="utf-8") as f:
        proc = subprocess.Popen(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            f.write(line)
            print(line, end="")
        return int(proc.wait())


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run one OpenROAD bullet-point case (writes to bullet-point-N/<case-id>/).")
    ap.add_argument("--case-id", required=True, help="E.g. 3a, 4b, 5c_strict.")
    ap.add_argument("--expect", choices=["OK", "ERROR"], default="OK")

    ap.add_argument("--testcase", required=True, help="E.g. JPEG-REAL1, JPEG-REAL1A, ...")
    ap.add_argument("--testcases-dir", type=Path, default=Path("bullet-point-generator/testcases"))

    ap.add_argument("--openroad", type=Path, default=Path("tools/OpenROAD/build/bin/openroad"))
    ap.add_argument("--liberty", type=Path, default=Path("flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"))

    ap.add_argument("--k", type=int, required=True, help="Chain count (exact).")
    ap.add_argument("--begin", required=True, help="LL/UL/UR or x,y (DBU).")
    ap.add_argument("--end", required=True, help="LL/UL/UR or x,y (DBU).")

    ap.add_argument("--max-imbalance", type=float, default=2.0)
    ap.add_argument("--clock-mixing", default="no_mix")
    ap.add_argument("--polarity-mode", default="strict", choices=["mid", "strict"])

    ap.add_argument("--solver", choices=["HEURISTIC", "SCANOPT"], default="SCANOPT")
    ap.add_argument("--scanopt-time-limit", type=float, default=15.0)
    ap.add_argument("--scanopt-rounds", type=int, default=500000)
    ap.add_argument("--scanopt-seed", type=int, default=1)

    args = ap.parse_args(argv)

    root = _repo_root()
    testcases_dir = (root / args.testcases_dir).resolve()
    manifest_path = testcases_dir / args.testcase / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    manifest = _read_json(manifest_path)

    inputs = manifest.get("inputs", {})
    odb = Path(inputs["odb"])
    sdc = Path(inputs["sdc"])
    final_def = Path(inputs.get("final_def", manifest.get("final_def", "")))
    if not odb.exists():
        raise FileNotFoundError(odb)
    if not sdc.exists():
        raise FileNotFoundError(sdc)
    if not final_def.exists():
        raise FileNotFoundError(final_def)

    die = manifest.get("diearea_dbu")
    if die is None:
        raise ValueError(f"Missing diearea_dbu in {manifest_path}")
    die_ll = die["ll"]
    die_ur = die["ur"]

    begin_xy = _parse_corner(name=args.begin, die_ll=die_ll, die_ur=die_ur)
    end_xy = _parse_corner(name=args.end, die_ll=die_ll, die_ur=die_ur)

    # Output location: bullet-point-<N>/<case-id>/...
    digits = "".join(ch for ch in args.case_id if ch.isdigit())
    if not digits:
        raise ValueError(f"--case-id '{args.case_id}' must contain leading digits (e.g. 4a).")
    bullet = int(digits[0])
    out_dir = (root / f"bullet-point-{bullet}" / args.case_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    constraints_file = out_dir / "constraints.txt"
    extras = _load_constraint_extras_from_testcase(manifest)
    _write_constraints(
        out_path=constraints_file,
        chain_count=args.k,
        begin_xy=begin_xy,
        end_xy=end_xy,
        extra_lines=extras,
    )

    out_prefix = out_dir / "run"
    out_json = out_dir / "metrics.json"

    cmd = [
        "python3",
        str((root / "flow/util/dft_preplaced_regress.py").resolve()),
        "--echo-openroad",
        "--openroad",
        str((root / args.openroad).resolve() if not args.openroad.is_absolute() else args.openroad),
        "--liberty",
        str((root / args.liberty).resolve() if not args.liberty.is_absolute() else args.liberty),
        "--odb",
        str(odb),
        "--sdc",
        str(sdc),
        "--constraints-file",
        str(constraints_file),
        "--out-prefix",
        str(out_prefix),
        "--chain-counts",
        str(args.k),
        "--max-imbalances",
        str(args.max_imbalance),
        "--clock-mixing",
        str(args.clock_mixing),
        "--polarity-mode",
        str(args.polarity_mode),
        "--scan-order-solver",
        str(args.solver),
        "--scanopt-rounds",
        str(args.scanopt_rounds),
        "--scanopt-seed",
        str(args.scanopt_seed),
        "--scanopt-time-limit",
        str(args.scanopt_time_limit),
        "--out-json",
        str(out_json),
    ]

    print(f"=== CASE {args.case_id} ({args.testcase}) ===")
    print(f"- out_dir: {out_dir}")
    print(f"- constraints: {constraints_file}")
    print(f"- cmd: {' '.join(cmd)}")

    start = time.perf_counter()
    rc = _stream_run(cmd, out_log=out_dir / "stdout.log")
    wall_s = time.perf_counter() - start

    ok = (rc == 0)
    expected_ok = (args.expect == "OK")
    if expected_ok and not ok:
        print(f"[FAIL] Expected OK but got ERROR (exit {rc}). wall={wall_s:.1f}s")
        return 2
    if not expected_ok and ok:
        print(f"[FAIL] Expected ERROR but got OK. wall={wall_s:.1f}s")
        return 3

    status_path = out_dir / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "case_id": args.case_id,
                "testcase": args.testcase,
                "expected": args.expect,
                "returncode": rc,
                "wall_s": wall_s,
                "openroad": str(args.openroad),
                "solver": args.solver,
                "scanopt_time_limit": args.scanopt_time_limit,
                "scanopt_rounds": args.scanopt_rounds,
                "scanopt_seed": args.scanopt_seed,
                "clock_mixing": args.clock_mixing,
                "polarity_mode": args.polarity_mode,
                "k": args.k,
                "max_imbalance": args.max_imbalance,
                "begin": args.begin,
                "end": args.end,
            },
            indent=2,
        )
        + "\n"
    )

    print(f"[OK] {args.case_id}: {args.expect} (exit {rc}). wall={wall_s:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
