# Doomed DRT Clips — Horror Story (sky130hd/jpeg)

This guide provides a reproducible “horror story” detailed-route (DRT) case that
shows long-tail wall time, plus a “slay” mode that bounds DRT to ~3–10
iterations using ORFS multi-start + early-kill.

## What You Get

- **Baseline (control)**: `sky130hd/jpeg` with `-allow_congestion` global-route;
  detailed-route can run for **hours** (40+ iterations) due to long-tail tiles.
- **Slay (doomed)**: multi-start runs with a small per-try iteration budget
  (default 3) and accepts the best attempt, finishing in ~1000s.

## Prereqs

- ORFS branch that includes multi-start wrapper:
  - `flow/scripts/multi_start_drt.py`
  - `flow/scripts/flow.sh` hook (`DETAILED_ROUTE_MULTI_START=1`)
- Two OpenROAD builds are recommended:
  - **control**: baseline OpenROAD (e.g. `7bc521f36a`)
  - **doomed**: OpenROAD with `detailed_route -doomed_clips`

## Run It (Recommended: Benchmark Harness)

The harness writes outputs under `WORK_HOME` (default:
`flow/benchmarks/doomed_clips/<timestamp>`).

### 1) Slay first (fast)

Runs only the “doomed/slay” variant (still generates the shared GRT checkpoint):

```bash
python3 benchmarks/doomed_clips/benchmark.py \
  --suite horror \
  --mode doomed \
  --threads 32 \
  --openroad-control /path/to/control/openroad \
  --openroad-doomed /path/to/doomed/openroad \
  --openroad /path/to/doomed/openroad \
  --horror-slay-max-iter 3 \
  --horror-slay-max-runs 1 \
  --doomed-args "-doomed_clips -doomed_clips_report_n 0"
```

Artifacts:

- `WORK_HOME/logs/sky130hd/jpeg/doomed/5_2_route.log`
- `WORK_HOME/logs/sky130hd/jpeg/doomed/5_2_route.json` (includes DRVs + wirelength)
- Per-try logs under `WORK_HOME/logs/sky130hd/jpeg/doomed/_multistart/try*/`

### 2) Reproduce the horror (slow)

Runs the control `5_2_route` with `DETAILED_ROUTE_END_ITERATION=40`:

```bash
python3 benchmarks/doomed_clips/benchmark.py \
  --suite horror \
  --mode control \
  --threads 32 \
  --openroad /path/to/control/openroad \
  --horror-control-end-iter 40
```

Artifacts:

- `WORK_HOME/logs/sky130hd/jpeg/control/5_2_route.log`

## How To See “Doomed Clips”

Set `-doomed_clips_report_n` to a non-zero value, either via `--doomed-args`:

```bash
--doomed-args "-doomed_clips -doomed_clips_report_n 5"
```

or by setting ORFS variables when running `make do-5_2_route` directly.

## Expected Numbers (Proof-of-life)

From a prior run on this machine using the same checkpoint recipe:

- **Control** (`DETAILED_ROUTE_END_ITERATION=40`): iter 0–39 summed elapsed time
  `40020s` (~11.1h). Worst iteration (iter 13) was `9390s` (~2.6h).
- **Slay** (`DETAILED_ROUTE_MULTI_START_MAX_ITER=3`, accept-best): `Took 914s`
  for 4 iterations (0–3), ending at `8455` violations with wirelength `1325276`.

These numbers are intended to demonstrate **wall-time bounding**; “slay” mode is
not DRC-clean by default.

