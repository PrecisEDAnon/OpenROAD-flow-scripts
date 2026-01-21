# ORFS/OpenROAD toggle handoff (DPL + GPL/RSZ)

This doc is meant to be “handoff tier”: baseline pins, toggles, exact commands,
and the most recent sweep directories that demonstrate “no regressions relative
to `rules-base.json`”.

## Where the JSON gate lives

- Rules: `flow/designs/<platform>/<design>/rules-base.json`
- Check: `make -C flow metadata` (runs `flow/util/genMetrics.py` then `flow/util/checkMetadata.py`)

## Baseline provenance (what the JSONs were built against)

The `rules-base.json` guard values in this checkout are from ORFS upstream
commit `570c868a7`, which pins:

- OpenROAD: `b42159587e` (`tools/OpenROAD`)
- yosys: `77005b69a` (`tools/yosys`)
- yosys-slang: `64b44616` (`tools/yosys-slang`)

## Local “don’t get burned” notes (executables)

This checkout has multiple OpenROAD builds and a stale `tools/install/`.
Always override executables on the command line:

- Baseline yosys (used for all sweeps): `YOSYS_EXE=$PWD/tools/install-orfs570c86/yosys/bin/yosys`
- Use an **absolute** `WORK_HOME` when doing `make -C flow ...` (relative paths
  are interpreted from `flow/`).

## Toggles

### DPL: `ENABLE_EXTRA_DPL` (only meaningful when `ENABLE_DPO=1`)

- OpenROAD branch (pushed): `anon-origin/for-export-toggle` @ `e0f3bf5156`
  - Fix included: extra-DPL max displacement units (DBU vs sites).
- ORFS branch (pushed): `anon-origin/orfs-dpl-toggle-rebased` @ `4768f8584`
- Toggle behavior:
  - `ENABLE_EXTRA_DPL=1` only takes effect when `ENABLE_DPO=1`.
  - `ENABLE_EXTRA_DPL=0` (default) uses vanilla DPO behavior.
- Recommended OpenROAD exe in this workspace: `OPENROAD_EXE=$PWD/tools/OpenROAD-dpl-toggle/build-dpl/bin/openroad`

### GPL/RSZ: `ORFS_ENABLE_NEW_OPENROAD` (unrelated to DPL)

- OpenROAD commit: `09e8701e9d` (installed in `tools/install-openroad-09e8701e9d/`)
- ORFS branch: `orfs-gpl-rsz-toggle-rebased` (this branch)
- Toggle behavior when `ORFS_ENABLE_NEW_OPENROAD=1`:
  - GPL: passes `global_placement -timing_driven_use_new_net_weights`
    - Flow defaults `GPL_WEIGHT_USE_ZERO_REF=0` unless user explicitly sets it,
      to avoid large regressions on some designs.
  - RSZ: passes `-equiv_filter_fallback` to `repair_design`/`repair_timing`.
  - `repair_timing -setup_tns_checkpoint` is **not** enabled by default (it
    regressed `jpeg` at globalroute vs JSON thresholds).
- OpenROAD exe: `OPENROAD_EXE=$PWD/tools/install-openroad-09e8701e9d/OpenROAD/bin/openroad`

## Repro commands

Single design (example: `nangate45/swerv`):

```bash
OR_EXE=$PWD/tools/install-openroad-09e8701e9d/OpenROAD/bin/openroad
YOSYS_EXE=$PWD/tools/install-orfs570c86/yosys/bin/yosys
WORK=$PWD/work-run-$(date +%Y%m%d-%H%M%S)-nangate45-swerv

make -C flow DESIGN_CONFIG=./designs/nangate45/swerv/config.mk \
  WORK_HOME=$WORK OPENROAD_EXE=$OR_EXE YOSYS_EXE=$YOSYS_EXE \
  ORFS_ENABLE_NEW_OPENROAD=1 NUM_CORES=16 metadata
```

## Latest sweeps (all PASS vs `rules-base.json`)

### DPL (`ENABLE_EXTRA_DPL=1`)

- Nangate45: `work-sweep-dpl-extra-final2-20260120-175621-nangate45-{aes,jpeg,ibex,gcd,swerv}`
- Sky130HD: `work-sweep-dpl-extra-final2-20260120-183338-sky130hd-{aes,jpeg,ibex,gcd}` (`EQUIVALENCE_CHECK=0`)

### GPL/RSZ (`ORFS_ENABLE_NEW_OPENROAD=0/1`)

- Nangate45: `work-sweep-gplrsz-20260120-230757-nangate45-*-toggle0` and `work-sweep-gplrsz-20260120-230757-nangate45-*-toggle1`
- Sky130HD: `work-sweep-gplrsz-20260121-002542-sky130hd-*-toggle0` and `work-sweep-gplrsz-20260121-002542-sky130hd-*-toggle1`

## Optional knobs

To try the “0-slack reference” net weighting again under GPL opt-in:

```bash
export GPL_WEIGHT_USE_ZERO_REF=1
```

(The flow defaults it to `0` only when `ORFS_ENABLE_NEW_OPENROAD=1` and the
variable is otherwise unset.)
