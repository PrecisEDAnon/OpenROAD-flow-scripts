# Bullet-Point Generator (JPEG-REAL1, bullet-point-3..7)

This folder contains scripts + explicit, one-command-at-a-time invocations to reproduce the Google-doc bullet points **3–7** for the JPEG-REAL1 “Final Tests”.

Outputs are written to top-level folders:

- `bullet-point-3/`
- `bullet-point-4/`
- `bullet-point-5/`
- `bullet-point-6/`
- `bullet-point-7/`

These are gitignored by default.

## Prereqs

- A **pre-placed** JPEG run directory with (at least):
  - `3_5_place_dp.odb`
  - `3_place.sdc`
  - `6_final.def` (for DIEAREA / DBU)
- An OpenROAD binary built from the `tools/OpenROAD` submodule (recommended):
  - `./build_openroad.sh -o`
  - Expected binary: `tools/OpenROAD/build/bin/openroad`
- Python deps for the OR-Tools baseline + OpenROAD-Python plotting:
  - `pip install ortools matplotlib networkx`

## Run (one command at a time)

See `bullet-point-generator/commands.sh`. Every line is intended to be runnable independently.

After running the cases, generate the filled table with:

- `python3 bullet-point-generator/fill_amur_table.py --out amur_table_filled.md`

Notable knobs to tune QoR/runtime:
- `SCANOPT_TIME_LIMIT` (seconds, OpenROAD ScanOpt time budget)
- `SCANOPT_ROUNDS` (ScanOpt ILS rounds)
- `ORTOOLS_TIME_*` (OR-Tools time limit)

Polarity modes:
- `mid` (default): mixed polarity allowed; falling stitched before rising
- `strict`: forbids mixing polarities in a chain (so mixed-polarity `K=1` should fail)
