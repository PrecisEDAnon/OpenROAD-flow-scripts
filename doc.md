# Surrogate autotuner (OpenROAD + ORFS) — status / handoff (2026-01-10)

Canonical end-to-end guides (these are what we expect users to follow):

- ORFS normal branch: `surrogate_autotuner_guide.md`
- ORFS rebased branch: `flow/surrogate_autotuner_guide.md`

This `doc.md` is a “current status” handoff that ties together the 4 branches,
their pairing rules, gating, and known limitations/results.

Note: upstream `.gitignore` ignores `doc.md`, but in these surrogate branches the
file is intentionally tracked (gitignore does not apply to tracked files).

## Current branch set (precisedanon)

The deliverable is **four branches total** (two repos × normal/rebased):

Repos (GitHub):

- ORFS: `git@github.com:PrecisEDAnon/OpenROAD-flow-scripts.git`
- OpenROAD: `git@github.com:precisedanon/OpenROAD.git`

### ORFS (OpenROAD-flow-scripts)

- `orfs-surrogate-normal`
  - Base: `93c42b2e6` + **1 commit** (minimal surrogate integration + docs)
  - OpenROAD submodule pin: `tools/OpenROAD @ 0b3616e102dbb3a9d76bc8233021361a4bad20bf` (`openroad-surrogate-normal`)
  - Guide: `surrogate_autotuner_guide.md`
- `orfs-surrogate-rebased`
  - Rebased on The-OpenROAD-Project `master` (then surrogate plumbing + docs)
  - OpenROAD submodule pin: `tools/OpenROAD @ f5de6e746232f0e8fc33915efba964ac3c38fc1d` (`openroad-surrogate-rebased`)
  - Guide: `flow/surrogate_autotuner_guide.md`

### OpenROAD (tools/OpenROAD)

- `openroad-surrogate-normal`
  - Base: `7bc521f36a` + **1 commit** `0b3616e102dbb3a9d76bc8233021361a4bad20bf` (“surrogate: add autotune support”)
  - Surrogate commands are compiled in and available without extra gating.
- `openroad-surrogate-rebased`
  - Base: OpenROAD `upstream/master` + `a04f00a450` + `f5de6e746232f0e8fc33915efba964ac3c38fc1d`
  - Compile-time gate: `-D ENABLE_SURROGATE=ON` (default `OFF`)
  - Runtime gate: `OPENROAD_ENABLE_SURROGATE=1` (default `OFF`)
  - Surrogate TCL commands are only registered when both gates are enabled.

Pairing rules:

- `orfs-surrogate-normal` ↔ `openroad-surrogate-normal`
- `orfs-surrogate-rebased` ↔ `openroad-surrogate-rebased`

Quick verification (precisedanon):

- OpenROAD branch heads:
  - `openroad-surrogate-normal` → `0b3616e102dbb3a9d76bc8233021361a4bad20bf`
  - `openroad-surrogate-rebased` → `f5de6e746232f0e8fc33915efba964ac3c38fc1d`
- ORFS `tools/OpenROAD` gitlink matches the corresponding OpenROAD head on each branch (see pins above).

## Quick run (normal vs rebased)

Normal:

```bash
git checkout orfs-surrogate-normal
git submodule update --init --recursive
./build_openroad.sh --local

# Baseline logs (recommended for calibration)
make -C flow finish DESIGN_CONFIG=designs/<platform>/<design>/config.mk

# Surrogate autotune (example)
make -C flow surrogate_autotune DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_VALIDATE=1 SURROGATE_VALIDATE_N=14
```

Rebased:

```bash
git checkout orfs-surrogate-rebased
git submodule update --init --recursive
./build_openroad.sh --local --openroad-args "-D ENABLE_SURROGATE=ON"

# Surrogate targets on this branch run OpenROAD with OPENROAD_ENABLE_SURROGATE=1
make -C flow surrogate_autotune DESIGN_CONFIG=designs/<platform>/<design>/config.mk \
  SURROGATE_VALIDATE=1 SURROGATE_VALIDATE_N=14
```

## What is shipped (ORFS surface area)

New `flow/Makefile` targets:

- `make -C flow surrogate_tune` (calls OpenROAD `surrogate_optimize` on the current synthesized netlist)
- `make -C flow surrogate_tune_synthaware` (clock sweep: rewrite SDC + re-synth per clock, then tune)
- `make -C flow surrogate_autotune` (orchestrates synth-aware tuning; can optionally run `finish` validations)

Implementation entry points:

- ORFS: `flow/scripts/surrogate_tune.tcl`, `flow/scripts/surrogate_tune_synthaware.py`, `flow/scripts/surrogate_autotune.py`
- OpenROAD: `surrogate_optimize` and `surrogate_supported_features` (C++: `tools/OpenROAD/src/Surrogate.cc`)

## Supported values (objectives + knobs)

### Objectives (`SURROGATE_OBJECTIVE`)

Supported objective names (minimize):

- `effective_clock_period` (default)
- `routed_wirelength`
- `power`
- `instance_area`
- `area`

Note: empirical “expected gains” are currently only characterized on disk for
`effective_clock_period` and `routed_wirelength` at ~`600s` and `K=14` (see below).

### Knob space keys

This integration is intentionally **restricted** vs ORFS’s baseline autotuner.
Only a small fixed set of knob names is supported; unknown keys are ignored.

See the guides for the full “supported knob → ORFS variable” table and valid ranges.

## Expected gains (what users should expect)

From on-disk runs with `SURROGATE_TIME_BUDGET_S=600` and `SURROGATE_VALIDATE_N=14`
(K=14), across `{asap7,nangate45,sky130hd} × {aes,ibex,jpeg}`:

- `routed_wirelength`: median gain `3.35%` (p25 `0.96%`, p75 `7.37%`), best observed `15.78%`, worst `0%`
- `effective_clock_period`: median gain `2.99%` (p25 `1.61%`, p75 `4.71%`), best observed `11.58%`, worst `0%`

Not yet characterized at ~`600s` on disk: `power`, `instance_area`, `area`.

## Outputs / artifacts

Outputs:

- `flow/results/<platform>/<design>/<variant>/surrogate_optimize.json`
- `flow/results/<platform>/<design>/<variant>/surrogate_synthaware.json`
- `flow/results/<platform>/<design>/<variant>/surrogate_autotune.json`

Logs:

- `flow/logs/<platform>/<design>/<variant>/surrogate_tune.log`
- `flow/logs/<platform>/<design>/<variant>/surrogate_tune_synthaware.log`
- `flow/logs/<platform>/<design>/<variant>/surrogate_autotune.log`

## Known limitations / TODOs (explicit)

- Objective support beyond `{effective_clock_period, routed_wirelength}` is **not yet validated** with enough on-disk runs to quote expected gain ranges.
- The knob space is intentionally restricted; this is not a drop-in replacement for baseline ORFS autotuning.

## Disk footprint (practical note)

Full `finish` runs can generate large artifacts under `flow/results/**` (e.g. `.odb`, `.def`, `.gds`, `.spef`).
If you need to reclaim disk, the safest first pass is to keep `flow/logs/**` and remove large, reproducible outputs
under `flow/results/**` for old variants you don’t need.
