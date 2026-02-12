# Surrogate autotune (synthesis-aware + optional validation)

Requires an OpenROAD build with surrogate support (`ENABLE_SURROGATE=ON`).

`make surrogate_autotune` is a practical wrapper around the OpenROAD builtin surrogate that:

1. Calibrates once from an ORFS reference point (`base` by default).
2. If the search space includes `clock_period`, supports two clock-handling modes:
   - `SURROGATE_CLOCK_MODE=single_netlist` (default for `SURROGATE_OBJECTIVE=effective_clock_period`): samples `clock_period` directly (often continuous via `step: 0`) without re-synthesizing per clock.
   - `SURROGATE_CLOCK_MODE=synth_sweep`: sweeps clocks and **re-synthesizes per clock**, tuning physical knobs with `clock_period` frozen.
3. Optionally runs **full ORFS validations** for the top surrogate candidates (`SURROGATE_VALIDATE=1`), controlled by `SURROGATE_VALIDATE_N` and `SURROGATE_VALIDATE_JOBS`.

## ASAP7 AES example

From repo root:

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/asap7/aes/config.mk \
  SURROGATE_SPACE_FILE=designs/asap7/aes/surrogate_space_no_cts_sane.json \
  SURROGATE_CLOCK_MODE=synth_sweep \
  SURROGATE_SAMPLES=500000 \
  SURROGATE_TOP_N=10 \
  SURROGATE_CLOCKS="300 320 340 360 380 400 420" \
  SURROGATE_CALIBRATE_WS_FILE=logs/asap7/aes/base/6_report.json \
  SURROGATE_CALIBRATE_WL_FILE=logs/asap7/aes/base/5_2_route.json
```

Default ECP mode (single-netlist clock search; no explicit `SURROGATE_CLOCK*`):

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/asap7/aes/config.mk \
  SURROGATE_SPACE_FILE=designs/asap7/aes/surrogate_space_no_cts_sane.json
```

## Validation (slow, but “real”)

To validate the top 3 surrogate candidates with full ORFS `finish` runs:

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/asap7/aes/config.mk \
  SURROGATE_SPACE_FILE=designs/asap7/aes/surrogate_space_no_cts_sane.json \
  SURROGATE_CLOCK_MODE=synth_sweep \
  SURROGATE_CLOCKS="316.953 380" \
  SURROGATE_VALIDATE=1 \
  SURROGATE_VALIDATE_N=3 \
  SURROGATE_VALIDATE_JOBS=3
```

## Outputs

- `flow/results/<platform>/<design>/<variant>/surrogate_autotune.json`
- `flow/logs/<platform>/<design>/<variant>/surrogate_autotune.log`

Per-clock surrogate runs are stored under:

- `flow/results/<platform>/<design>/<variant>_clk*/surrogate_optimize.json`
