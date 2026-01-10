#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


STANDARD_PLATFORMS = ("nangate45", "asap7", "sky130hd")
STANDARD_DESIGNS = ("aes", "ibex", "jpeg")

STRESS_SUITE = "stress"
HORROR_SUITE = "horror"

# Chosen to increase routing difficulty without making the suite too brittle.
STRESS_CORE_UTILIZATION = {
    "nangate45": "85",
    "asap7": "85",
    "sky130hd": "75",
}
STRESS_PLACE_DENSITY = "0.85"
STRESS_PLACE_DENSITY_LB_ADDON = "0.35"
STRESS_PER_CASE_OVERRIDES: dict[tuple[str, str], list[str]] = {
    # The standard config pins AES to a fixed floorplan DEF; disable it so
    # utilization overrides actually take effect for stress runs.
    ("nangate45", "aes"): ["FLOORPLAN_DEF="],
}

HORROR_DESIGN_CONFIG = "./designs/sky130hd/jpeg/config_horror.mk"
HORROR_PLACE_DENSITY_LB_ADDON = "0.25"
HORROR_GLOBAL_ROUTE_ARGS = (
    "-congestion_iterations 30 -congestion_report_iter_step 5 -verbose -allow_congestion"
)


@dataclass(frozen=True)
class IterTime:
  iter: int
  cpu_s: int
  elapsed_s: int

  @property
  def effective_cores(self) -> float:
    if self.elapsed_s <= 0:
      return 0.0
    return self.cpu_s / self.elapsed_s


@dataclass(frozen=True)
class RunResult:
  platform: str
  design: str
  variant: str
  drt_seconds: int | None
  final_drvs: int | None
  final_wirelength: int | None
  iter_drvs: dict[int, int]
  iter_wirelength: dict[int, int]
  iter_times: dict[int, IterTime]

  @property
  def avg_effective_cores(self) -> float | None:
    cores = [t.effective_cores for t in self.iter_times.values() if t.elapsed_s > 0]
    if not cores:
      return None
    return sum(cores) / len(cores)


def _run(cmd: list[str], env: dict[str, str], cwd: Path) -> None:
  subprocess.run(cmd, check=True, env=env, cwd=str(cwd))

def _normalize_make_overrides(overrides: list[str]) -> list[str]:
  merged: dict[str, str] = {}
  order: list[str] = []
  for item in overrides:
    if "=" not in item:
      raise ValueError(f"make override must look like NAME=VALUE (got: {item})")
    name, value = item.split("=", 1)
    if not name:
      raise ValueError(f"make override has empty NAME (got: {item})")
    if name not in merged:
      order.append(name)
    merged[name] = value
  return [f"{name}={merged[name]}" for name in order]


def _make(
    flow_dir: Path,
    make_args: list[str],
    env: dict[str, str],
) -> None:
  _run(["make", "-C", str(flow_dir), *make_args], env=env, cwd=flow_dir.parent)


def _parse_hms(hms: str) -> int:
  parts = hms.split(":")
  if len(parts) != 3:
    raise ValueError(f"bad hms: {hms}")
  h, m, s = (int(p) for p in parts)
  return h * 3600 + m * 60 + s


def parse_drt_seconds(log_text: str) -> int | None:
  times = [
      int(m.group(1))
      for m in re.finditer(
          r"^Took (\d+) seconds: detailed_route\b",
                                                log_text,
          re.MULTILINE,
      )
  ]
  if times:
    return sum(times)
  m = re.search(r"Elapsed time: ([0-9:.]+)\[h:]min:sec\.", log_text)
  if not m:
    return None
  time_str = m.group(1)
  parts = time_str.split(":")
  try:
    if len(parts) == 3:
      h = int(parts[0])
      mn = int(parts[1])
      sec = float(parts[2])
      return int(round(h * 3600 + mn * 60 + sec))
    if len(parts) == 2:
      mn = int(parts[0])
      sec = float(parts[1])
      return int(round(mn * 60 + sec))
    if len(parts) == 1:
      return int(round(float(parts[0])))
  except ValueError:
    return None
  return None


def parse_iter_times(log_text: str) -> dict[int, IterTime]:
  iter_start_re = re.compile(
      r"^\[INFO DRT-0195\] Start (\d+)(?:st|nd|rd|th) (?:stubborn tiles|optimization) iteration\.",
      re.MULTILINE,
  )
  complete_re = re.compile(
      r"^\[INFO DRT-0198\] Complete detail routing\.",
      re.MULTILINE,
  )
  time_re = re.compile(
      r"^\[INFO DRT-0267\] cpu time = (\d\d:\d\d:\d\d), elapsed time = (\d\d:\d\d:\d\d),",
      re.MULTILINE,
  )
  lines = log_text.splitlines()
  starts: list[tuple[int, int]] = []
  for idx, line in enumerate(lines):
    m = iter_start_re.match(line)
    if m:
      starts.append((int(m.group(1)), idx))

  result: dict[int, IterTime] = {}
  for i, (iter_num, start_idx) in enumerate(starts):
    end_idx = starts[i + 1][1] if i + 1 < len(starts) else len(lines)
    last_time: tuple[int, int] | None = None
    for line in lines[start_idx:end_idx]:
      if complete_re.match(line):
        break
      m = time_re.match(line)
      if not m:
        continue
      cpu_s = _parse_hms(m.group(1))
      elapsed_s = _parse_hms(m.group(2))
      last_time = (cpu_s, elapsed_s)
    if last_time is not None:
      cpu_s, elapsed_s = last_time
      result[iter_num] = IterTime(iter=iter_num, cpu_s=cpu_s, elapsed_s=elapsed_s)
  return result


def parse_route_metrics(metrics_path: Path) -> tuple[int | None, dict[int, int], int | None, dict[int, int]]:
  if not metrics_path.exists():
    return None, {}, None, {}
  data = json.loads(metrics_path.read_text())
  final_drvs = data.get("detailedroute__route__drc_errors")
  final_wirelength = data.get("detailedroute__route__wirelength")
  iter_drvs: dict[int, int] = {}
  iter_wirelength: dict[int, int] = {}
  for key, value in data.items():
    m = re.match(r"detailedroute__route__drc_errors__iter:(\d+)$", key)
    if m:
      iter_drvs[int(m.group(1))] = int(value)
      continue
    m = re.match(r"detailedroute__route__wirelength__iter:(\d+)$", key)
    if m:
      iter_wirelength[int(m.group(1))] = int(value)
      continue
  return (
      int(final_drvs) if final_drvs is not None else None,
      iter_drvs,
      int(final_wirelength) if final_wirelength is not None else None,
      iter_wirelength,
  )


def parse_run(work_home: Path, platform: str, design: str, variant: str) -> RunResult:
  log_path = work_home / "logs" / platform / design / variant / "5_2_route.log"
  metrics_path = work_home / "logs" / platform / design / variant / "5_2_route.json"
  log_text = log_path.read_text() if log_path.exists() else ""
  drt_seconds = parse_drt_seconds(log_text)
  iter_times = parse_iter_times(log_text)
  final_drvs, iter_drvs, final_wirelength, iter_wirelength = parse_route_metrics(metrics_path)
  return RunResult(
      platform=platform,
      design=design,
      variant=variant,
      drt_seconds=drt_seconds,
      final_drvs=final_drvs,
      final_wirelength=final_wirelength,
      iter_drvs=iter_drvs,
      iter_wirelength=iter_wirelength,
      iter_times=iter_times,
  )


def ensure_grt_checkpoint(
    *,
    flow_dir: Path,
    work_home: Path,
    checkpoint_work_home: Path | None,
    checkpoint_variant: str,
    design_config: str,
    pre_variant: str,
    openroad_exe: Path,
    threads: int,
    env: dict[str, str],
    make_overrides: list[str],
) -> None:
  platform = Path(design_config).parts[-3]
  design = Path(design_config).parts[-2]
  pre_results_dir = work_home / "results" / platform / design / pre_variant
  pre_results_dir.mkdir(parents=True, exist_ok=True)
  odb = pre_results_dir / "5_1_grt.odb"
  sdc = pre_results_dir / "5_1_grt.sdc"
  guides = pre_results_dir / "route.guide"
  if odb.exists() and sdc.exists() and guides.exists():
    return

  if checkpoint_work_home is not None:
    checkpoint_work_home = checkpoint_work_home.resolve()
    src = checkpoint_work_home / "results" / platform / design / checkpoint_variant
    for name in ("5_1_grt.odb", "5_1_grt.sdc", "route.guide"):
      src_path = src / name
      if not src_path.exists():
        raise FileNotFoundError(f"missing checkpoint file: {src_path}")
      shutil.copy2(src_path, pre_results_dir / name)
    return

  _make(
      flow_dir,
      [
          f"DESIGN_CONFIG={design_config}",
          f"FLOW_VARIANT={pre_variant}",
          f"WORK_HOME={work_home}",
          f"NUM_CORES={threads}",
          f"OPENROAD_EXE={openroad_exe}",
          *make_overrides,
          "grt",
      ],
      env=env,
  )


def copy_grt_checkpoint(
    *,
    work_home: Path,
    platform: str,
    design: str,
    pre_variant: str,
    dst_variant: str,
) -> None:
  src = work_home / "results" / platform / design / pre_variant
  dst = work_home / "results" / platform / design / dst_variant
  dst.mkdir(parents=True, exist_ok=True)
  for name in ("5_1_grt.odb", "5_1_grt.sdc", "route.guide"):
    shutil.copy2(src / name, dst / name)


def run_drt(
    *,
    flow_dir: Path,
    work_home: Path,
    design_config: str,
    variant: str,
    openroad_exe: Path,
    threads: int,
    env: dict[str, str],
    extra_args: str | None,
    make_overrides: list[str],
    extra_make_overrides: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> None:
  platform = Path(design_config).parts[-3]
  design = Path(design_config).parts[-2]
  copy_grt_checkpoint(
      work_home=work_home,
      platform=platform,
      design=design,
      pre_variant="grt_ref",
      dst_variant=variant,
  )
  env = dict(env)
  if extra_env:
    env.update(extra_env)
  if extra_args:
    env["DETAILED_ROUTE_EXTRA_ARGS"] = extra_args
  else:
    env.pop("DETAILED_ROUTE_EXTRA_ARGS", None)

  combined_overrides = list(make_overrides)
  if extra_make_overrides:
    combined_overrides.extend(extra_make_overrides)
  combined_overrides = _normalize_make_overrides(combined_overrides)

  _make(
      flow_dir,
      [
          f"DESIGN_CONFIG={design_config}",
          f"FLOW_VARIANT={variant}",
          f"WORK_HOME={work_home}",
          f"NUM_CORES={threads}",
          f"OPENROAD_EXE={openroad_exe}",
          *combined_overrides,
          "do-5_2_route",
      ],
      env=env,
  )


def write_summary(
    *,
    out_dir: Path,
    control: RunResult,
    doomed: RunResult,
) -> None:
  out_dir.mkdir(parents=True, exist_ok=True)
  speedup = None
  if control.drt_seconds and doomed.drt_seconds and doomed.drt_seconds > 0:
    speedup = control.drt_seconds / doomed.drt_seconds
  row = {
      "platform": control.platform,
      "design": control.design,
      "threads": "",
      "control_drt_s": control.drt_seconds,
      "doomed_drt_s": doomed.drt_seconds,
      "speedup": f"{speedup:.3f}" if speedup is not None else "",
      "control_final_drvs": control.final_drvs,
      "doomed_final_drvs": doomed.final_drvs,
      "control_avg_eff_cores": f"{control.avg_effective_cores:.2f}"
      if control.avg_effective_cores is not None
      else "",
      "doomed_avg_eff_cores": f"{doomed.avg_effective_cores:.2f}"
      if doomed.avg_effective_cores is not None
      else "",
      "control_iter_count": len(control.iter_times),
      "doomed_iter_count": len(doomed.iter_times),
  }
  csv_path = out_dir / "summary.csv"
  md_path = out_dir / "summary.md"
  keys = list(row.keys())
  with csv_path.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    w.writerow(row)
  with md_path.open("w") as f:
    f.write("| " + " | ".join(keys) + " |\n")
    f.write("| " + " | ".join(["---"] * len(keys)) + " |\n")
    f.write("| " + " | ".join(str(row[k]) for k in keys) + " |\n")


def main() -> int:
  ap = argparse.ArgumentParser(
      description="Benchmark doomed-clip scheduling in OpenROAD DRT (ORFS step 5_2_route)."
  )
  ap.add_argument(
      "--openroad",
      type=Path,
      required=True,
      help="Path to openroad executable (default used for both control and doomed runs).",
  )
  ap.add_argument(
      "--openroad-control",
      type=Path,
      default=None,
      help="Path to openroad executable for the control run (default: --openroad).",
  )
  ap.add_argument(
      "--openroad-doomed",
      type=Path,
      default=None,
      help="Path to openroad executable for the doomed run (default: --openroad).",
  )
  ap.add_argument("--flow-dir", type=Path, default=Path("flow"), help="Path to ORFS flow dir.")
  ap.add_argument("--threads", type=int, default=32, help="Threads for OpenROAD (-threads).")
  ap.add_argument(
      "--work-home",
      type=Path,
      default=None,
      help="WORK_HOME for ORFS outputs (default: flow/benchmarks/doomed_clips/<timestamp>).",
  )
  ap.add_argument(
      "--suite",
      choices=("standard", STRESS_SUITE, HORROR_SUITE),
      default="standard",
      help="Benchmark suite selection.",
  )
  ap.add_argument(
      "--mode",
      choices=("both", "control", "doomed"),
      default="both",
      help="Which variants to run (default: both).",
  )
  ap.add_argument(
      "--platform",
      action="append",
      choices=STANDARD_PLATFORMS,
      default=None,
      help="Restrict to a platform (repeatable). Default: all standard platforms.",
  )
  ap.add_argument(
      "--design",
      action="append",
      choices=STANDARD_DESIGNS,
      default=None,
      help="Restrict to a design (repeatable). Default: all standard designs.",
  )
  ap.add_argument(
      "--design-config",
      action="append",
      default=None,
      help=(
          "Explicit ORFS DESIGN_CONFIG path (repeatable). "
          "When set, overrides --suite/--platform/--design selection."
      ),
  )
  ap.add_argument(
      "--make-override",
      action="append",
      default=None,
      help="Additional Make variable override (repeatable), e.g. CORE_UTILIZATION=85.",
  )
  ap.add_argument(
      "--summarize-only",
      action="store_true",
      help="Do not run the flow; only parse existing logs under --work-home and write summaries.",
  )
  ap.add_argument(
      "--checkpoint-work-home",
      type=Path,
      default=None,
      help=(
          "Existing WORK_HOME to source 5_1_grt checkpoint files from "
          "(expects results/<platform>/<design>/<variant>/{5_1_grt.odb,5_1_grt.sdc,route.guide})."
      ),
  )
  ap.add_argument(
      "--checkpoint-variant",
      default="base",
      help="Variant name under --checkpoint-work-home to source checkpoints from (default: base).",
  )
  ap.add_argument(
      "--doomed-args",
      default="-doomed_clips -doomed_clips_report_n 0",
      help="Args appended to detailed_route when enabled.",
  )
  ap.add_argument(
      "--or-seed",
      default="0",
      help="OR_SEED environment variable (for detailed_route -or_seed).",
  )
  ap.add_argument(
      "--or-k",
      default="0",
      help="OR_K environment variable (for detailed_route -or_k).",
  )
  ap.add_argument(
      "--out-dir",
      type=Path,
      default=None,
      help="Directory for summary artifacts (default: <work_home>/doomed_clips_benchmark).",
  )
  ap.add_argument(
      "--horror-control-end-iter",
      type=int,
      default=40,
      help=f"For --suite {HORROR_SUITE}: DETAILED_ROUTE_END_ITERATION for the control run.",
  )
  ap.add_argument(
      "--horror-slay-max-iter",
      type=int,
      default=3,
      help=f"For --suite {HORROR_SUITE}: DETAILED_ROUTE_MULTI_START_MAX_ITER for the doomed run.",
  )
  ap.add_argument(
      "--horror-slay-max-runs",
      type=int,
      default=1,
      help=f"For --suite {HORROR_SUITE}: DETAILED_ROUTE_MULTI_START_MAX_RUNS for the doomed run.",
  )
  args = ap.parse_args()

  flow_dir: Path = args.flow_dir
  if not flow_dir.exists():
    raise SystemExit(f"flow dir not found: {flow_dir}")

  work_home = args.work_home
  if args.summarize_only and work_home is None:
    raise SystemExit("--summarize-only requires --work-home")
  if work_home is None:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    work_home = flow_dir / "benchmarks" / "doomed_clips" / ts
  work_home = work_home.resolve()

  out_dir = args.out_dir or (work_home / "doomed_clips_benchmark")
  out_dir = out_dir.resolve()

  suite: list[tuple[str, str, str]] = []
  if args.design_config:
    for design_config in args.design_config:
      parts = Path(design_config).parts
      if len(parts) < 3:
        raise SystemExit(f"design config path too short: {design_config}")
      platform = parts[-3]
      design = parts[-2]
      suite.append((platform, design, design_config))
  else:
    if args.suite == HORROR_SUITE:
      suite.append(("sky130hd", "jpeg", HORROR_DESIGN_CONFIG))
    else:
      platforms = args.platform or list(STANDARD_PLATFORMS)
      designs = args.design or list(STANDARD_DESIGNS)
      for platform in platforms:
        for design in designs:
          suite.append((platform, design, f"./designs/{platform}/{design}/config.mk"))
  env = dict(os.environ)
  env["OR_SEED"] = str(args.or_seed)
  env["OR_K"] = str(args.or_k)

  openroad_control: Path | None = None
  openroad_doomed: Path | None = None
  if not args.summarize_only:
    openroad_default: Path = args.openroad.resolve()
    openroad_control = (args.openroad_control or openroad_default).resolve()
    openroad_doomed = (args.openroad_doomed or openroad_default).resolve()
    for p in (openroad_control, openroad_doomed):
      if not p.exists():
        raise SystemExit(f"openroad not found: {p}")

  pre_variant = "grt_ref"
  control_variant = "control"
  doomed_variant = "doomed"

  results: list[tuple[RunResult, RunResult]] = []
  for platform, design, design_config in suite:
    make_overrides: list[str] = []
    user_overrides = args.make_override or []

    if args.suite == STRESS_SUITE:
      core_util = STRESS_CORE_UTILIZATION.get(platform)
      if core_util is None:
        raise SystemExit(f"no stress CORE_UTILIZATION for platform: {platform}")
      make_overrides.extend(
          [
              f"CORE_UTILIZATION={core_util}",
              f"PLACE_DENSITY={STRESS_PLACE_DENSITY}",
              f"PLACE_DENSITY_LB_ADDON={STRESS_PLACE_DENSITY_LB_ADDON}",
          ]
      )
      make_overrides.extend(STRESS_PER_CASE_OVERRIDES.get((platform, design), []))

    if args.suite == HORROR_SUITE:
      make_overrides.extend(
          [
              f"PLACE_DENSITY_LB_ADDON={HORROR_PLACE_DENSITY_LB_ADDON}",
              f"GLOBAL_ROUTE_ARGS={HORROR_GLOBAL_ROUTE_ARGS}",
              "SKIP_ANTENNA_REPAIR_POST_DRT=1",
          ]
      )

    make_overrides.extend(user_overrides)
    try:
      make_overrides = _normalize_make_overrides(make_overrides)
    except ValueError as e:
      raise SystemExit(str(e))

    print(f"== {platform}/{design} ==", flush=True)

    if not args.summarize_only:
      assert openroad_control is not None
      assert openroad_doomed is not None
      ensure_grt_checkpoint(
          flow_dir=flow_dir,
          work_home=work_home,
          checkpoint_work_home=args.checkpoint_work_home,
          checkpoint_variant=args.checkpoint_variant,
          design_config=design_config,
          pre_variant=pre_variant,
          openroad_exe=openroad_control,
          threads=args.threads,
          env=env,
          make_overrides=make_overrides,
      )
      if args.mode in ("both", "control"):
        run_drt(
            flow_dir=flow_dir,
            work_home=work_home,
            design_config=design_config,
            variant=control_variant,
            openroad_exe=openroad_control,
            threads=args.threads,
            env=env,
            extra_args=None,
            make_overrides=make_overrides,
            extra_make_overrides=[
                f"DETAILED_ROUTE_END_ITERATION={args.horror_control_end_iter}"
            ]
            if args.suite == HORROR_SUITE
            else None,
        )
      if args.mode in ("both", "doomed"):
        doomed_env: dict[str, str] | None = None
        if args.suite == HORROR_SUITE:
          doomed_env = {
              "DETAILED_ROUTE_MULTI_START": "1",
              "DETAILED_ROUTE_MULTI_START_MAX_ITER": str(args.horror_slay_max_iter),
              "DETAILED_ROUTE_MULTI_START_MAX_RUNS": str(args.horror_slay_max_runs),
              "DETAILED_ROUTE_MULTI_START_ACCEPT_BEST": "1",
              "DETAILED_ROUTE_MULTI_START_FALLBACK_TO_SINGLE": "0",
              "DETAILED_ROUTE_MULTI_START_OR_K": "0.0",
          }
        run_drt(
            flow_dir=flow_dir,
            work_home=work_home,
            design_config=design_config,
            variant=doomed_variant,
            openroad_exe=openroad_doomed,
            threads=args.threads,
            env=env,
            extra_args=args.doomed_args,
            make_overrides=make_overrides,
            extra_env=doomed_env,
        )

    control = parse_run(work_home, platform, design, control_variant)
    doomed = parse_run(work_home, platform, design, doomed_variant)
    results.append((control, doomed))

  # Write combined summary
  out_dir.mkdir(parents=True, exist_ok=True)
  csv_path = out_dir / "suite_summary.csv"
  md_path = out_dir / "suite_summary.md"
  rows: list[dict[str, str]] = []
  for control, doomed in results:
    speedup = ""
    if control.drt_seconds and doomed.drt_seconds and doomed.drt_seconds > 0:
      speedup = f"{control.drt_seconds / doomed.drt_seconds:.3f}"
    rows.append(
        {
            "platform": control.platform,
            "design": control.design,
            "control_drt_s": "" if control.drt_seconds is None else str(control.drt_seconds),
            "doomed_drt_s": "" if doomed.drt_seconds is None else str(doomed.drt_seconds),
            "speedup": speedup,
            "control_final_drvs": "" if control.final_drvs is None else str(control.final_drvs),
            "doomed_final_drvs": "" if doomed.final_drvs is None else str(doomed.final_drvs),
            "control_final_wirelength": ""
            if control.final_wirelength is None
            else str(control.final_wirelength),
            "doomed_final_wirelength": ""
            if doomed.final_wirelength is None
            else str(doomed.final_wirelength),
            "control_avg_eff_cores": f"{control.avg_effective_cores:.2f}"
            if control.avg_effective_cores is not None
            else "",
            "doomed_avg_eff_cores": f"{doomed.avg_effective_cores:.2f}"
            if doomed.avg_effective_cores is not None
            else "",
            "control_iter_count": str(len(control.iter_times)),
            "doomed_iter_count": str(len(doomed.iter_times)),
        }
    )
  keys = list(rows[0].keys()) if rows else []
  with csv_path.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    for row in rows:
      w.writerow(row)
  with md_path.open("w") as f:
    f.write("| " + " | ".join(keys) + " |\n")
    f.write("| " + " | ".join(["---"] * len(keys)) + " |\n")
    for row in rows:
      f.write("| " + " | ".join(row[k] for k in keys) + " |\n")

  print(f"Wrote {md_path}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
