# Surrogate autotuner: branch-to-run instructions

This repo has two surrogate integration styles:

- **Normal**: minimal enablement (surrogate is always available in OpenROAD).
- **Rebased**: PR-style integration (surrogate is **opt-in** and does not affect default flows).

## Branch pairing

- **Normal pair**
  - ORFS: `orfs-surrogate-normal`
  - OpenROAD: `openroad-surrogate-normal`
- **Rebased pair**
  - ORFS: `orfs-surrogate-rebased`
  - OpenROAD: `openroad-surrogate-rebased`

The ORFS branches already pin the matching OpenROAD commit via `tools/OpenROAD`.

## Build + run (normal)

1) Checkout ORFS and its pinned OpenROAD:

```bash
git checkout orfs-surrogate-normal
git submodule update --init --recursive
```

2) Build tools:

```bash
./build_openroad.sh --local
```

3) (Recommended) Produce baseline logs for calibration:

```bash
make -C flow finish DESIGN_CONFIG=designs/<platform>/<design>/config.mk
```

4) Run surrogate tuning:

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_SPACE_FILE=designs/<platform>/<design>/surrogate_space.json
```

## Build + run (rebased, opt-in)

1) Checkout ORFS and its pinned OpenROAD:

```bash
git checkout orfs-surrogate-rebased
git submodule update --init --recursive
```

2) Build OpenROAD with surrogate compiled in (default is OFF):

```bash
./build_openroad.sh --local --openroad-args "-D ENABLE_SURROGATE=ON"
```

3) Baseline logs + run commands are the same as the normal pair.

ORFS surrogate targets run OpenROAD with `OPENROAD_ENABLE_SURROGATE=1`, so default flow stages remain unchanged unless you call the surrogate targets.

If you keep a separate surrogate-enabled OpenROAD binary, point ORFS at it:

```bash
make -C flow surrogate_autotune DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_OPENROAD_EXE=/path/to/surrogate-enabled/openroad
```

## Common knobs / files

- Calibration inputs (defaults):
  - `flow/logs/<platform>/<design>/base/6_report.json`
  - `flow/logs/<platform>/<design>/base/5_2_route.json`
  - Override with `SURROGATE_CALIBRATE_WS_FILE` / `SURROGATE_CALIBRATE_WL_FILE`
- Core run knobs:
  - `SURROGATE_SPACE_FILE`, `SURROGATE_SAMPLES`, `SURROGATE_TOP_N`
  - Clock sweep: `SURROGATE_CLOCKS="..."` or `SURROGATE_CLOCK_MIN/MAX/STEP`
  - Optional full-ORFS validation: `SURROGATE_VALIDATE=1` and `SURROGATE_VALIDATE_N=<n>`
- Outputs:
  - `flow/results/<platform>/<design>/<variant>/surrogate_autotune.json`
  - `flow/logs/<platform>/<design>/<variant>/surrogate_autotune.log`

If you see `surrogate_optimize is not available`, the OpenROAD binary you are running does not have surrogate enabled (rebased: needs `ENABLE_SURROGATE=ON` at build time and `OPENROAD_ENABLE_SURROGATE=1` at runtime).

