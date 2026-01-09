# Surrogate autotune (synthesis-aware + optional validation)

Requires an OpenROAD build with surrogate support (`ENABLE_SURROGATE=ON`).

`make surrogate_autotune` is a practical wrapper around the OpenROAD builtin surrogate that:

1. Calibrates once from an ORFS reference point (`base` by default).
2. If the search space includes `clock_period`, sweeps clocks by **re-synthesizing per clock** and tuning physical knobs with `clock_period` frozen (avoids the single-netlist clock mismatch).
3. Optionally runs a small number of **full ORFS `finish` validations** for the top surrogate candidates.

## ASAP7 AES example

From repo root:

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/asap7/aes/config.mk \
  SURROGATE_SPACE_FILE=designs/asap7/aes/surrogate_space_no_cts_sane.json \
  SURROGATE_SAMPLES=500000 \
  SURROGATE_TOP_N=10 \
  SURROGATE_CLOCKS="300 320 340 360 380 400 420" \
  SURROGATE_CALIBRATE_WS_FILE=logs/asap7/aes/base/6_report.json \
  SURROGATE_CALIBRATE_WL_FILE=logs/asap7/aes/base/5_2_route.json
```

Auto clock selection (no explicit `SURROGATE_CLOCK*`):

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/asap7/aes/config.mk \
  SURROGATE_SPACE_FILE=designs/asap7/aes/surrogate_space_no_cts_sane.json
```

## Validation (slow, but “real”)

To validate the top 3 surrogate candidates with full ORFS `finish` runs:

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/asap7/aes/config.mk \
  SURROGATE_SPACE_FILE=designs/asap7/aes/surrogate_space_no_cts_sane.json \
  SURROGATE_CLOCKS="316.953 380" \
  SURROGATE_VALIDATE=1 \
  SURROGATE_VALIDATE_N=3
```

## Outputs

- `flow/results/<platform>/<design>/<variant>/surrogate_autotune.json`
- `flow/logs/<platform>/<design>/<variant>/surrogate_autotune.log`

Per-clock surrogate runs are stored under:

- `flow/results/<platform>/<design>/<variant>_clk*/surrogate_optimize.json`
