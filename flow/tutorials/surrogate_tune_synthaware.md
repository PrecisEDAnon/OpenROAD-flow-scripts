# Synthesis-aware surrogate tuning (clock sweep)

The built-in `surrogate_tune` command evaluates a *single synthesized netlist* very quickly. When `clock_period` is part of the search space, this can break ranking because a real ORFS `finish` run re-synthesizes the netlist at the candidate clock.

`make surrogate_tune_synthaware` fixes that by:

1. Sweeping a list/range of clock periods.
2. Re-synthesizing per clock (so the netlist matches the clock constraint).
3. Running the fast surrogate optimizer on each synthesized netlist.
4. Writing a merged summary across all clocks.

## ASAP7 AES example

From repo root:

```bash
make -C flow surrogate_tune_synthaware DESIGN_CONFIG=designs/asap7/aes/config.mk \
  SURROGATE_SPACE_FILE=designs/asap7/aes/surrogate_space_no_cts_sane.json \
  SURROGATE_SAMPLES=500000 \
  SURROGATE_TOP_N=10 \
  SURROGATE_CLOCK_MIN=300 \
  SURROGATE_CLOCK_MAX=420 \
  SURROGATE_CLOCK_STEP=10 \
  SURROGATE_CALIBRATE_WS_FILE=logs/asap7/aes/base/6_report.json \
  SURROGATE_CALIBRATE_WL_FILE=logs/asap7/aes/base/5_2_route.json
```

Key knobs:

- `SURROGATE_CLOCKS="316.953 340 360 380 420"` overrides the min/max/step grid.
- `SURROGATE_GLOBAL_TOP_N=20` controls how many candidates are kept in the merged list.

## Outputs

- Per-clock tuning runs are stored under `flow/results/<platform>/<design>/<variant>_clk*/surrogate_optimize.json`.
- The merged summary is written to `flow/results/<platform>/<design>/<variant>/surrogate_synthaware.json`.
