#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path


ITER_START_RE = re.compile(
    r"^\[INFO DRT-0195\] Start (\d+)(?:st|nd|rd|th) (?:stubborn tiles|optimization) iteration\.\s*$",
    re.MULTILINE,
)
TOOK_RE = re.compile(r"^Took (\d+) seconds: detailed_route\b", re.MULTILINE)


def _parse_drt_seconds(log_text: str) -> int | None:
    times = [int(m.group(1)) for m in TOOK_RE.finditer(log_text)]
    return sum(times) if times else None


def _parse_iter_starts(log_text: str) -> tuple[int, int | None]:
    iters = [int(m.group(1)) for m in ITER_START_RE.finditer(log_text)]
    if not iters:
        return 0, None
    return len(iters), max(iters)


def _parse_final_drvs(metrics_path: Path) -> int | None:
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


def _run(cmd: list[str], env: dict[str, str]) -> None:
    subprocess.run(cmd, check=True, env=env)


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


def _variant_slug(parts: list[str]) -> str:
    safe: list[str] = []
    for p in parts:
        p = p.replace(".", "p").replace("-", "m")
        p = re.sub(r"[^A-Za-z0-9_]+", "_", p)
        p = p.strip("_")
        if p:
            safe.append(p)
    return "_".join(safe) if safe else "case"


@dataclass(frozen=True)
class Result:
    platform: str
    design: str
    design_config: str
    variant: str
    core_util: str | None
    place_density: str | None
    place_density_lb_addon: str | None
    max_routing_layer: str | None
    routing_layer_adjustment: str | None
    drt_seconds: int | None
    iter_starts: int
    max_iter: int | None
    final_drvs: int | None
    work_home: Path


def _build_make_overrides(
    *,
    user_overrides: list[str],
    core_util: str | None,
    place_density: str | None,
    place_density_lb_addon: str | None,
    max_routing_layer: str | None,
    routing_layer_adjustment: str | None,
) -> list[str]:
    overrides: list[str] = []
    if core_util is not None:
        overrides.append(f"CORE_UTILIZATION={core_util}")
    if place_density is not None:
        overrides.append(f"PLACE_DENSITY={place_density}")
    if place_density_lb_addon is not None:
        overrides.append(f"PLACE_DENSITY_LB_ADDON={place_density_lb_addon}")
    if max_routing_layer is not None:
        overrides.append(f"MAX_ROUTING_LAYER={max_routing_layer}")
    if routing_layer_adjustment is not None:
        overrides.append(f"ROUTING_LAYER_ADJUSTMENT={routing_layer_adjustment}")
    overrides.extend(user_overrides)
    return _normalize_make_overrides(overrides)


def _run_one(
    *,
    flow_dir: Path,
    openroad_exe: Path,
    threads: int,
    work_home: Path,
    design_config: str,
    variant: str,
    env: dict[str, str],
    make_overrides: list[str],
) -> None:
    cmd_base = [
        "make",
        "-C",
        str(flow_dir),
        f"DESIGN_CONFIG={design_config}",
        f"FLOW_VARIANT={variant}",
        f"WORK_HOME={work_home}",
        f"NUM_CORES={threads}",
        f"OPENROAD_EXE={openroad_exe}",
        *make_overrides,
    ]
    _run([*cmd_base, "grt"], env=env)
    _run([*cmd_base, "do-5_2_route"], env=env)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Search for naturally hard DRT cases by sweeping congestion knobs "
            "(CORE_UTILIZATION/PLACE_DENSITY/etc) and measuring DRT iteration count."
        )
    )
    ap.add_argument("--openroad", type=Path, required=True, help="Path to openroad executable.")
    ap.add_argument("--flow-dir", type=Path, default=Path("flow"), help="Path to ORFS flow dir.")
    ap.add_argument("--threads", type=int, default=32, help="Threads for OpenROAD (-threads).")
    ap.add_argument(
        "--work-home",
        type=Path,
        default=None,
        help="WORK_HOME for ORFS outputs (default: flow/benchmarks/hardcase_search/<timestamp>).",
    )
    ap.add_argument(
        "--design-config",
        action="append",
        default=None,
        help="ORFS DESIGN_CONFIG path (repeatable). Default: nangate45/jpeg and nangate45/ibex.",
    )
    ap.add_argument(
        "--core-util",
        action="append",
        default=[],
        help="CORE_UTILIZATION percent (repeatable), e.g. 90. Omit to avoid overriding.",
    )
    ap.add_argument(
        "--place-density",
        action="append",
        default=[],
        help="PLACE_DENSITY (repeatable), e.g. 0.95. Omit to avoid overriding.",
    )
    ap.add_argument(
        "--place-density-lb-addon",
        action="append",
        default=[],
        help="PLACE_DENSITY_LB_ADDON (repeatable), e.g. 0.50. Omit to avoid overriding.",
    )
    ap.add_argument(
        "--max-routing-layer",
        action="append",
        default=[],
        help="MAX_ROUTING_LAYER (repeatable), e.g. metal6. Omit to avoid overriding.",
    )
    ap.add_argument(
        "--routing-layer-adjustment",
        action="append",
        default=[],
        help="ROUTING_LAYER_ADJUSTMENT (repeatable), e.g. 0.20. Omit to avoid overriding.",
    )
    ap.add_argument(
        "--make-override",
        action="append",
        default=[],
        help="Additional Make variable override (repeatable), e.g. FLOORPLAN_DEF=.",
    )
    ap.add_argument(
        "--min-iters",
        type=int,
        default=0,
        help="Only print rows with at least this many DRT iteration starts.",
    )
    ap.add_argument("--limit", type=int, default=50, help="Max rows to print.")
    ap.add_argument(
        "--out-csv",
        type=Path,
        default=None,
        help="Optional CSV output path (default: <work_home>/hardcase_search.csv).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned variants but do not run make.",
    )
    args = ap.parse_args()

    flow_dir: Path = args.flow_dir.resolve()
    if not flow_dir.exists():
        raise SystemExit(f"flow dir not found: {flow_dir}")

    openroad_exe = args.openroad.resolve()
    if not openroad_exe.exists():
        raise SystemExit(f"openroad not found: {openroad_exe}")

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    work_home = (args.work_home or (flow_dir / "benchmarks" / "hardcase_search" / ts)).resolve()
    work_home.mkdir(parents=True, exist_ok=True)

    design_configs = args.design_config or [
        "./designs/nangate45/jpeg/config.mk",
        "./designs/nangate45/ibex/config.mk",
    ]

    grid = {
        "core_util": args.core_util or [None],
        "place_density": args.place_density or [None],
        "place_density_lb_addon": args.place_density_lb_addon or [None],
        "max_routing_layer": args.max_routing_layer or [None],
        "routing_layer_adjustment": args.routing_layer_adjustment or [None],
    }

    env = dict(os.environ)
    env.setdefault("OR_SEED", "0")
    env.setdefault("OR_K", "0")

    results: list[Result] = []
    for design_config in design_configs:
        parts = Path(design_config).parts
        if len(parts) < 3:
            raise SystemExit(f"design config path too short: {design_config}")
        platform, design = parts[-3], parts[-2]

        for core_util, place_density, lb_addon, max_layer, rla in product(
            grid["core_util"],
            grid["place_density"],
            grid["place_density_lb_addon"],
            grid["max_routing_layer"],
            grid["routing_layer_adjustment"],
        ):
            slug = _variant_slug(
                [
                    "cu" + str(core_util) if core_util is not None else "",
                    "pd" + str(place_density) if place_density is not None else "",
                    "la" + str(lb_addon) if lb_addon is not None else "",
                    "ml" + str(max_layer) if max_layer is not None else "",
                    "rla" + str(rla) if rla is not None else "",
                ]
            )
            variant = f"hc_{slug}"
            make_overrides = _build_make_overrides(
                user_overrides=args.make_override,
                core_util=core_util,
                place_density=place_density,
                place_density_lb_addon=lb_addon,
                max_routing_layer=max_layer,
                routing_layer_adjustment=rla,
            )

            print(f"== {platform}/{design} {variant} ==", flush=True)
            if not args.dry_run:
                try:
                    _run_one(
                        flow_dir=flow_dir,
                        openroad_exe=openroad_exe,
                        threads=args.threads,
                        work_home=work_home,
                        design_config=design_config,
                        variant=variant,
                        env=env,
                        make_overrides=make_overrides,
                    )
                except subprocess.CalledProcessError as e:
                    print(f"[search_hardcases] FAIL {platform}/{design} {variant}: {e}", file=sys.stderr)

            log_path = work_home / "logs" / platform / design / variant / "5_2_route.log"
            metrics_path = work_home / "logs" / platform / design / variant / "5_2_route.json"
            log_text = log_path.read_text(errors="ignore") if log_path.exists() else ""
            iter_starts, max_iter = _parse_iter_starts(log_text)
            drt_seconds = _parse_drt_seconds(log_text)
            final_drvs = _parse_final_drvs(metrics_path)
            results.append(
                Result(
                    platform=platform,
                    design=design,
                    design_config=design_config,
                    variant=variant,
                    core_util=None if core_util is None else str(core_util),
                    place_density=None if place_density is None else str(place_density),
                    place_density_lb_addon=None if lb_addon is None else str(lb_addon),
                    max_routing_layer=None if max_layer is None else str(max_layer),
                    routing_layer_adjustment=None if rla is None else str(rla),
                    drt_seconds=drt_seconds,
                    iter_starts=iter_starts,
                    max_iter=max_iter,
                    final_drvs=final_drvs,
                    work_home=work_home,
                )
            )

    results.sort(key=lambda r: (r.iter_starts, r.drt_seconds or -1), reverse=True)

    out_csv = args.out_csv or (work_home / "hardcase_search.csv")
    with out_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "platform",
                "design",
                "variant",
                "core_util",
                "place_density",
                "place_density_lb_addon",
                "max_routing_layer",
                "routing_layer_adjustment",
                "iter_starts",
                "max_iter",
                "drt_seconds",
                "final_drvs",
                "work_home",
            ]
        )
        for r in results:
            w.writerow(
                [
                    r.platform,
                    r.design,
                    r.variant,
                    r.core_util or "",
                    r.place_density or "",
                    r.place_density_lb_addon or "",
                    r.max_routing_layer or "",
                    r.routing_layer_adjustment or "",
                    r.iter_starts,
                    "" if r.max_iter is None else r.max_iter,
                    "" if r.drt_seconds is None else r.drt_seconds,
                    "" if r.final_drvs is None else r.final_drvs,
                    str(r.work_home),
                ]
            )

    keys = [
        "iters",
        "drt_s",
        "final_drvs",
        "platform",
        "design",
        "variant",
        "cu",
        "pd",
        "la",
        "ml",
        "rla",
    ]
    print("\n" + " | ".join(keys))
    print(" | ".join(["---"] * len(keys)))
    shown = 0
    for r in results:
        if r.iter_starts < args.min_iters:
            continue
        row = [
            str(r.iter_starts),
            "" if r.drt_seconds is None else str(r.drt_seconds),
            "" if r.final_drvs is None else str(r.final_drvs),
            r.platform,
            r.design,
            r.variant,
            r.core_util or "",
            r.place_density or "",
            r.place_density_lb_addon or "",
            r.max_routing_layer or "",
            r.routing_layer_adjustment or "",
        ]
        print(" | ".join(row))
        shown += 1
        if shown >= args.limit:
            break

    print(f"\nWrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

