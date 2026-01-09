# Surrogate autotuner guide (rebased branch)

This guide is for the **rebased** surrogate integration:

- ORFS branch: `orfs-surrogate-rebased`
- OpenROAD branch: `openroad-surrogate-rebased` (pinned via `tools/OpenROAD`)

In the rebased integration, surrogate support is:

- **Compile-time gated** (CMake): `-D ENABLE_SURROGATE=ON`
- **Runtime gated** (env): `OPENROAD_ENABLE_SURROGATE=1`

This keeps default OpenROAD/ORFS behavior unchanged unless you explicitly opt in.

## 1) Checkout

From the ORFS repo root:

```bash
git checkout orfs-surrogate-rebased
git submodule update --init --recursive
```

## 2) Build OpenROAD with surrogate enabled

Build and install into `tools/install/...`:

```bash
./build_openroad.sh --local --openroad-args "-D ENABLE_SURROGATE=ON"
```

If you already have a surrogate-enabled OpenROAD binary elsewhere, you can use it by setting `SURROGATE_OPENROAD_EXE` when running the surrogate targets (below).

## 3) (Recommended) Create calibration logs (baseline)

The tuning wrappers default to using:

- `flow/logs/<platform>/<design>/base/6_report.json`
- `flow/logs/<platform>/<design>/base/5_2_route.json`

Create those with a normal ORFS run:

```bash
make -C flow finish DESIGN_CONFIG=designs/<platform>/<design>/config.mk
```

You can override the calibration inputs with:

- `SURROGATE_CALIBRATE_WS_FILE=/path/to/6_report.json`
- `SURROGATE_CALIBRATE_WL_FILE=/path/to/5_2_route.json`

## 4) Run the surrogate autotuner

All surrogate targets:

- run OpenROAD with `OPENROAD_ENABLE_SURROGATE=1`
- use `SURROGATE_OPENROAD_EXE` (defaults to `OPENROAD_EXE`)

### 4.1) Define the search space (`surrogate_space.json`)

The surrogate autotuner is intentionally **more restricted** than the baseline
ORFS autotuner: it only understands a small, fixed set of knob names and types.

The search space format is a JSON **object** mapping knob-name → spec:

```json
{
  "core_utilization": { "type": "int",   "minmax": [20, 99],   "step": 1 },
  "core_aspect_ratio":{ "type": "float", "minmax": [0.8, 1.2], "step": 0 },
  "enable_dpo":       { "type": "binary","minmax": [0, 1],     "step": 1 }
}
```

Rules:

- `type` must be one of: `float`, `int`, `binary`
- `minmax: [min, max]` is required for all knobs (even `binary`)
- `step` is optional:
  - if `step > 0`, values are sampled on `min + k*step` (inclusive), then rounded for `int`
  - if `step == 0` or omitted, values are sampled uniformly over `[min, max]` (then rounded for `int`)
- `binary` always samples `0` or `1` (the `minmax/step` fields are required but effectively ignored)
- Unknown knob names are ignored (OpenROAD logs a warning and continues)

### 4.2) Supported knobs + valid values

These are the only supported surrogate **design knobs** (space keys), and how
they map onto ORFS variables for the optional validation runs:

| Space key | ORFS variable | Type | Valid values |
|---|---|---:|---|
| `clock_period` | (via `SDC_FILE`) | float | `> 0` in the same units as your SDC (the wrappers rewrite `SDC_FILE` by substituting this value into `clk_period` / `create_clock -period`) |
| `core_utilization` | `CORE_UTILIZATION` | int | `0..100` (%); surrogate model effectively clamps to about `20..99` |
| `core_aspect_ratio` | `CORE_ASPECT_RATIO` | float | `> 0`; surrogate model effectively clamps to about `0.2..5.0` |
| `tns_end_percent` | `TNS_END_PERCENT` | int | `0..100` |
| `global_padding` | `CELL_PAD_IN_SITES_GLOBAL_PLACEMENT` | int | `>= 0` (sites) |
| `detail_padding` | `CELL_PAD_IN_SITES_DETAIL_PLACEMENT` | int | `>= 0` (sites) |
| `enable_dpo` | `ENABLE_DPO` | binary | `0` or `1` |
| `pin_layer_adjust` | `PIN_LAYER_ADJUST` | float | `0.0..1.0` (routing capacity adjustment factor) |
| `above_layer_adjust` | `ABOVE_LAYER_ADJUST` | float | `0.0..1.0` (routing capacity adjustment factor) |
| `density_margin_addon` | `PLACE_DENSITY_LB_ADDON` | float | `0.0..0.99` (ORFS errors out above `0.99`) |
| `cts_cluster_size` | `CTS_CLUSTER_SIZE` | int | `>= 1` (sinks/cluster) |
| `cts_cluster_diameter` | `CTS_CLUSTER_DIAMETER` | float | `> 0` (microns) |

Notes:

- `clock_period` is handled **synthesis-aware** when present in the space:
  - the wrappers sweep clocks by rewriting `SDC_FILE` and re-synthesizing per clock
  - surrogate tuning itself freezes `clock_period` (avoids “single-netlist clock mismatch”)
- `density_margin_addon` maps to `PLACE_DENSITY_LB_ADDON`, which overrides `PLACE_DENSITY` in ORFS when set.
- Routing adjust knobs use a simple split: first two routing layers get `PIN_LAYER_ADJUST`, and the rest get `ABOVE_LAYER_ADJUST` (fallback is the platform default when unset).

### 4.3) Choose the optimization objective (`SURROGATE_OBJECTIVE`)

The surrogate autotuner supports a small, fixed set of objective names
(default: `effective_clock_period`):

- `effective_clock_period`
- `routed_wirelength`
- `power`
- `instance_area`
- `area`

### A) Fast tuning on one synthesized netlist

```bash
make -C flow surrogate_tune DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_SPACE_FILE=designs/<platform>/<design>/surrogate_space.json \
  SURROGATE_SAMPLES=20000 \
  SURROGATE_TOP_N=10
```

### B) Synthesis-aware tuning (clock sweep)

```bash
make -C flow surrogate_tune_synthaware DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_SPACE_FILE=designs/<platform>/<design>/surrogate_space.json \
  SURROGATE_CLOCKS="300 320 340 360 380 400 420" \
  SURROGATE_SAMPLES=500000 \
  SURROGATE_TOP_N=10
```

### C) Autotune (synthesis-aware + optional full-ORFS validation)

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_SPACE_FILE=designs/<platform>/<design>/surrogate_space.json \
  SURROGATE_SAMPLES=500000 \
  SURROGATE_TOP_N=10 \
  SURROGATE_VALIDATE=1 \
  SURROGATE_VALIDATE_N=20
```

## Using a separate surrogate-enabled OpenROAD binary

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_OPENROAD_EXE=/path/to/surrogate-enabled/openroad \
  SURROGATE_SPACE_FILE=designs/<platform>/<design>/surrogate_space.json
```

## Outputs

- `flow/results/<platform>/<design>/<variant>/surrogate_optimize.json`
- `flow/results/<platform>/<design>/<variant>/surrogate_synthaware.json`
- `flow/results/<platform>/<design>/<variant>/surrogate_autotune.json`

Logs are written under `flow/logs/<platform>/<design>/<variant>/`.

## Expected gains (empirical)

From on-disk runs with `SURROGATE_TIME_BUDGET_S=600` and `SURROGATE_VALIDATE_N=14`
(K=14), across `{asap7,nangate45,sky130hd} × {aes,ibex,jpeg}`:

- `routed_wirelength`: median gain `3.35%` (p25 `0.96%`, p75 `7.37%`), best observed `15.78%`, worst `0%`
- `effective_clock_period`: median gain `2.99%` (p25 `1.61%`, p75 `4.71%`), best observed `11.58%`, worst `0%`

Notes:

- Gains are vs the design’s baseline (`flow/logs/<platform>/<design>/base/...`); baseline is always a candidate, so gains are non-negative by construction.
- These runs only cover `routed_wirelength` and `effective_clock_period` at `600s`. Power/area at this budget are not yet characterized on disk.

## Troubleshooting

- If you see `surrogate_optimize is not available`, your OpenROAD binary is missing surrogate support:
  - rebuild OpenROAD with `-D ENABLE_SURROGATE=ON`
  - ensure the surrogate targets run with `OPENROAD_ENABLE_SURROGATE=1` (they do by default in this branch)
