#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="ignore"))


def _find_first_png(case_dir: Path) -> Optional[Path]:
    pngs = sorted(case_dir.glob("*.png"))
    return pngs[0] if pngs else None


def _fmt_num(x: Optional[float]) -> str:
    if x is None:
        return ""
    return f"{x:.3f}"


def _fmt_runtime(x: Optional[float]) -> str:
    if x is None:
        return ""
    # Keep short while still readable.
    if x >= 1000.0:
        return f"{x:.0f}"
    return f"{x:.3f}"


@dataclass(frozen=True)
class Row:
    test_id: str
    purpose: str
    case_id: str
    bullet: int


def _case_dir(root: Path, bullet: int, case_id: str) -> Path:
    return root / f"bullet-point-{bullet}" / case_id


def _load_openroad_metrics(case_dir: Path) -> Tuple[Optional[float], Optional[float]]:
    metrics_path = case_dir / "metrics.json"
    if not metrics_path.exists():
        return None, None
    data = _read_json(metrics_path)
    if isinstance(data, list) and data:
        m = data[0]
        if isinstance(m, dict):
            return (
                float(m["total_um"]) if m.get("total_um") is not None else None,
                float(m["openroad_runtime_s"])
                if m.get("openroad_runtime_s") is not None
                else None,
            )
    return None, None


def _load_ortools_metrics(case_dir: Path) -> Tuple[Optional[float], Optional[float]]:
    metrics_path = case_dir / "metrics.json"
    if not metrics_path.exists():
        return None, None
    data = _read_json(metrics_path)
    if isinstance(data, dict) and "total_cost_um" in data:
        return (
            float(data["total_cost_um"]) if data.get("total_cost_um") is not None else None,
            float(data["wall_s"]) if data.get("wall_s") is not None else None,
        )
    return None, None


def _load_status_wall(case_dir: Path) -> Optional[float]:
    status_path = case_dir / "status.json"
    if not status_path.exists():
        return None
    data = _read_json(status_path)
    if isinstance(data, dict) and data.get("wall_s") is not None:
        return float(data["wall_s"])
    return None


def _format_link(root: Path, path: Path, label: str) -> str:
    rel = path.resolve().relative_to(root.resolve())
    return f"[{label}]({rel.as_posix()})"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fill the AMUR TABLE for bullet-point-3..7 runs (writes markdown)."
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("amur_table_filled.md"),
        help="Markdown output path (repo-relative by default).",
    )
    args = ap.parse_args(argv)

    root = _repo_root()
    out_path = args.out
    if not out_path.is_absolute():
        out_path = (root / out_path).resolve()

    # Google-doc table rows, plus an explicit 5c_mid/5c_strict appendix.
    rows: List[Row] = [
        Row("3a", "Optimizers", "3a", 3),
        Row("3b", "Optimizers", "3b", 3),
        Row("3c", "Optimizers", "3c", 3),
        Row("3d", "Optimizers", "3d", 3),
        Row("4a", "Ports/chains", "4a", 4),
        Row("4b", "Ports/chains", "4b", 4),
        Row("4c", "Ports/chains", "4c", 4),
        Row("4d", "Ports/chains", "4d", 4),
        # 5c in the doc is the "should error?" case; by default map to strict.
        Row("5c", "Polarity", "5c_strict", 5),
        Row("5d", "Polarity", "5d", 5),
        Row("5e", "Polarity", "5e", 5),
        Row("6c", "Clocks", "6c", 6),
        Row("6d", "Clocks", "6d", 6),
        Row("6e", "Clocks", "6e", 6),
        Row("6f", "Clocks", "6f", 6),
        Row("6g", "Clocks", "6g", 6),
        Row("6h", "Clocks", "6h", 6),
        Row("7c", "Grouping", "7c", 7),
        Row("7d", "Grouping", "7d", 7),
        Row("7e", "Grouping", "7e", 7),
    ]

    lines: List[str] = []
    lines.append("| Test ID | Purpose | Total chain length (um) | Runtime (sec) | Link to visualization |")
    lines.append("| ------: | ------------ | ----------------------- | ------------- | --------------------- |")

    for row in rows:
        case_dir = _case_dir(root, row.bullet, row.case_id)
        total_um: Optional[float] = None
        runtime_s: Optional[float] = None
        link: str = ""

        if row.case_id in {"3c", "3d"}:
            total_um, runtime_s = _load_ortools_metrics(case_dir)
            png = _find_first_png(case_dir)
            if png is not None:
                link = _format_link(root, png, "plot")
        else:
            total_um, runtime_s = _load_openroad_metrics(case_dir)
            if runtime_s is None:
                runtime_s = _load_status_wall(case_dir)
            png = _find_first_png(case_dir)
            if png is not None:
                link = _format_link(root, png, "plot")
            else:
                stdout_log = case_dir / "stdout.log"
                if stdout_log.exists():
                    link = _format_link(root, stdout_log, "log")

        lines.append(
            f"| {row.test_id} | {row.purpose} | {_fmt_num(total_um)} | {_fmt_runtime(runtime_s)} | {link} |"
        )

    # Appendix: explicitly show the polarity-mode delta for 5c.
    lines.append("")
    lines.append("## Polarity mode delta (5c)")
    lines.append(
        "This section is not part of the original AMUR TABLE, but shows how `mid` vs `strict` affects the `5c` expectation."
    )
    lines.append("")
    lines.append("| Case | Polarity mode | Result | Total chain length (um) | Runtime (sec) | Link |")
    lines.append("| ---- | ------------- | ------ | ----------------------- | ------------- | ---- |")

    for case_id, mode in [("5c_mid", "mid"), ("5c_strict", "strict")]:
        case_dir = _case_dir(root, 5, case_id)
        total_um, runtime_s = _load_openroad_metrics(case_dir)
        if runtime_s is None:
            runtime_s = _load_status_wall(case_dir)
        png = _find_first_png(case_dir)
        link = ""
        if png is not None:
            link = _format_link(root, png, "plot")
        else:
            stdout_log = case_dir / "stdout.log"
            if stdout_log.exists():
                link = _format_link(root, stdout_log, "log")
        result = "OK" if (case_dir / "metrics.json").exists() else "ERROR"
        lines.append(
            f"| {case_id} | {mode} | {result} | {_fmt_num(total_um)} | {_fmt_runtime(runtime_s)} | {link} |"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"WROTE: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

