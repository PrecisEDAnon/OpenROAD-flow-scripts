# Benchmarking Doomed DRT Clips

This benchmark quantifies the impact of OpenROAD DRT “doomed clip” handling (`detailed_route -doomed_clips`) on **detailed-route runtime tail** and **thread utilization**.

The same hooks can also be used as a **diagnostic**: `-doomed_clips_report_n` prints the top-N worst tiles per iteration.

## What To Measure

For each run, collect:

- **Detailed-route runtime (seconds)**: parsed from `Took <N> seconds: detailed_route ...` in `5_2_route.log`.
- **Per-iteration CPU vs elapsed time**: parsed from `[INFO DRT-0267] cpu time = ... elapsed time = ...` in `5_2_route.log`.
  - Compute **effective cores** per iteration as `cpu_seconds / elapsed_seconds` (higher is better; long-tail imbalance lowers this).
- **Per-iteration DRVs**: from `5_2_route.json` keys `detailedroute__route__drc_errors__iter:<iter>`.

The “doomed clips” feature is considered successful when **elapsed time drops** and **effective cores rises**, with **no regression in DRVs**.

## Standard Suite

- Platforms: `nangate45`, `asap7`, `sky130hd`
- Designs: `aes`, `ibex`, `jpeg`

## Running The Benchmark

### 1) Build OpenROAD with the feature

Use the OpenROAD worktree that contains the `-doomed_clips` implementation (example path from this repo setup):

- `tools/OpenROAD-drt-doomed-clips/build/bin/openroad`

For a baseline comparison, also build the baseline worktree (OpenROAD `7bc521`):

- `tools/OpenROAD-baseline-7bc521/build/bin/openroad`

### 2) Run the suite

From the repo root:

```bash
python3 benchmarks/doomed_clips/benchmark.py \
  --openroad tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --openroad-control tools/OpenROAD-baseline-7bc521/build/bin/openroad \
  --openroad-doomed tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --threads 32
```

What it does:

- Creates a shared **global-route checkpoint** per platform/design (`FLOW_VARIANT=grt_ref`, `make grt`).
- Runs detailed route twice from the same checkpoint:
  - `FLOW_VARIANT=control` (no extra args)
  - `FLOW_VARIANT=doomed` (adds `-doomed_clips`, default: `-doomed_clips -doomed_clips_report_n 0`)
- Writes summary tables:
  - `<work_home>/doomed_clips_benchmark/suite_summary.md`
  - `<work_home>/doomed_clips_benchmark/suite_summary.csv`

### 2a) Faster: reuse an existing checkpoint

Generating a fresh `grt_ref` checkpoint requires running the flow up to global-route prerequisites. For quick iterations, reuse an existing WORK_HOME that already contains `5_1_grt.{odb,sdc}` and `route.guide` (for example, a previous `make route` run or a `flow/backup_*` directory):

```bash
python3 benchmarks/doomed_clips/benchmark.py \
  --openroad tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --openroad-control tools/OpenROAD-baseline-7bc521/build/bin/openroad \
  --openroad-doomed tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --checkpoint-work-home flow/backup_20251122_234546 \
  --threads 32
```

### 3) Tuning the experiment

- Change the “doomed” args:

```bash
python3 benchmarks/doomed_clips/benchmark.py \
  --openroad tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --openroad-control tools/OpenROAD-baseline-7bc521/build/bin/openroad \
  --openroad-doomed tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --threads 32 \
  --doomed-args "-doomed_clips -doomed_clips_report_n 0 -doomed_clips_min_iter 2"
```

- If you want “multi-cost exploration” on the worst clips (may increase runtime, sometimes improves convergence):

```bash
python3 benchmarks/doomed_clips/benchmark.py \
  --openroad tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --openroad-control tools/OpenROAD-baseline-7bc521/build/bin/openroad \
  --openroad-doomed tools/OpenROAD-drt-doomed-clips/build/bin/openroad \
  --threads 32 \
  --doomed-args "-doomed_clips -doomed_clips_report_n 0 -doomed_clips_top_n 16"
```

### 4) Doomed clip report (diagnostic)

Set `-doomed_clips_report_n` to a non-zero value to print the top-N worst tiles each iteration. Example snippet:

```text
Doomed clips (iter 4) top 5:
  gcell(  69   83) score  1.45 time   0.38s init    9 best    2
  gcell(  69   48) score  1.00 time   0.85s init   37 best    0
  ...
```

Interpretation (high-level):

- `gcell(x y)`: tile coordinate.
- `score`: weighted sum used for prioritization.
- `time`: measured clip time from the previous iteration.
- `init`: clip DRVs at start of routing that clip in the iteration.
- `best`: best clip DRVs achieved (relevant when `-doomed_clips_top_n` is enabled).

### 5) Re-summarize an existing run

If you already ran the suite and only want to regenerate `suite_summary.*` from logs/metrics:

```bash
python3 benchmarks/doomed_clips/benchmark.py \
  --summarize-only \
  --work-home flow/benchmarks/doomed_clips/<timestamp> \
  --openroad tools/OpenROAD-drt-doomed-clips/build/bin/openroad
```

## Example Results (standard suite)

Example output from `suite_summary.md` (32 threads, checkpoint reuse, baseline vs `-doomed_clips`), showing **no DRV regression** and mostly **sub-2% runtime deltas**:

| platform | design | control_drt_s | doomed_drt_s | speedup |
| --- | --- | --- | --- | --- |
| nangate45 | aes | 81 | 80 | 1.012 |
| nangate45 | ibex | 60 | 59 | 1.017 |
| nangate45 | jpeg | 122 | 120 | 1.017 |
| asap7 | aes | 206 | 205 | 1.005 |
| asap7 | ibex | 317 | 315 | 1.006 |
| asap7 | jpeg | 353 | 354 | 0.997 |
| sky130hd | aes | 665 | 666 | 0.998 |
| sky130hd | ibex | 359 | 357 | 1.006 |
| sky130hd | jpeg | 348 | 350 | 0.994 |

## Flow Variables

- `DETAILED_ROUTE_DOOMED_CLIPS` (default 0): enable `detailed_route -doomed_clips`.
  - Optional tuning knobs (used when enabled): `DETAILED_ROUTE_DOOMED_CLIPS_REPORT_N`, `DETAILED_ROUTE_DOOMED_CLIPS_TOP_N`, `DETAILED_ROUTE_DOOMED_CLIPS_MIN_ITER`, `DETAILED_ROUTE_DOOMED_CLIPS_W_RUNTIME`, `DETAILED_ROUTE_DOOMED_CLIPS_W_DRVS`, `DETAILED_ROUTE_DOOMED_CLIPS_W_CONGESTION`.
- `DETAILED_ROUTE_EXTRA_ARGS`: appends raw arguments to `detailed_route` without overriding `DETAILED_ROUTE_ARGS` (escape hatch; appended after the toggles so it can override them).
