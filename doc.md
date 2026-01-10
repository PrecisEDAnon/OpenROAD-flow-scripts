# ORFS engineering work log (team)

This is a shared, team-facing work log / index for ongoing workstreams in this
repo. Keep it **high-signal** and link out to dedicated docs for deep dives.

## DFT / Scan insertion (OpenROAD DFT)

Docs:
- `doc-DFT-howto.md`: quickstart (how to run ORFS with scan insertion)
- `doc-DFT.md`: design/implementation notes (knobs, algorithm, QoR deltas, validation tools)

Branches on PrecisEDAnon GitHub:
- OpenROAD:
  - [`OpenROAD-clean-DFT`](https://github.com/PrecisEDAnon/OpenROAD/tree/OpenROAD-clean-DFT) (baseline)
  - [`OpenROAD-toggle-rebased-DFT`](https://github.com/PrecisEDAnon/OpenROAD/tree/OpenROAD-toggle-rebased-DFT) (active)
- OpenROAD-flow-scripts:
  - [`ORFS-clean-DFT`](https://github.com/PrecisEDAnon/OpenROAD-flow-scripts/tree/ORFS-clean-DFT) (baseline)
  - [`ORFS-toggle-rebased-DFT`](https://github.com/PrecisEDAnon/OpenROAD-flow-scripts/tree/ORFS-toggle-rebased-DFT) (active)

Note:
- `ORFS-clean-DFT` is meant as a baseline snapshot; the knob list below reflects the active `ORFS-toggle-rebased-DFT` branch.

How to run (ORFS):
- `POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl` (runs `scan_replace`, creates scan ports)
- `PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl` (optional scan port placement + runs `execute_dft_plan`)

Key knobs (ORFS-toggle-rebased-DFT):
- `DFT_CHAIN_COUNT`: fixed number of scan chains (exact)
- `DFT_MAX_CHAIN_LENGTH`/`DFT_MAX_LENGTH`: max bits per chain (also used to infer chain count when `DFT_CHAIN_COUNT` is not set)
- `DFT_PLACE_SCAN_PORTS`: re-place `scan_in_N`/`scan_out_N` near chain endpoints; defaults on when multi-chain is configured; override with `DFT_PLACE_SCAN_PORTS=0`
- `DFT_DONT_TOUCH_SCAN_NETS`: marks SCAN nets `dont_touch` post-stitching to reduce QoR-driven resizer churn on scan-only nets

Algorithm sketch:
- Clustering/partitioning across chains: placement-aware reassignment (“swap/move”) under a per-chain max-length cap.
- Intra-chain ordering: “TSP path” heuristic (NN + farthest insertion + bounded 2‑opt).

QoR snapshot (example: `nangate45/ibex`):
- DFT vs no-DFT typically costs ~`+8%` detailed-route WL, ~`+9%` instance area (seq area ~`+26%`), ~`+2%` total power.
- Functional timing is reported with scan disabled (`set_case_analysis 0 scan_enable_0`), so WS deltas are small/run-dependent.

---

## Recover power work log (historical)

This section is a running log of what we changed and what we measured while
trying to get a consistent power reduction across:

- Platforms/PDKs: `asap7`, `sky130hd`, `nangate45`
- Designs: `aes`, `ibex`, `jpeg`
- Metric of record: `finish__power__total` from `flow/logs/<platform>/<design>/<variant>/6_report.json`

This file started as a local/untracked note; it is now kept in-repo for team
handoffs. Avoid putting secrets/credentials in it.


## Handoff / quickstart (for another team)

Goal:

- Reproduce the **baseline vs ours** comparison using identical ORFS + OpenSTA and
  identical design configs, with `RECOVER_POWER=100` in both cases.
- Baseline OpenROAD: `7bc521f36a` (v2.0-26260)
- Ours OpenROAD (recover_power rework + runtime fixes): `0db856789c` (v2.0-26264)
- OpenSTA: baseline `d7cb9be1` in both cases

What someone needs to change vs vanilla ORFS `93c42b2e68`:

- **Only** set `RECOVER_POWER=100` (either in the design `config.mk` or on the
  `make` command line). No additional Tcl changes are required for recover-power
  enablement on that ORFS snapshot.

Where the handoff “packet” lives in this tree:

- `tech-memo.md`: technical memo (EDA + code) describing baseline vs ours
  `recover_power` behavior, correctness constraints, and measured deltas under
  `RECOVER_POWER=100`.
- `table.md`: side-by-side comparison for the two installed-binary runs
  (includes internal/switching/leakage breakdown, wirelength, ECP, area/count).
- Full flow artifacts for the installed-binary A/B runs:
  - `flow/backup_install_6f9703_rp100_20251228_021115_20251228_055055/`
  - `flow/backup_install_7bc521_rp100_20251228_055901_20251228_063557/`
  - Each backup includes `meta/manifest.txt` recording ORFS head, OpenROAD hash,
    OpenSTA hash, installed exe path, and knobs used.

Minimal reproduce steps (A/B installed binary):

1) Ensure submodules are present (note: submodule URLs in `.gitmodules` are
   **relative** in this environment, e.g. `tools/OpenROAD` points at `../OpenROAD.git`;
   update/sync them if cloning elsewhere).

2) Build + install OpenROAD **(ours)**:

- `git -C tools/OpenROAD checkout 0db856789c`
- `git -C tools/OpenROAD submodule update --init --recursive`
- Optional sanity: `git -C tools/OpenROAD/src/sta rev-parse --short=10 HEAD` should be `d7cb9be1ca`
- `make -C tools/OpenROAD/build -j"$(nproc)" install`
- Verify banner: `tools/install/OpenROAD/bin/openroad` prints
  `OpenROAD v2.0-*-g0db856789c` at startup.

3) Run the 9 shipped designs (3 platforms × 3 designs), in parallel:

- Example:
  - `OPENROAD_EXE=$PWD/tools/install/OpenROAD/bin/openroad RECOVER_POWER=100 CONFIG_COMMIT=93c42b2e68 EQUIVALENCE_CHECK=0 ./run_9_shipped_configs_parallel.sh install_ours_rp100_$(date +%Y%m%d_%H%M%S)`
- Outputs land in:
  - `flow/logs/<platform>/<design>/<variant>/...`
  - Wrapper logs: `flow/logs/run_<platform>_<design>_<variant>.log`

If the `config_<commit>.mk` snapshots are missing on a fresh clone:

- Either run with a commit suffix you *do* have locally, or create the `93c42b2e68`
  snapshots from git history, e.g.:
  - `for p in asap7 sky130hd nangate45; do for d in aes ibex jpeg; do git show 93c42b2e68:flow/designs/$p/$d/config.mk > flow/designs/$p/$d/config_93c42b2e68.mk; done; done`

4) Build + install OpenROAD **(baseline)** and re-run with a new variant:

- `git -C tools/OpenROAD checkout 7bc521f36a` (or `orfs-baseline-7bc521`)
- `git -C tools/OpenROAD submodule update --init --recursive`
- Optional sanity: `git -C tools/OpenROAD/src/sta rev-parse --short=10 HEAD` should be `d7cb9be1ca`
- `make -C tools/OpenROAD/build -j"$(nproc)" install`
- Re-run: `./run_9_shipped_configs_parallel.sh install_7bc521_rp100_$(date +%Y%m%d_%H%M%S)`

Notes on installs / submodule state:

- `make install` overwrites `tools/install/OpenROAD/bin/openroad` (and `sta`). For
  an A/B, either (a) run the flow after each install as above, or (b) copy the
  binaries aside and use `OPENROAD_EXE=...` to point to the intended one.
- ORFS may pin `tools/OpenROAD` to an older gitlink; checking out other commits
  inside the submodule for baseline builds will make `git status` show the
  submodule as “modified”. Treat the OpenROAD banner in run logs as the ground
  truth.

5) Verify the correct binary + `RECOVER_POWER=100` were used:

- OpenROAD hash is in the stage logs (banner line):
  - `rg -F "OpenROAD v2.0" flow/logs/*/*/<variant>/*.log`
- Recover-power invocation is visible in the GRT stage:
  - `rg -F "repair_timing -verbose -recover_power 100" flow/logs/*/*/<variant>/5_1_grt.log`

6) Compare:

- Power breakdown keys are in `flow/logs/<platform>/<design>/<variant>/6_report.json`:
  - `finish__power__internal__total`
  - `finish__power__switching__total`
  - `finish__power__leakage__total`
  - `finish__power__total`
- Wirelength is in `flow/logs/<platform>/<design>/<variant>/5_2_route.json`:
  - `detailedroute__route__wirelength` (ignore the per-iter keys)
- ECP (as used here): `period - finish__timing__setup__ws`
  - Period is from `flow/results/<platform>/<design>/<variant>/6_1_fill.sdc` (`create_clock -period ...`)
- Instance count/area:
  - `6_report.json` has duplicate keys for `finish__design__instance__count` and
    `finish__design__instance__area` (two different rollups); use the unambiguous
    `finish__design__instance__count__stdcell` / `finish__design__instance__area__stdcell`.

## 0. Repo / environment snapshot

- ORFS repo HEAD: `f023cc896`
- “Shipped configs” snapshot commit used for design configs: `93c42b2e68`
  - Local copies exist as `flow/designs/<platform>/<design>/config_93c42b2e68.mk`
- Baseline OpenROAD for current comparisons: `7bc521f36a` (v2.0-26260)
  - Preserved as branch: `tools/OpenROAD` → `orfs-baseline-7bc521`
- Current OpenROAD under test (recover_power rework + runtime fixes): `0db856789c` (v2.0-26264)
- Historical reference point (recover_power rework without runtime fixes): `6f9703be52` (v2.0-26263)
  - Submodule: `tools/OpenROAD/` (pinned in ORFS at `6f9703be52` at time of initial measurements)
  - OpenSTA submodule: `d7cb9be1` (baseline OpenSTA; no activity-model tweak)
- Optional combined OpenROAD+OpenSTA variant (not shipped): `89d7104824`
  - Preserved as branch: `tools/OpenROAD` → `orfs-powerfix-sta`
  - Preserved OpenSTA patch branch: `tools/OpenROAD/src/sta` → `orfs-power-density-wns`

Local safety backup:

- Prebuilt binaries from the removed worktrees were copied to:
  - `tools/openroad_exe_backups_20251228_002226/`

Previous baseline used earlier in this log (superseded by `7bc521`):

- `98be0fa0be` (see §6.2)

Host constraints:

- `eqy` is not generally available on this machine → equivalence checking must
  not hard-fail runs.

---

## 1. What “RECOVER_POWER=100” actually means

In ORFS, `RECOVER_POWER` flows through to OpenROAD as:

- `repair_timing -recover_power <RECOVER_POWER>`

In OpenROAD, this is an **effort / coverage knob** for power recovery:

- It does **not** mean “100% power saving”.
- It means “consider (up to) ~100% of eligible candidates per pass” (and/or run
  the most aggressive recovery settings).

So the right interpretation is:

- `RECOVER_POWER=0` → no power-recovery actions
- `RECOVER_POWER=100` → “max effort” power recovery

---

## 2. Flow robustness: disabling EQY automatically

Problem:

- ORFS can be configured with `EQUIVALENCE_CHECK=1`, but many hosts won’t have
  `eqy` installed; runs would fail for reasons unrelated to PPA.

Fix:

- `flow/scripts/load.tcl` now disables `EQUIVALENCE_CHECK` automatically when
  `eqy` is not in `PATH`, and also skips if `eqy` fails to execute.

Patch summary:

- `flow/scripts/load.tcl`:
  - Added `maybe_disable_equivalence_check`
  - `run_equivalence_test` now checks `auto_execok eqy`, and wraps `exec eqy`
    in `catch` to avoid hard-failing the run.

---

## 3. OpenROAD code changes (tools/OpenROAD)

All changes below refer to the OpenROAD-only powerfix commit:

- `tools/OpenROAD` @ `6f9703be52`

### 3.1 Build / CMake quality-of-life

- `tools/OpenROAD/CMakeLists.txt`
  - Generate `Version.hh` into the build tree (`${CMAKE_CURRENT_BINARY_DIR}/include/ord/Version.hh`)
    rather than modifying the source tree.
- `tools/OpenROAD/src/CMakeLists.txt`
  - Add `${CMAKE_BINARY_DIR}/include` to include dirs so the generated
    `Version.hh` is found.
- `tools/OpenROAD/src/drt/CMakeLists.txt`
  - Make VTune optional: `find_package(VTune QUIET)` so missing VTune doesn’t
    fail configuration.

### 3.2 Recover power rework (rsz)

Files:

- `tools/OpenROAD/src/rsz/src/RecoverPower.hh`
- `tools/OpenROAD/src/rsz/src/RecoverPower.cc`
- `tools/OpenROAD/src/rsz/include/rsz/Resizer.hh`
- `tools/OpenROAD/src/rsz/src/Resizer.cc`
- `tools/OpenROAD/src/rsz/README.md` (documents semantics and behavior)

High-level behavior implemented:

- Multi-pass recovery loop: repeatedly
  - recompute WNS
  - collect eligible candidates
  - sort by a “power × slack headroom” score
  - attempt safe swaps until no more progress
- Candidate selection:
  - Prefer high-power instances with usable slack headroom
  - Treat clock-network drivers specially (some have no meaningful data slack)
  - Optionally allow safe non-clock buffer removal
  - Filter out unconstrained/extreme slack artifacts
- Per-instance optimization:
  1) Try safe removal of redundant non-clock buffers
  2) Try “next smaller footprint” downsizes (area reduction → dynamic power)
  3) Try VT swaps toward lower leakage (if VT-equivalent cells exist)
- Safety guards:
  - Preserve (or bound) setup/hold WNS via floors
  - Do not increase max slew/cap/fanout violation counts
  - Roll back rejected changes via the ECO journal

Notable semantic choice:

- When the design is already setup-failing (`WNS < 0`), recovery is allowed to
  trade a small additional WNS budget for power (documented as a fraction of
  min clock period, scaled by effort). This is visible in the results (some
  designs cross from setup-closed to setup-failing when power recovery is ON).

### 3.3 (Optional / not shipped) STA power default activity tweak (src/sta)

File:

- `tools/OpenROAD/src/sta/power/Power.cc` (OpenSTA commit `54f0fdbd`)

Change:

- Adjust default input activity “density” when setup timing is failing:
  - if `WNS < 0`, treat the design as effectively running slower than the
    requested period (`effective_period = period - WNS`), then set density
    from that.

Note (why we did not ship this):

- This can affect `report_power` whenever default activity is used, and therefore
  can confound `finish__power__total` comparisons when WNS changes sign/magnitude.
- We measured that the bulk of the reduction vs the baseline is already achieved
  by OpenROAD `recover_power` netlist changes alone (with baseline OpenSTA), so
  this tweak is not required.

---

## 4. ORFS UX tweaks related to recover power

- `flow/scripts/util.tcl`
  - More explicit log banner text for the recover-power phase.
- `flow/scripts/variables.yaml`
  - Clarified `RECOVER_POWER` description (effort semantics).
- `collect_metrics.py`
  - Removed a debug print and fixed file newline.

---

## 5. How runs were executed (repro)

### 5.1 Inputs / configs

- Used “shipped” config snapshots:
  - `DESIGN_CONFIG=designs/<platform>/<design>/config_93c42b2e68.mk`

### 5.2 Targets

- We ran to `6_report` to populate `6_report.json`:
  - `logs/<platform>/<design>/<variant>/6_report.log`

### 5.3 Key knobs

- `RECOVER_POWER={0,100}`
- `EQUIVALENCE_CHECK=0` (forced off; also now auto-disabled if `eqy` missing)
- `OPENROAD_EXE=...` used to select the OpenROAD binary per comparison variant

### 5.4 Wrapper logs

Each run also has a wrapper log:

- `flow/logs/run_<platform>_<design>_<variant>.log`

---

## 6. Experiments and results

All power numbers below are from:

- `finish__power__total` in `flow/logs/<platform>/<design>/<variant>/6_report.json`

### 6.1 Our patched OpenROAD (default ORFS OpenROAD) — `RECOVER_POWER: 0 → 100`

Variants:

- Base: `eval_93c42b2e68_20251226_001739_base` (`RECOVER_POWER=0`)
- Pwr:  `eval_93c42b2e68_20251226_001739_pwr` (`RECOVER_POWER=100`)

Result summary:

- Average power delta across 9: **-7.44%**
- Setup closure regressions (setup-closed → setup-failing in `finish__timing__setup__ws`):
  - `asap7/jpeg`
  - `sky130hd/aes`
  - `sky130hd/jpeg`

Per-design:

| platform | design | base power (W) | pwr power (W) | Δ% | base setup WNS | pwr setup WNS |
|---|---:|---:|---:|---:|---:|---:|
| asap7 | aes  | 0.153795 | 0.150799 | -1.948% | -19.143 | -51.448 |
| asap7 | ibex | 0.058131 | 0.046635 | -19.775% | -127.271 | -149.076 |
| asap7 | jpeg | 0.119744 | 0.117463 | -1.905% | 18.233 | -1.006 |
| sky130hd | aes  | 0.457787 | 0.437447 | -4.443% | 0.121 | -0.126 |
| sky130hd | ibex | 0.093246 | 0.074849 | -19.730% | -0.466 | -0.899 |
| sky130hd | jpeg | 0.486010 | 0.476472 | -1.963% | 0.007 | -0.263 |
| nangate45 | aes  | 0.385728 | 0.373988 | -3.044% | -0.031 | -0.056 |
| nangate45 | ibex | 0.096048 | 0.090754 | -5.511% | -0.021 | -0.093 |
| nangate45 | jpeg | 0.498596 | 0.455721 | -8.599% | -0.116 | -0.145 |

Platform averages:

- asap7: **-7.88%**
- sky130hd: **-8.71%**
- nangate45: **-5.72%**

### 6.2 Baseline OpenROAD — `RECOVER_POWER: 0 → 100`

Baseline OpenROAD snapshot used earlier in this log (superseded by `7bc521`):

- OpenROAD commit: `98be0fa0be` (“power test”)
- Preserved as branch: `tools/OpenROAD` → `orfs-baseline-98be0fa-buildfix` (commit `b899aafed5`)
- Prebuilt binary backup: `tools/openroad_exe_backups_20251228_002226/openroad_98be0fa0be`

Minimal compile-fix patch (now committed as `b899aafed5`, no intended behavior change):

- `tools/OpenROAD_baseline/src/rsz/src/RecoverPower.hh`
  - Removed unused `using sta::PathExpanded;`
- `tools/OpenROAD_baseline/src/rsz/src/RecoverPower.cc`
  - Fixed `sta_->corners()` API usage (`sta::Corners*`)

Variants:

- Base: `orbase_98be0fa0be_93c42b2e68_20251226_051506_base` (`RECOVER_POWER=0`)
- Pwr:  `orbase_98be0fa0be_93c42b2e68_20251226_051506_pwr` (`RECOVER_POWER=100`)

Note:

- The initial 9-way parallel `*_pwr` run timed out in the harness because
  `asap7/jpeg` took much longer; it was re-run serially to completion.

Result summary:

- Average power delta across 9: **-2.34%**

Per-design:

| platform | design | base power (W) | pwr power (W) | Δ% | base setup WNS | pwr setup WNS |
|---|---:|---:|---:|---:|---:|---:|
| asap7 | aes  | 0.153795 | 0.152023 | -1.152% | -19.143 | -27.104 |
| asap7 | ibex | 0.058131 | 0.057927 | -0.349% | -127.271 | -129.394 |
| asap7 | jpeg | 0.119744 | 0.119257 | -0.407% | 18.233 | 17.015 |
| sky130hd | aes  | 0.457787 | 0.411592 | -10.091% | 0.121 | 0.040 |
| sky130hd | ibex | 0.093246 | 0.093097 | -0.160% | -0.466 | -0.424 |
| sky130hd | jpeg | 0.486010 | 0.463911 | -4.547% | 0.007 | 0.016 |
| nangate45 | aes  | 0.385728 | 0.372757 | -3.363% | -0.031 | -0.030 |
| nangate45 | ibex | 0.096048 | 0.095333 | -0.744% | -0.021 | -0.046 |
| nangate45 | jpeg | 0.498596 | 0.497271 | -0.266% | -0.116 | -0.112 |

Platform averages:

- asap7: **-0.64%**
- sky130hd: **-4.93%**
- nangate45: **-1.46%**

### 6.3 Baseline `7bc521` vs combined `89d710` — `RECOVER_POWER=100` in both

Variants:

- Baseline: `or7bc521_rp100_20251227_065427`
  - run-time OpenROAD banner: `OpenROAD v2.0-26260-g7bc521f36a`
  - rerun with: `OPENROAD_EXE=tools/openroad_exe_backups_20251228_002226/openroad_7bc521f36a`
- Combined: `or89d710_rp100_20251227_065427` (includes OpenSTA activity tweak)
  - run-time OpenROAD banner: `OpenROAD v2.0-26264-g89d7104824`
  - rerun with: `OPENROAD_EXE=tools/openroad_exe_backups_20251228_002226/openroad_89d7104824`

Result summary:

- Average power delta across 9 (ours vs `7bc521`): **-7.39%**
- Setup closure regressions (setup-closed → setup-failing in `finish__timing__setup__ws`):
  - `asap7/jpeg`
  - `sky130hd/aes`
  - `sky130hd/jpeg`

Per-design:

| platform | design | 7bc521 power (W) | ours power (W) | Δ% | 7bc521 setup WNS | ours setup WNS |
|---|---:|---:|---:|---:|---:|---:|
| asap7 | aes  | 0.153740 | 0.150799 | -1.913% | -23.502 | -51.448 |
| asap7 | ibex | 0.058104 | 0.046635 | -19.738% | -124.221 | -149.076 |
| asap7 | jpeg | 0.119701 | 0.117463 | -1.870% | 17.462 | -1.006 |
| sky130hd | aes  | 0.456944 | 0.437447 | -4.267% | 0.020 | -0.126 |
| sky130hd | ibex | 0.093201 | 0.074849 | -19.690% | -0.424 | -0.899 |
| sky130hd | jpeg | 0.485904 | 0.476472 | -1.941% | 0.010 | -0.263 |
| nangate45 | aes  | 0.385294 | 0.373988 | -2.934% | -0.032 | -0.056 |
| nangate45 | ibex | 0.095977 | 0.090754 | -5.442% | -0.033 | -0.093 |
| nangate45 | jpeg | 0.499218 | 0.455721 | -8.713% | -0.115 | -0.145 |

### 6.4 “Pure OpenROAD” (no OpenSTA patch) vs baseline `7bc521` — `RECOVER_POWER=100`

This isolates the OpenROAD `recover_power` rework from any OpenSTA changes:

- OpenROAD commit: `6f9703be52` (recover_power rework)
- OpenSTA submodule: baseline `d7cb9be1` (no default-activity tweak)

Variants:

- Baseline: `or7bc521_rp100_20251227_065427`
  - rerun with: `OPENROAD_EXE=tools/openroad_exe_backups_20251228_002226/openroad_7bc521f36a`
- OpenROAD-only: `or6f9703_nosta_rp100_20251227_191654`
  - rerun with: `OPENROAD_EXE=tools/openroad_exe_backups_20251228_002226/openroad_6f9703be52`
- OpenROAD-only (installed) rerun: `install_6f9703_rp100_20251228_021115`
  - run-time OpenROAD banner: `OpenROAD v2.0-26263-g6f9703be52`
  - built/installed from `tools/OpenROAD` @ `orfs-powerfix-nosta`
  - rerun with: `OPENROAD_EXE=tools/install/OpenROAD/bin/openroad`
- Baseline (installed) rerun: `install_7bc521_rp100_20251228_055901`
  - run-time OpenROAD banner: `OpenROAD v2.0-26260-g7bc521f36a`
  - built/installed from `tools/OpenROAD` @ `orfs-baseline-7bc521`

Result summary:

- Average power delta across 9 (OpenROAD-only vs `7bc521`): **-7.35%**
  - This is the mean of per-design % deltas (each design equal weight); the
    sum-of-powers delta across all 9 is **-5.33%** (see `table.md`).
- Average difference vs combined (`89d710`): **+0.07%** (OpenROAD-only slightly higher; negligible)
- Setup closure regressions vs baseline: same 3/9
  - `asap7/jpeg`
  - `sky130hd/aes`
  - `sky130hd/jpeg`

Per-design:

| platform | design | 7bc521 power (W) | nosta power (W) | Δ% | 7bc521 setup WNS | nosta setup WNS |
|---|---:|---:|---:|---:|---:|---:|
| asap7 | aes  | 0.153740 | 0.150766 | -1.934% | -23.502 | -52.791 |
| asap7 | ibex | 0.058104 | 0.047250 | -18.679% | -124.221 | -146.473 |
| asap7 | jpeg | 0.119701 | 0.117466 | -1.867% | 17.462 | -0.310 |
| sky130hd | aes  | 0.456944 | 0.437447 | -4.267% | 0.020 | -0.126 |
| sky130hd | ibex | 0.093201 | 0.075202 | -19.311% | -0.424 | -0.940 |
| sky130hd | jpeg | 0.485904 | 0.476595 | -1.916% | 0.010 | -0.278 |
| nangate45 | aes  | 0.385294 | 0.373953 | -2.943% | -0.032 | -0.053 |
| nangate45 | ibex | 0.095977 | 0.089953 | -6.277% | -0.033 | -0.120 |
| nangate45 | jpeg | 0.499218 | 0.454395 | -8.979% | -0.115 | -0.143 |

Platform averages (OpenROAD-only vs `7bc521`):

- asap7: **-7.49%**
- sky130hd: **-8.50%**
- nangate45: **-6.07%**

### 6.5 Committed runtime-fixed `recover_power` vs baseline `7bc521` — `RECOVER_POWER=100`

This is the “current” A/B comparison for the committed runtime-fixed
implementation (OpenROAD `0db856789c`) versus baseline (OpenROAD `7bc521f36a`).
It re-runs the full 9-design suite from scratch after backing up and cleaning
the flow artifacts.

Runs / variants:

- Baseline: `cmp9_or7bc521_rp100_20251228_190831`
  - stored under: `flow/backup_preclean_0db856_20251229_022357/logs/...`
- Ours: `cmp9_or0db856_rp100_20251229_022425`
  - stored under: `flow/logs/...`

Result summary:

- Average power delta across 9 (mean of per-design % deltas): **-8.540%**
- Sum-of-powers delta across the 9 designs (aggregate watts): **-7.417%**
- Setup closure regressions vs baseline in `finish__timing__setup__ws` (sign flips): 3/9
  - `asap7/jpeg`
  - `sky130hd/aes`
  - `sky130hd/jpeg`
- DRV regression warnings from `recover_power` (`RSZ-0145`): 2/9
  - `sky130hd/jpeg` (slew violations)
  - `nangate45/ibex` (max-cap violations)

Intermediate run identity check:

- The previously “intermediate/uncertain” run `cmp9_or6f97fast_rp100_20251228_193507`
  matches `cmp9_or0db856_rp100_20251229_022425` exactly for `finish__power__total`
  across all 9 designs (0.000% deltas), so treat that intermediate table as the
  committed runtime-fixed implementation.

Per-design:

| platform | design | 7bc521 power (W) | ours power (W) | Δ% | 7bc521 setup WNS | ours setup WNS |
|---|---:|---:|---:|---:|---:|---:|
| asap7 | aes  | 0.153740 | 0.150746 | -1.947% | -23.502 | -63.189 |
| asap7 | ibex | 0.058104 | 0.047237 | -18.703% | -124.221 | -153.513 |
| asap7 | jpeg | 0.119701 | 0.117483 | -1.853% | 17.462 | -1.618 |
| sky130hd | aes  | 0.456944 | 0.437548 | -4.245% | 0.020 | -0.039 |
| sky130hd | ibex | 0.093201 | 0.075136 | -19.383% | -0.424 | -0.804 |
| sky130hd | jpeg | 0.485904 | 0.428012 | -11.914% | 0.010 | -0.298 |
| nangate45 | aes  | 0.385294 | 0.373668 | -3.017% | -0.032 | -0.055 |
| nangate45 | ibex | 0.095977 | 0.089371 | -6.883% | -0.033 | -0.132 |
| nangate45 | jpeg | 0.499218 | 0.454714 | -8.915% | -0.115 | -0.140 |

Recover-power runtime (from `5_1_grt.log`: “Took … seconds: repair_timing -verbose -recover_power 100”):

| platform | design | 7bc521 time (s) | ours time (s) |
|---|---|---:|---:|
| asap7 | aes  | 7 | 189 |
| asap7 | ibex | 26 | 421 |
| asap7 | jpeg | 41 | 519 |
| sky130hd | aes  | 10 | 147 |
| sky130hd | ibex | 20 | 419 |
| sky130hd | jpeg | 26 | 547 |
| nangate45 | aes  | N/A | 132 |
| nangate45 | ibex | 20 | 450 |
| nangate45 | jpeg | 36 | 556 |

Notes:

- The sky130hd/jpeg power delta is substantially larger than the historical
  OpenROAD-only run (`install_6f9703_rp100_20251228_021115`) and coincides with
  an `RSZ-0145` warning (DRV count increase). Treat this case as “power win with
  DRV caveat” until DRV preservation is tightened.

Backups of the full flow artifacts (logs/reports/results/objects) for the two
installed-binary A/B runs:

- Powerfix install run: `flow/backup_install_6f9703_rp100_20251228_021115_20251228_055055/`
- Baseline install run: `flow/backup_install_7bc521_rp100_20251228_055901_20251228_063557/`

---

## 7. Conclusions so far (as of this log)

1) **Recover power is a real baseline feature**, but baseline OpenROAD
   `RECOVER_POWER=100` does **not** yield ~5% consistently across this 9-design set:
   it averages **~2.3%**.

2) The current patched OpenROAD implementation produces **~8.5% average**
   reduction on this 9-design set (see §6.5: **-8.540%** mean of per-design
   deltas; **-7.417%** aggregate watts vs baseline), but it can reduce final
   setup margin at `RECOVER_POWER=100`:
   - 3/9 designs show `finish__timing__setup__ws` sign flips vs baseline.
   - 2/9 designs emit `RSZ-0145` warnings (DRV count increases).

3) Because ORFS power is sourced from OpenSTA `report_power` (and default
   activity assumptions), any changes that alter default activity (or tie
   activity to timing) can materially affect `finish__power__total`. This is
   especially important for runs where WNS changes sign or magnitude.

4) The measured power delta is dominated by netlist changes in `recover_power`
   (downsizing, non-clock buffer removal, VT swaps), not by a reporting-model
   tweak to default activity. Historical “OpenSTA activity” variants were not
   required to reproduce the bulk of the delta on this set.

5) Baseline `recover_power` is conservative and timing-driven (path-based driver
   downsizing). The rework is power-driven and global (instance power × slack
   headroom ranking) with a broader move set and multi-pass iteration.

6) On this 9-design set, the total-power reduction is dominated by **internal
   power** (≈74% of total Δ in aggregate watts, with most of the remainder in
   switching; leakage is small in absolute watts). This correlates with fewer
   buffering instances and lower stdcell area/count (see `table.md` for the
   installed-binary A/B breakdown).

---

## 8. Pointers to other local notes

- `state-dec-24.md` contains additional context about repo/config state and
  the earlier “config.mk mismatch” issue.
- `tech-memo.md` is the technical memo describing the OpenROAD `recover_power`
  rework (and why it differs from baseline).
- `table.md` contains the full per-metric baseline-vs-ours comparison for the
  two installed-binary A/B runs.
