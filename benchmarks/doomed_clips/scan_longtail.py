#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path


ITER_START_RE = re.compile(
    r"^\[INFO DRT-0195\] Start (\d+)(?:st|nd|rd|th) (?:stubborn tiles|optimization) iteration\.\s*$",
    re.MULTILINE,
)
VIOL_RE = re.compile(r"^\[INFO DRT-0199\]\s+Number of violations = (\d+)\.\s*$", re.MULTILINE)
TOOK_RE = re.compile(r"^Took (\d+) seconds: detailed_route\b", re.MULTILINE)
TIME_RE = re.compile(
    r"^\[INFO DRT-0267\] cpu time = (\d\d:\d\d:\d\d), elapsed time = (\d\d:\d\d:\d\d),",
    re.MULTILINE,
)


def _parse_hms(hms: str) -> int:
  parts = hms.split(":")
  if len(parts) != 3:
    raise ValueError(f"bad hms: {hms}")
  h, m, s = (int(p) for p in parts)
  return h * 3600 + m * 60 + s


def _find_last_index(parts: tuple[str, ...], name: str) -> int | None:
  idx = None
  for i, part in enumerate(parts):
    if part == name:
      idx = i
  return idx


def _try_parse_log_identity(log_path: Path) -> tuple[Path, str, str, str] | None:
  parts = log_path.parts
  logs_idx = _find_last_index(parts, "logs")
  if logs_idx is None:
    return None
  if logs_idx + 4 >= len(parts):
    return None
  platform, design, variant = parts[logs_idx + 1 : logs_idx + 4]
  work_home = Path(*parts[:logs_idx])
  return work_home, platform, design, variant


def _parse_drt_seconds(log_text: str) -> int | None:
  times = [int(m.group(1)) for m in TOOK_RE.finditer(log_text)]
  if times:
    return sum(times)
  return None


def _parse_iter_count(log_text: str) -> tuple[int, int | None]:
  iters = [int(m.group(1)) for m in ITER_START_RE.finditer(log_text)]
  if not iters:
    return 0, None
  return len(iters), max(iters)


def _parse_final_viols_from_log(log_text: str) -> int | None:
  viols = [int(m.group(1)) for m in VIOL_RE.finditer(log_text)]
  return viols[-1] if viols else None


def _parse_avg_effective_cores(log_text: str) -> tuple[float | None, float | None, float | None]:
  pairs: list[tuple[int, int]] = []
  for m in TIME_RE.finditer(log_text):
    try:
      cpu_s = _parse_hms(m.group(1))
      elapsed_s = _parse_hms(m.group(2))
    except ValueError:
      continue
    if elapsed_s <= 0:
      continue
    pairs.append((cpu_s, elapsed_s))
  if not pairs:
    return None, None, None
  cores = [cpu / elapsed for cpu, elapsed in pairs]
  return sum(cores) / len(cores), min(cores), max(cores)


def _parse_final_drvs_from_metrics(metrics_path: Path) -> int | None:
  if not metrics_path.exists():
    return None
  try:
    data = json.loads(metrics_path.read_text())
  except json.JSONDecodeError:
    return None
  v = data.get("detailedroute__route__drc_errors")
  if v is None:
    return None
  try:
    return int(v)
  except (TypeError, ValueError):
    return None


@dataclass(frozen=True)
class Entry:
  work_home: Path
  platform: str
  design: str
  variant: str
  log_path: Path
  drt_seconds: int | None
  iter_count: int
  max_iter: int | None
  final_drvs: int | None
  final_viols_log: int | None
  avg_eff_cores: float | None
  min_eff_cores: float | None
  max_eff_cores: float | None


def main() -> int:
  ap = argparse.ArgumentParser(
      description="Scan ORFS logs for long-tail detailed routing (DRT) cases (by iteration count)."
  )
  ap.add_argument(
      "--root",
      action="append",
      type=Path,
      default=[Path("flow")],
      help="Directory to scan (repeatable). Default: flow",
  )
  ap.add_argument(
      "--min-iters",
      type=int,
      default=0,
      help="Only include runs with at least this many DRT iterations.",
  )
  ap.add_argument("--limit", type=int, default=50, help="Max rows to print.")
  ap.add_argument(
      "--out-csv",
      type=Path,
      default=None,
      help="Optional CSV output path (writes all rows, not just --limit).",
  )
  args = ap.parse_args()

  entries: list[Entry] = []
  for root in args.root:
    root = root.resolve()
    if not root.exists():
      continue
    for log_path in root.rglob("5_2_route.log"):
      ident = _try_parse_log_identity(log_path)
      if ident is None:
        continue
      work_home, platform, design, variant = ident
      try:
        log_text = log_path.read_text(errors="ignore")
      except OSError:
        continue
      iter_count, max_iter = _parse_iter_count(log_text)
      if iter_count < args.min_iters:
        continue
      drt_seconds = _parse_drt_seconds(log_text)
      final_viols_log = _parse_final_viols_from_log(log_text)
      avg_eff_cores, min_eff_cores, max_eff_cores = _parse_avg_effective_cores(log_text)
      metrics_path = log_path.with_suffix(".json")
      final_drvs = _parse_final_drvs_from_metrics(metrics_path)
      entries.append(
          Entry(
              work_home=work_home,
              platform=platform,
              design=design,
              variant=variant,
              log_path=log_path,
              drt_seconds=drt_seconds,
              iter_count=iter_count,
              max_iter=max_iter,
              final_drvs=final_drvs,
              final_viols_log=final_viols_log,
              avg_eff_cores=avg_eff_cores,
              min_eff_cores=min_eff_cores,
              max_eff_cores=max_eff_cores,
          )
      )

  entries.sort(key=lambda e: (e.iter_count, e.drt_seconds or -1), reverse=True)

  keys = [
      "iters",
      "drt_s",
      "final_drvs",
      "avg_eff_cores",
      "platform",
      "design",
      "variant",
      "work_home",
  ]
  print(" | ".join(keys))
  print(" | ".join(["---"] * len(keys)))
  for e in entries[: args.limit]:
    row = [
        str(e.iter_count),
        "" if e.drt_seconds is None else str(e.drt_seconds),
        "" if e.final_drvs is None else str(e.final_drvs),
        "" if e.avg_eff_cores is None else f"{e.avg_eff_cores:.2f}",
        e.platform,
        e.design,
        e.variant,
        str(e.work_home),
    ]
    print(" | ".join(row))

  if args.out_csv is not None:
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="") as f:
      w = csv.writer(f)
      w.writerow(
          [
              "work_home",
              "platform",
              "design",
              "variant",
              "log_path",
              "drt_seconds",
              "iter_count",
              "max_iter",
              "final_drvs",
              "final_viols_log",
              "avg_eff_cores",
              "min_eff_cores",
              "max_eff_cores",
          ]
      )
      for e in entries:
        w.writerow(
            [
                str(e.work_home),
                e.platform,
                e.design,
                e.variant,
                str(e.log_path),
                "" if e.drt_seconds is None else e.drt_seconds,
                e.iter_count,
                "" if e.max_iter is None else e.max_iter,
                "" if e.final_drvs is None else e.final_drvs,
                "" if e.final_viols_log is None else e.final_viols_log,
                "" if e.avg_eff_cores is None else f"{e.avg_eff_cores:.6f}",
                "" if e.min_eff_cores is None else f"{e.min_eff_cores:.6f}",
                "" if e.max_eff_cores is None else f"{e.max_eff_cores:.6f}",
            ]
        )

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
