# Doomed DRT Clips (OpenROAD DRT) — Handoff

This repo contains a prototype “doomed clip” handling mode for OpenROAD’s detailed router (`drt` / TritonRoute) plus OpenROAD-flow-scripts (ORFS) hooks and a benchmark harness.

Goal: reduce **long-tail detailed-route runtime** caused by a small number of stubborn tiles by (1) prioritizing expensive tiles earlier and (2) optionally exploring multiple cost settings on the worst tiles.

## Repo Layout (what’s where)

- OpenROAD submodule: `tools/OpenROAD/`
  - Baseline reference OpenROAD commit: `7bc521f36a` (called “7bc521” below).
  - Feature work is implemented in a separate OpenROAD worktree (see below).
- ORFS changes (this repo):
  - `flow/scripts/detail_route.tcl`: adds `DETAILED_ROUTE_EXTRA_ARGS` (append-only) hook.
  - `flow/scripts/global_route.tcl`: propagates `-allow_congestion` into incremental `global_route` calls (needed for “stress” experiments).
  - `docs/user/FlowVariables.md`, `docs/toc.yml`: documents the new flow variable.
- Benchmark harness (this repo):
  - `benchmarks/doomed_clips/benchmark.py`: runs control vs doomed from a shared GRT checkpoint and summarizes results.
  - `docs/user/DoomedClipsBenchmark.md`: benchmark usage notes.

## OpenROAD Feature: CLI + Behavior

The feature adds new `detailed_route` flags (see `help detailed_route` from the feature binary):

- `-doomed_clips`: enable doomed-clip scheduling.
- `-doomed_clips_report_n <n>`: print top-N “worst tiles” per iteration (diagnostic).
- `-doomed_clips_min_iter <n>`: start using previous-iteration stats at/after iteration N (default 1).
- `-doomed_clips_w_runtime <w>`, `-doomed_clips_w_drvs <w>`, `-doomed_clips_w_congestion <w>`: scoring weights.
- `-doomed_clips_top_n <n>`: for the top-N tiles, run multiple cost variants in parallel and keep the best (can increase runtime).

Implementation location (feature worktree):

- Scheduling + scoring + optional multi-cost: `tools/OpenROAD-drt-doomed-clips/src/drt/src/dr/FlexDR.cpp`
- Configuration plumbing:
  - `tools/OpenROAD-drt-doomed-clips/src/drt/include/drt/TritonRoute.h`
  - `tools/OpenROAD-drt-doomed-clips/src/drt/src/TritonRoute.cpp`
  - `tools/OpenROAD-drt-doomed-clips/src/drt/src/TritonRoute.i`
  - `tools/OpenROAD-drt-doomed-clips/src/drt/src/TritonRoute.tcl`
  - `tools/OpenROAD-drt-doomed-clips/src/drt/src/global.h`

High-level algorithm:

1. During iteration *k*, each tile/clip records runtime, DRVs (init/best), and congestion.
2. At iteration *k+1*, tiles are scored (normalized by max runtime/max DRVs) and **sorted so worst tiles run first** within each batch.
3. Optional: for the worst tiles, multiple worker cost variants run; the best result is selected.

## How To Set Up Baseline vs Feature OpenROAD (recommended)

This doc assumes you want **two OpenROAD builds**:

- baseline: OpenROAD @ `7bc521f36a`
- feature: your branch continuing from `7bc521f36a`

From the repo root:

```bash
cd tools/OpenROAD

# Create two worktrees off the baseline commit.
git worktree add ../OpenROAD-baseline-7bc521 7bc521f36a
git worktree add -b drt-doomed-clips ../OpenROAD-drt-doomed-clips 7bc521f36a
```

Build both (example with Ninja; adjust to your environment):

```bash
# Baseline
cmake -S tools/OpenROAD-baseline-7bc521 -B tools/OpenROAD-baseline-7bc521/build -G Ninja -DENABLE_TESTS=OFF
cmake --build tools/OpenROAD-baseline-7bc521/build -j

# Feature
cmake -S tools/OpenROAD-drt-doomed-clips -B tools/OpenROAD-drt-doomed-clips/build -G Ninja -DENABLE_TESTS=OFF
cmake --build tools/OpenROAD-drt-doomed-clips/build -j
```

Expected binaries:

- `tools/OpenROAD-baseline-7bc521/build/bin/openroad`
- `tools/OpenROAD-drt-doomed-clips/build/bin/openroad`

## Running A Single Case (manual sanity check)

Example: run only the detailed-route step from a global-route checkpoint:

```bash
make -C flow do-5_2_route \
  DESIGN_CONFIG=./designs/sky130hd/jpeg/config.mk \
  FLOW_VARIANT=base \
  WORK_HOME=flow/work_doombench \
  NUM_CORES=32 \
  OPENROAD_EXE=../tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  DETAILED_ROUTE_EXTRA_ARGS="-doomed_clips -doomed_clips_report_n 5"
```

Notes:

- `DETAILED_ROUTE_EXTRA_ARGS` appends to the existing `detailed_route` args; it does not replace `DETAILED_ROUTE_ARGS`.
- `WORK_HOME` controls where `logs/`, `results/`, `reports/` land.

## Benchmark Harness (control vs doomed)

The harness runs:

1. `make grt` into `FLOW_VARIANT=grt_ref` (or reuse an existing checkpoint).
2. `make do-5_2_route` twice from the same checkpoint:
   - `FLOW_VARIANT=control` (baseline binary, no extra args)
   - `FLOW_VARIANT=doomed` (feature binary, `-doomed_clips ...`)

Standard suite (aes/ibex/jpeg × nangate45/asap7/sky130hd):

```bash
python3 benchmarks/doomed_clips/benchmark.py \
  --openroad tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --openroad-control tools/OpenROAD-baseline-7bc521/build/bin/openroad \
  --openroad-doomed tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --threads 32
```

Outputs:

- `<work_home>/doomed_clips_benchmark/suite_summary.md`
- `<work_home>/doomed_clips_benchmark/suite_summary.csv`

## “Stress” Benchmarking (making the baseline explode on purpose)

To make the feature’s value obvious, you typically need cases where:

- global routing is congested (or borderline), and/or
- detailed routing has **many DRVs** and **worker time imbalance** (long tails).

Practical knobs in ORFS (use carefully):

- Increase density/utilization:
  - `PLACE_DENSITY`, `PLACE_DENSITY_LB_ADDON`, `CORE_UTILIZATION`
- Allow global-route congestion (to reach DRT anyway):
  - add `-allow_congestion` to `GLOBAL_ROUTE_ARGS`
- Bound runtime while still showing the effect:
  - set `DETAILED_ROUTE_END_ITERATION` to a small value (e.g. 2–6) so runs finish.

Example stress run (single case; tweak values per platform/design):

```bash
python3 benchmarks/doomed_clips/benchmark.py \
  --suite stress \
  --platform sky130hd \
  --design jpeg \
  --threads 64 \
  --openroad tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --openroad-control tools/OpenROAD-baseline-7bc521/build/bin/openroad \
  --openroad-doomed tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --doomed-args "-doomed_clips -doomed_clips_report_n 0" \
  --make-override GLOBAL_ROUTE_ARGS="-congestion_iterations 30 -congestion_report_iter_step 5 -verbose -allow_congestion" \
  --make-override DETAILED_ROUTE_END_ITERATION=2 \
  --make-override SKIP_ANTENNA_REPAIR_POST_DRT=1
```

Why `SKIP_ANTENNA_REPAIR_POST_DRT=1` matters:

- ORFS may re-run `detailed_route` multiple times during antenna repair; that makes `5_2_route.log` contain multiple DRT passes and can confuse naive runtime/utilization parsing.
- Disabling post-DRT antenna repair makes the benchmark’s “DRT seconds” and iteration-level stats easier to interpret.

## Tuning The Feature (what to try next)

The current implementation is intentionally simple; it’s expected that you will tune the knobs for stress cases.

Suggested experiments:

- Start using stats later:
  - `-doomed_clips_min_iter 2` (let iteration 0/1 settle before reordering)
- Change the scoring weights:
  - raise `-doomed_clips_w_runtime` to focus more on long-tail worker time
  - raise `-doomed_clips_w_drvs` to focus more on high-DRV tiles
- Enable multi-cost exploration (top-N tiles):
  - `-doomed_clips_top_n 8` or `16` (watch runtime)

Example:

```bash
--doomed-args "-doomed_clips -doomed_clips_min_iter 2 -doomed_clips_w_runtime 2.0 -doomed_clips_w_drvs 1.0 -doomed_clips_w_congestion 0.25 -doomed_clips_top_n 16 -doomed_clips_report_n 5"
```

## Known Pitfalls / Things That Look Weird

- Multiple `Start detail routing` blocks in `5_2_route.log`:
  - This is usually antenna repair re-running DRT; disable it for benchmark comparability (`SKIP_ANTENNA_REPAIR_POST_DRT=1`).
- Congestion-related failures before DRT:
  - Very aggressive utilization/density can fail earlier (placement or GRT). Increase stress gradually and record what fails where.
- `GLOBAL_ROUTE_ARGS=-allow_congestion` must be propagated into incremental GRT:
  - This repo includes a fix in `flow/scripts/global_route.tcl` so incremental `global_route -start_incremental/-end_incremental` doesn’t unexpectedly fail after an “allow congestion” run.

## Where To Start If You Want To Extend The Feature

1. **Improve classification features**
   - Current: runtime, DRVs, congested boolean
   - Candidate: pin count, net count, guide density, overflow/congestion metrics, etc.
2. **Change the scheduling policy**
   - Today: reorder within existing batching
   - Candidate: dynamic batch formation; dedicate extra workers to worst tiles; expand clip region for selected tiles.
3. **Make the benchmark more diagnostic**
   - Persist per-iteration tile reports (from `-doomed_clips_report_n`) into a structured artifact.
   - Ensure runtime/utilization metrics account for multiple DRT invocations when antenna repair is enabled.

## Quick “Done / Not Done” Status

- Implemented: OpenROAD `-doomed_clips` feature + ORFS hook to pass args + benchmark harness.
- Not finished: a stress configuration that shows a **large** and **reliable** speedup (e.g. >5–10%) across multiple runs; further tuning/measurement improvements are expected.

