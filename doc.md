# Surrogate autotuner (OpenROAD + ORFS) — status / handoff (2026-01-27)

Canonical end-to-end guides (these are what we expect users to follow):

- ORFS normal branch: `surrogate_autotuner_guide.md`
- ORFS rebased branch: `flow/surrogate_autotuner_guide.md`

This `doc.md` is a “current status” handoff that ties together the 4 branches,
their pairing rules, gating, and known limitations/results.

Note: upstream `.gitignore` ignores `doc.md`, but in these surrogate branches the
file is intentionally tracked (gitignore does not apply to tracked files).

## Jan28 defaults (why this branch exists)

This `*-Jan28` branch intentionally changes **default** surrogate settings to a
heavier, “characterization-quality” configuration:

- `SURROGATE_TIME_BUDGET_S=600` (time-bounded tuning; paired with `SURROGATE_SAMPLES=1e9`)
- `SURROGATE_{TOP_N,GLOBAL_TOP_N}=560`
- Validation enabled by default: `SURROGATE_VALIDATE=1`, `K=SURROGATE_VALIDATE_N=14`, `SURROGATE_VALIDATE_JOBS=10`
- Route-prefilter enabled by default: `SURROGATE_ROUTE_VALIDATE=1`, `SURROGATE_ROUTE_VALIDATE_STAGE=grt`

Override any of these with environment variables / make args as usual.

## 2026-01-24 update (conformal + 2-stage validation)

This branch now has a “surrogate → route-prefilter → finish” pipeline that makes
conformal prediction actually matter in practice (selection diversity) and
avoids wasting full `finish` runs on candidates that were never going to route.

High-level changes:

- Conformal selection for validation uses `SURROGATE_VALIDATE_SELECT=conformal_portfolio`
  with `SURROGATE_CONFORMAL_ALPHA=0.1` (sigma defaults: `fail_risk` for ECP, `constant` for WL).
- Optional 2-stage validation:
  - Prefilter `M` candidates with `grt` (or `route`), then
  - Run full `finish` on the best `K` candidates from the prefilter set.
- The validated “best” is selected from `{baseline} ∪ {K finish runs}` (baseline is read from base logs; no rerun).
- Robustness fixes (see “Root causes fixed” below) unblocked previously flatlined cases.

## 2026-01-26 update (broad sweep CSV: 40 designs × 3 goals)

To sanity-check performance on a broader set of circuits/goals (beyond the 18-case suite),
we ran a parallel sweep and wrote a CSV in repo root:

- `surrogate_autotune_perf.csv` (run id: `bench_20260126_115652`)
- Sweep driver: `run_surrogate_sweep.py` (writes per-run logs under `bench_logs/<run_id>/`).

Sweep setup (chosen for throughput on a 112-vCPU machine):

- Parallelism: 12 designs at a time (`--jobs 12`), `NUM_CORES=8` per job (~96 OpenROAD threads total).
- Objectives: `{effective_clock_period, routed_wirelength, power}`.
- Tuning budget: `SURROGATE_TIME_BUDGET_S=300` (time-bounded; `SURROGATE_SAMPLES=1e9`).
- Candidate pool: `SURROGATE_{TOP_N,GLOBAL_TOP_N}=120`.
- Validation: `SURROGATE_VALIDATE_SELECT=conformal_portfolio`, `SURROGATE_CONFORMAL_ALPHA=0.1`,
  and small K for speed (`K=2` for ECP/WL, `K=1` for power).
- Clock sweep: disabled by setting `SURROGATE_CLOCK_FACTORS=""` (base clock only).
- Route prefilter: disabled (`SURROGATE_ROUTE_VALIDATE=0`) for throughput.
- Validation make target: report-only (`SURROGATE_VALIDATE_MAKE_TARGET=report`) so we run through `6_report`
  without generating final GDS (much faster for large sweeps).

Results summary (30/40 designs succeeded for all 3 objectives; remaining 10 failed at baseline generation):

- ECP: median improve `+0.42%` (max `+4.29%`), with `~43%` of cases selecting baseline (0%).
- WL: median improve `+1.64%` (max `+21.20%`), with `~47%` of cases selecting baseline (0%).
- Power: median improve `+1.08%` (max `+18.19%`), with `~40%` of cases selecting baseline (0%).

Top improvements observed in this sweep:

- ECP: `gf180/jpeg +4.29%`, `asap7/aes +3.79%`, `asap7/gcd +2.99%`.
- WL: `asap7/gcd-ccs +21.20%`, `asap7/gcd +13.12%`, `sky130hd/aes +12.84%`.
- Power: `asap7/ethmac_lvt +18.19%`, `asap7/ethmac +16.34%`, `asap7/mock-cpu +14.16%`.

Notes / limitations from this sweep:

- Several designs do not currently have design-specific `surrogate_space*.json`; for sweep coverage we used a generic
  no-clock space (`surrogate_space_generic_no_clock.json`). For best results, add per-design spaces.
- The 10 baseline failures are recorded in `surrogate_autotune_perf.csv` as `baseline_error`, and the exact root error
  is in `bench_logs/bench_20260126_115652/<platform>/<design>/baseline.log` (most were missing/implicit design artifacts).
- Baseline failures in this run: `asap7/{aes-block,minimal,riscv32i,riscv32i-mock-sram,swerv_wrapper,uart}`,
  `gf180/uart-blocks`, `nangate45/{swerv_wrapper,tinyRocket}`, `sky130hd/microwatt`.
- These numbers are **not comparable** to the fully tuned 18-case “Expected gains” suite below (different K, budget, and no route-prefilter).

Quality sweep (fewer designs, bigger budget/K):

- `run_surrogate_sweep.py` now supports `--preset quality`, which defaults to:
  - Platforms: `asap7,nangate45,sky130hd`
  - Designs: `aes,ibex,jpeg`
  - Objectives: `ecp,wl`
  - `SURROGATE_TIME_BUDGET_S=600`, `K=14`, route-prefilter enabled (`grt`), and ORFS default clock sweep when supported by the space.

## Current branch set (precisedanon)

The deliverable is **four branches total** (two repos × normal/rebased):

Additional snapshot branches:

- `orfs-surrogate-rebased-Jan28` ↔ `openroad-surrogate-rebased-Jan28` (same OpenROAD pin as rebased; heavier default surrogate settings + updated docs)

Repos (GitHub):

- ORFS: `git@github.com:PrecisEDAnon/OpenROAD-flow-scripts.git`
- OpenROAD: `git@github.com:precisedanon/OpenROAD.git`

### ORFS (OpenROAD-flow-scripts)

- `orfs-surrogate-normal`
  - Base: `93c42b2e6` + **1 commit** (minimal surrogate integration + docs)
  - OpenROAD submodule pin: `tools/OpenROAD @ 0b3616e102dbb3a9d76bc8233021361a4bad20bf` (`openroad-surrogate-normal`)
  - Guide: `surrogate_autotuner_guide.md`
- `orfs-surrogate-rebased`
  - Rebased on The-OpenROAD-Project `master` (then surrogate plumbing + docs)
  - OpenROAD submodule pin: `tools/OpenROAD @ f5de6e746232f0e8fc33915efba964ac3c38fc1d` (`openroad-surrogate-rebased`)
  - Guide: `flow/surrogate_autotuner_guide.md`

### OpenROAD (tools/OpenROAD)

- `openroad-surrogate-normal`
  - Base: `7bc521f36a` + **1 commit** `0b3616e102dbb3a9d76bc8233021361a4bad20bf` (“surrogate: add autotune support”)
  - Surrogate commands are compiled in and available without extra gating.
- `openroad-surrogate-rebased`
  - Base: OpenROAD `upstream/master` + `a04f00a450` + `f5de6e746232f0e8fc33915efba964ac3c38fc1d`
  - Compile-time gate: `-D ENABLE_SURROGATE=ON` (default `OFF`)
  - Runtime gate: `OPENROAD_ENABLE_SURROGATE=1` (default `OFF`)
  - Surrogate TCL commands are only registered when both gates are enabled.

Pairing rules:

- `orfs-surrogate-normal` ↔ `openroad-surrogate-normal`
- `orfs-surrogate-rebased` ↔ `openroad-surrogate-rebased`

Quick verification (precisedanon):

- OpenROAD branch heads:
  - `openroad-surrogate-normal` → `0b3616e102dbb3a9d76bc8233021361a4bad20bf`
  - `openroad-surrogate-rebased` → `f5de6e746232f0e8fc33915efba964ac3c38fc1d`
- ORFS `tools/OpenROAD` gitlink matches the corresponding OpenROAD head on each branch (see pins above).

## Quick run (normal vs rebased)

Normal:

```bash
git checkout orfs-surrogate-normal
git submodule update --init --recursive
./build_openroad.sh --local

# Baseline logs (recommended for calibration)
make -C flow finish DESIGN_CONFIG=designs/<platform>/<design>/config.mk

# Surrogate autotune (example)
make -C flow surrogate_autotune DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_VALIDATE=1 SURROGATE_VALIDATE_N=14
```

Rebased:

```bash
git checkout orfs-surrogate-rebased
git submodule update --init --recursive
./build_openroad.sh --local --openroad-args "-D ENABLE_SURROGATE=ON"

# Surrogate targets on this branch run OpenROAD with OPENROAD_ENABLE_SURROGATE=1
make -C flow surrogate_autotune DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_VALIDATE=1 SURROGATE_VALIDATE_N=14
```

Characterization config (matches the “Expected gains” table below):

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_OBJECTIVE=<effective_clock_period|routed_wirelength> \
  SURROGATE_TIME_BUDGET_S=600 SURROGATE_SAMPLES=1000000000 \
  SURROGATE_TOP_N=560 SURROGATE_GLOBAL_TOP_N=560 \
  SURROGATE_VALIDATE=1 SURROGATE_VALIDATE_N=14 SURROGATE_VALIDATE_JOBS=10 \
  SURROGATE_ROUTE_VALIDATE=1 SURROGATE_ROUTE_VALIDATE_STAGE=grt \
  SURROGATE_ROUTE_VALIDATE_N=28 SURROGATE_ROUTE_VALIDATE_JOBS=10 \
  SURROGATE_VALIDATE_SELECT=conformal_portfolio SURROGATE_CONFORMAL_ALPHA=0.1
```

## Validation workflow + accounting (answers: time + candidates)

Baseline sources (what “improve% vs baseline” means):

- `effective_clock_period` baseline comes from `flow/logs/<platform>/<design>/base/6_report.json`
  (`finish__timing__fmax` preferred, else `constraint__clock__period - finish__timing__setup__ws`).
- `routed_wirelength` baseline comes from `flow/logs/<platform>/<design>/base/5_2_route.json`
  (`detailedroute__route__wirelength`).

Time spent “optimizing” (surrogate search):

- The surrogate optimizer runs for `SURROGATE_TIME_BUDGET_S` seconds **per clock**.
- If the selected space contains `clock_period`, `surrogate_autotune` does a synth-aware clock sweep:
  - Default sweep is 5 clocks (base clock + `SURROGATE_CLOCK_FACTORS` = `0.78 0.84 0.90 0.96`).
  - With `SURROGATE_TIME_BUDGET_S=600`, surrogate time is about `5 × 600 = 3000s` (~50 minutes) + per-clock synth overhead.
- If the selected space does **not** contain `clock_period`, tuning is single-netlist:
  - With `SURROGATE_TIME_BUDGET_S=600`, surrogate time is about `600s` (~10 minutes) + minimal overhead.
  - In the on-disk suite below, WL is single-netlist only for `sky130hd/aes` (WL-safe space excludes `clock_period`); the other 8 WL cases still sweep 5 clocks.

How many candidates are actually run (WL question):

- Candidate pool from surrogate is bounded by:
  - `SURROGATE_TOP_N` per clock run, merged and truncated to `SURROGATE_GLOBAL_TOP_N` (we used `560`).
- With 2-stage validation enabled (what we used on disk):
  - Prefilter stage runs `M = SURROGATE_ROUTE_VALIDATE_N` candidates as `make grt` (or `make route`) (we used `M=28`).
  - Final stage runs `K = SURROGATE_VALIDATE_N` candidates as full `make finish` (we used `K=14`, with `SURROGATE_VALIDATE_JOBS≈10` parallel).
  - The “best for WL” is chosen from `{baseline} ∪ {K finish runs}`.
    - So: **14 finish candidates are executed**, and the final best-of comparison is over **15 options** (baseline + 14).

Validation target (runtime knob):

- Default validation runs `make finish` for each candidate (includes GDS generation).
- For faster sweeps where you only need metrics, set `SURROGATE_VALIDATE_MAKE_TARGET=report` to run through `6_report`
  without generating final GDS (internally mapped to `make flow/logs/<platform>/<design>/<variant>/6_report.log`).

## What is shipped (ORFS surface area)

New `flow/Makefile` targets:

- `make -C flow surrogate_tune` (calls OpenROAD `surrogate_optimize` on the current synthesized netlist)
- `make -C flow surrogate_tune_synthaware` (clock sweep: rewrite SDC + re-synth per clock, then tune)
- `make -C flow surrogate_autotune` (orchestrates synth-aware tuning; can optionally run `finish` validations)

Implementation entry points:

- ORFS: `flow/scripts/surrogate_tune.tcl`, `flow/scripts/surrogate_tune_synthaware.py`, `flow/scripts/surrogate_autotune.py`
- OpenROAD: `surrogate_optimize` and `surrogate_supported_features` (C++: `tools/OpenROAD/src/Surrogate.cc`)

## Supported values (objectives + knobs)

### Objectives (`SURROGATE_OBJECTIVE`)

Supported objective names (minimize):

- `effective_clock_period` (default)
- `routed_wirelength`
- `power`
- `instance_area`
- `area`

Note: empirical “expected gains” are currently only characterized on disk for
`effective_clock_period` and `routed_wirelength` at ~`600s` and `K=14` (see below).

### Knob space keys

This integration is intentionally **restricted** vs ORFS’s baseline autotuner.
Only a small fixed set of knob names is supported; unknown keys are ignored.

See the guides for the full “supported knob → ORFS variable” table and valid ranges.

## Expected gains (what users should expect)

From on-disk runs using the improved setup:

- `SURROGATE_TIME_BUDGET_S=600` (per clock), `SURROGATE_SAMPLES=1000000000` (time-bounded)
- `SURROGATE_TOP_N=560`, `SURROGATE_GLOBAL_TOP_N=560`
- `SURROGATE_VALIDATE_SELECT=conformal_portfolio`, `SURROGATE_CONFORMAL_ALPHA=0.1`
- 2-stage validation: `SURROGATE_ROUTE_VALIDATE=1`, `SURROGATE_ROUTE_VALIDATE_STAGE=grt`, `SURROGATE_ROUTE_VALIDATE_N=28`,
  then full `finish` on `SURROGATE_VALIDATE_N=14`

Across `{asap7,nangate45,sky130hd} × {aes,ibex,jpeg}` (18 total runs):

- `routed_wirelength`: median gain `7.38%` (p25 `3.88%`, p75 `15.02%`), best observed `23.45%`, worst `1.35%`
- `effective_clock_period`: median gain `4.21%` (p25 `2.31%`, p75 `6.83%`), best observed `12.59%`, worst `1.56%`

Per-design improve% vs baseline (same suite):

| platform/design | ECP improve% | WL improve% |
| --- | ---: | ---: |
| asap7/aes | 4.214% | 23.448% |
| asap7/ibex | 2.313% | 21.504% |
| asap7/jpeg | 11.592% | 6.869% |
| nangate45/aes | 4.024% | 3.278% |
| nangate45/ibex | 4.644% | 7.558% |
| nangate45/jpeg | 1.565% | 1.350% |
| sky130hd/aes | 12.595% | 15.021% |
| sky130hd/ibex | 6.832% | 7.380% |
| sky130hd/jpeg | 1.624% | 3.882% |

On-disk references for this suite:

- ECP: `flow/results/<platform>/<design>/matrix_20260104_ecp/surrogate_autotune.json`
- WL: `flow/results/<platform>/<design>/matrix_20260104_wl/surrogate_autotune.json`

Not yet characterized at ~`600s` on disk: `power`, `instance_area`, `area`.

## Outputs / artifacts

Outputs:

- `flow/results/<platform>/<design>/<variant>/surrogate_optimize.json`
- `flow/results/<platform>/<design>/<variant>/surrogate_synthaware.json`
- `flow/results/<platform>/<design>/<variant>/surrogate_autotune.json`

Logs:

- `flow/logs/<platform>/<design>/<variant>/surrogate_tune.log`
- `flow/logs/<platform>/<design>/<variant>/surrogate_tune_synthaware.log`
- `flow/logs/<platform>/<design>/<variant>/surrogate_autotune.log`

## Known limitations / TODOs (explicit)

- Objective support beyond `{effective_clock_period, routed_wirelength}` is **not yet validated** with enough on-disk runs to quote expected gain ranges.
- The knob space is intentionally restricted; this is not a drop-in replacement for baseline ORFS autotuning.

## Root causes fixed (why some runs previously flatlined)

- Missing `eqy` could break CTS when `EQUIVALENCE_CHECK=1`; `flow/scripts/load.tcl` now skips equivalence check if `eqy` is not found.
- Some OpenROAD builds don’t support `surrogate_optimize -portfolio`; `flow/scripts/surrogate_tune.tcl` retries without `-portfolio`.
- Fixed-floorplan designs (e.g. `nangate45/aes`) conflicted with tuning `CORE_UTILIZATION`; `flow/scripts/surrogate_autotune.py` clears fixed-floorplan envs when tuning utilization.
- Sky130HD designs had `fastroute.tcl` overrides that hard-forced layer adjust and ignored `PIN_LAYER_ADJUST/ABOVE_LAYER_ADJUST`,
  making many “compact WL” candidates unroutable; updated `flow/designs/sky130hd/{aes,ibex,jpeg}/fastroute.tcl` to respect env overrides.
- `flow/scripts/surrogate_autotune.py` now computes best-of as `{baseline} ∪ {validated finishes}` and emits `validation.improve_pct` when baseline logs are present.

## Disk footprint (practical note)

Full `finish` runs can generate large artifacts under `flow/results/**` (e.g. `.odb`, `.def`, `.gds`, `.spef`).
If you need to reclaim disk, the safest first pass is to keep `flow/logs/**` and remove large, reproducible outputs
under `flow/results/**` for old variants you don’t need.
