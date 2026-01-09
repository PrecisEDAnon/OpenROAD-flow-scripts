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

## Troubleshooting

- If you see `surrogate_optimize is not available`, your OpenROAD binary is missing surrogate support:
  - rebuild OpenROAD with `-D ENABLE_SURROGATE=ON`
  - ensure the surrogate targets run with `OPENROAD_ENABLE_SURROGATE=1` (they do by default in this branch)

