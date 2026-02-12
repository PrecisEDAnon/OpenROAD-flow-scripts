# DFT / Scan in ORFS (OpenROAD-flow-scripts) — Worklog + How To Reproduce

This is a living worklog for DFT scan insertion + scan-chain stitching/optimization in ORFS. For historical comparisons, **OpenROAD `7bc521f36a` is treated as the “baseline DFT”** (often yields 0 chains due to scan-pin recognition failures). All work here assumes a **vanilla OpenSTA** requirement (no `src/sta` parser changes required).

## Workspace snapshot (2026-02-12)

Reproducible “clean DFT” baselines (PrecisEDAnon GitHub):
- OpenROAD: `OpenROAD-clean-DFT` @ `b64941f4c9` (scan_enable buffering + strict polarity default)
- ORFS: `ORFS-clean-DFT` (pins `tools/OpenROAD` to `b64941f4c9`)
- OpenSTA: `d7cb9be1` (vanilla)

Active dev branches (PrecisEDAnon GitHub):
- OpenROAD: `OpenROAD-toggle-rebased-DFT` @ `9b94d649ad`
- ORFS: `ORFS-toggle-rebased-DFT`

Local note:
- This repo’s working tree may be dirty; for reproducible DFT behavior (especially `buffer_scan_enable`), prefer the clean baselines above.

## Goal / Scope

- Make OpenROAD’s DFT scan insertion usable in ORFS:
  - `scan_replace` converts functional flops → scan flops.
  - `execute_dft_plan` stitches scan chains using placement (wirelength-aware).
- Ensure it works with **vanilla OpenSTA** (no OpenSTA parser patches required).
- Align with the v1.0 requirements + review notes captured in `spec-random-comments.md`.
- Provide a practical way to compare:
  - **7bc521 “baseline DFT”** (broken / mostly no-op) vs
  - **fixed DFT** (actually produces scan flops + stitched chains),
  - using QoR proxies and a scan-chain “TSP-like” cost metric.

## Status (as of 2026-02-12)

- ORFS hooks support `DFT_ENABLE=1` end-to-end: `scan_replace`, scan port creation, optional scan port placement, chain stitching, and reporting.
- OpenROAD scan ordering:
  - `PLACEMENT` metric uses consistent pin-to-pin Manhattan distance (scan-out pin → next scan-in pin).
  - `SCANOPT` solver integrates UCLA ScanOptpack (`UCLApack-3-010411`), and the repo-root UCLApack sources match OpenROAD’s vendored copy used by DFT.
- For A/B comparisons, fix `DFT_SCANOPT_SEED` and increase `DFT_SCANOPT_TIME_LIMIT` to reduce run-to-run variance from tight time budgets.
- ORFS scan-chain tooling (cost + plotting + bundled external solver I/O) uses pin-level asymmetric costs (scan-out → scan-in) and includes Begin/End terms when endpoints are known.
- Scan enable fanout control is handled in OpenROAD as `buffer_scan_enable`; ORFS calls it by default via `DFT_BUFFER_SCAN_ENABLE=1` (falls back to legacy `insert_buffer` if the command is unavailable).
- `polarity_mode=strict` is the default (so mixed-edge flops are split across chains unless explicitly overridden).
- ORFS `final_report.tcl` no longer hard-requires `orfs_write_db` (falls back to `write_db`), avoiding fork regressions.
- When `DFT_SCAN_ORDER_CONSTRAINTS_FILE` is set, ORFS keeps OpenROAD’s ordering (does not apply external `scanopt_next` reorder) so groups/paths/before/fixed_edge constraints are preserved.
- Verification smoke tests completed:
  - ORFS `nangate45/gcd` runs end-to-end through `finish` with `DFT_ENABLE=1` on `ORFS-clean-DFT`.
  - “DFT-only” planning on a pre-done `sky130hd/jpeg` placement validates with 0 broken links (including a multi-chain run).
  - `dft-verifier/DFTRepro` outputs were backed up and regenerated cleanly; prior invalid pin placement issues were traced to harness pin/endpoints setup and fixed.

## Verification (quick sanity)

### 0) Confirm the OpenROAD binary you are using

In this workstream there are often multiple OpenROAD builds in play. For DFT runs, the minimal sanity check is that your OpenROAD build has Python enabled and exposes the DFT commands you expect.

Example:

```tcl
# check_dft.tcl
puts "buffer_scan_enable: [info commands buffer_scan_enable]"
puts "write_scandef: [info commands write_scandef]"
exit
```

Run with:

```bash
$OPENROAD_EXE -exit check_dft.tcl
```

### 1) ORFS end-to-end smoke test (Nangate45 `gcd`)

```bash
make -C flow DESIGN_CONFIG=./designs/nangate45/gcd/config.mk FLOW_VARIANT=dft_gcd_smoke DFT_ENABLE=1 finish
```

### 2) “DFT-only” sanity on an existing `sky130hd/jpeg` placement (do not re-run the full flow)

Single-chain planning + validation:

```bash
python3 flow/util/scan_chain_validate.py \
  --odb flow/results/sky130hd/jpeg/jpeg_real1_fresh_20260209/3_5_place_dp.odb \
  --openroad "$OPENROAD_EXE" \
  --liberty flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib \
  --sdc flow/results/sky130hd/jpeg/jpeg_real1_fresh_20260209/3_place.sdc \
  --scan-replace --execute-dft-plan --ensure-ports
```

Multi-chain planning + validation:

```bash
python3 flow/util/scan_chain_validate.py \
  --odb flow/results/sky130hd/jpeg/jpeg_real1_fresh_20260209/3_5_place_dp.odb \
  --openroad "$OPENROAD_EXE" \
  --liberty flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib \
  --sdc flow/results/sky130hd/jpeg/jpeg_real1_fresh_20260209/3_place.sdc \
  --max-chains 4 --max-length 1200 \
  --scan-replace --execute-dft-plan --ensure-ports --auto-chains
```

### 3) `dft-verifier/DFTRepro` harness (packaged DB)

Location:
- `dft-verifier/DFTRepro/` (OpenROAD Python-based harness atop a packaged `db/`).
- Note: in this ORFS checkout, `dft-verifier/` may be a local/untracked workspace folder (not part of upstream ORFS).

Backups:
- Existing prior outputs (other OpenROAD variants) are preserved under `dft-verifier/DFTRepro/backups/`.
- Most recent backup during this work: `dft-verifier/DFTRepro/backups/20260212_072356/`.

Regenerate using a specific OpenROAD build:

```bash
cd dft-verifier/DFTRepro
OPENROAD_EXE="$OPENROAD_EXE" ./run_all.sh
```

Notes:
- The harness now clamps scan ports to the die area and avoids `(0,0)` endpoint collisions (which previously caused `GRT-0080 Invalid pin placement`).
- The harness calls `buffer_scan_enable` (when available) to avoid GRT issues on very high scan_enable fanout.

## Baselines, Branches, and Key Commits (history)

### OpenROAD submodule (`tools/OpenROAD`)

- Baseline reference branch: `orfs-baseline-7bc521`
  - pinned at `7bc521f36a`
- DFT enablement milestone (vanilla OpenSTA): `orfs-dft-scan`
  - `ae904a0624` (milestone)
  - `5649f22868` (DFT enablement milestone; history)
- Older variant (kept for history): `orfs-dft-scan-with-opensta`
  - `5d3e1e243c`

- Clean DFT baseline (PrecisEDAnon): `OpenROAD-clean-DFT` @ `b64941f4c9`
- Active dev baseline (PrecisEDAnon): `OpenROAD-toggle-rebased-DFT` @ `9b94d649ad`

### OpenSTA submodule (`tools/OpenROAD/src/sta`)

- Vanilla OpenSTA used by baseline and final solution:
  - `d7cb9be1`
- A prior OpenSTA parser patch was made (not required for the final approach) and preserved:
  - branch `orfs-sta-scan-nextstate-6d62008a` at commit `6d62008a`

### ORFS top-level (this repo)

- ORFS commit `84cc6b71d`: bumps `tools/OpenROAD` gitlink to `5649f22868`, adds DFT hook scripts under `flow/scripts/`
- Additional ORFS commits on this branch:
  - document scan flow + QoR/algorithm notes
  - add scan-chain validation + visualization tooling
  - add max-chain-length support in the hook scripts

## What Was Fixed in OpenROAD DFT

### 1) Make scan-cell recognition work with vanilla OpenSTA

Problem:
- Libraries (e.g., Nangate45) tag scan pins via Liberty `nextstate_type` like `scan_in`, `scan_enable`.
- Vanilla OpenSTA does **not** reliably surface those pins as `test_scan_*` scan-signal types.
- Result: OpenROAD DFT often can’t identify scan pins → scan cells not recognized → chains not built.

Fix (final approach):
- In `tools/OpenROAD/src/dbSta/src/dbSta.cc`, added **fallback inference** by common pin names:
  - enable: `SE`, `SCE`, `SCAN_EN`, `SCAN_ENABLE`, `SCANENABLE`
  - in: `SI`, `SCD`, `SCAN_IN`, `SCANIN`
  - out: `SO`, `SCO`, `SCAN_OUT`, `SCANOUT`
- This allows DFT to identify scan pins without requiring any `tools/OpenROAD/src/sta` changes.

### 2) Fix scan stitching correctness

Problem:
- Baseline stitching logic had an iteration/pop bug in `ScanStitch.cpp` that could skip/omit links.

Fix:
- Rewrote the scan-cell linking loop to deterministically connect:
  - `scan_in_driver -> first.SI`
  - `cell[i-1].SO -> cell[i].SI` for all i
  - `last.SO -> scan_out_load`

### 3) Remove reliance on `sta::TestCell` and improve scan-out handling

Changes in the earlier “with-opensta” variant that were retained/improved:
- Stop depending on `sta::TestCell` objects.
- Use `getLibertyScanIn/Enable/Out()` helpers instead.
- Add scan-out fallback when scan-out pin metadata isn’t tagged (`Q` by default, or `QN`/`Q_N` when `prefer_qbar` is enabled).

### 4) Add/enable regression coverage

- Added DFT regressions covering scan-chain planning/stitching plus key v1.0 knobs (`exclude_shift_registers`, `prefer_qbar`, `write_scandef`).
- Verified DFT tests pass in the fixed OpenROAD build (`ctest -R '^dft\.'`).

### 5) Add a scan ordering constraints file (naming, endpoints, grouping, ordering)

User-facing goal: make `execute_dft_plan` able to satisfy “real” scan-chain constraints (named chains, Begin/End ports, grouping, ordering, exclusions).

Implemented in `tools/OpenROAD/src/dft/src/config/ScanArchitectConfig.cpp` (see `loadScanOrderConstraintsFile()`):
- Chain naming + per-chain endpoints:
  - `chain <name> [begin <x> <y>|<port|inst/pin>] [end <x> <y>|<port|inst/pin>]` (coords are **DBU**)
    - begin/end are included in the ordering objective (begin→first, last→end).
    - terminal begin/end (`<port|inst/pin>`) override the scan-in/scan-out endpoints for that chain (useful for stitching to existing scan chains in macros).
    - point begin/end (`<x> <y>`) are used as scan I/O pin locations during stitching.
  - aliases: `chain_begin <name> ...` (`chainbegin`), `chain_end <name> ...` (`chainend`)
- Grouping:
  - `group [<name>] [<priority>] <inst/group...>` (hierarchical; cycles rejected)
  - `path [<name>] [<priority>] <inst...>` (strict adjacency subpath; ≥2 instances)
  - `default_priority <int>` (0–127)
- Directed ordering:
  - `fixed_edge <from> <to>` (directed adjacency)
  - `before <a> <b>` (partial order; cycles rejected)
- Assignments:
  - `assign <chain> <inst/group...>` (errors on unknown chain or conflicting assignments)
- Exclusions:
  - exact: `exclude <inst/group...>`
  - patterns: `exclude_instance_pattern <glob...>` (alias: `exclude_name_pattern`) and `exclude_master_pattern <glob...>` (aliases: `exclude_master`, `exclude_master_patterns`) (`*`/`?` supported)

Name escaping (important):
- Instance tokens in constraints must match OpenDB instance names exactly. In DEF/ODB, bus indices are typically escaped (e.g. `foo\\[0\\]`), so a Verilog-style token like `foo[0]` will not match.
- For `inst/pin` terms, escape literal slashes as `\\/` (OpenROAD treats unescaped `/` as the inst/pin separator).

Planner sanity:
- If the constraints file defines chain names, their count must match the DFT plan’s total chain count (after domain splitting), otherwise `execute_dft_plan` errors instead of silently producing fewer chains.

### 6) Suppress “big jumps” (multi-chain QoR)

Observed issue: visually obvious long-hop edges (“jumps”), especially with multiple chains.

Fixes:
- Include Begin/EndPort terms in the ordering objective when endpoints have locations (reduces IO “stem” artifacts).
- Partitioning guardrails: after K-means clustering, also try X/Y axis sweeps and a Hilbert space-filling sweep, then pick the assignment with the smallest worst within-chain Manhattan diameter (tie-break by worst X/Y gap) to avoid geographically discontiguous chain membership that local ordering can’t fix.
- `SCANOPT` QoR focus: stronger long-edge penalty and worst-edge local moves (worst-edge 2-opt, worst-edge segment relocate, and a direction-preserving 3-opt “segment swap” inspired by `ScanOptpack-010411.tar` → `UCLApack-3-010411/ScanOpt/scanTourDZ.cxx`), plus a bounds fix to avoid crashes on open paths.
- ORFS scan port placement: when re-placing `scan_in_N`/`scan_out_N` near endpoints, try all 4 die edges (in distance order) so dense IO regions don’t force large shifts along the boundary.

### 7) Special cells + power domains (warn-only)

Per the v1.0 “mandatory” notes (“initially warn”), OpenROAD DFT now recognizes and warns about:

- Clock gate cells on scan clocks / scan-enable pins (to ensure scan clocks run during scan).
- Internal tri-state drivers (which may need explicit disable/constraints during scan).
- Scan-chain edges that cross different OpenDB `dbPowerDomain`s (voltage/switching mismatches).

### 8) Shift-register recognition (optional exclusion)

Per the v1.0 “mandatory” notes (“shift registers do not need an additional scan chain through them”), OpenROAD DFT supports:

- `set_dft_config -exclude_shift_registers 1` (ORFS: `DFT_EXCLUDE_SHIFT_REGISTERS=1`)
  - Detects simple functional shift-register structures (direct `Q→D` chains) and excludes them from `scan_replace` and scan planning.
  - Control the minimum length with `-shift_register_min_length` (ORFS: `DFT_SHIFT_REGISTER_MIN_LENGTH`; default `4`).

### 9) Export scan chains (SCANDEF) for ATPG

Per the v1.0 “mandatory” notes (“exporting chains for ATPG”), OpenROAD DFT adds:

- `write_scandef -file <path>`: writes a DEF-style `SCANCHAINS` section describing the stitched chains.
- ORFS: `DFT_WRITE_SCANDEF=1` writes `$RESULTS_DIR/6_final.scandef` (or `DFT_SCANDEF_FILE`).

Note:
- `write_scandef` relies on scan chain objects stored in OpenDB by `execute_dft_plan`. When ORFS uses a non-OpenROAD stitch solver (manual net connections), it writes + imports a temporary SCANDEF (`read_def -incremental`) to populate those OpenDB objects so export still works.

## ORFS Integration (How DFT Is Hooked Into the Flow)

Two ORFS hook scripts were added:

- `flow/scripts/dft_scan_post_floorplan.tcl`
  - Intended to be set via `POST_FLOORPLAN_TCL=...`
  - Runs:
    - `set_dft_config` (defaults to 1 chain; configurable via env vars below)
    - `scan_replace`
    - infers planned chain count from `report_dft_plan` and creates ports:
      - `scan_enable_0` (shared enable)
      - `scan_in_<N>`, `scan_out_<N>` for each planned chain
    - `set_case_analysis 0 [get_ports scan_enable_0]` (functional-mode assumption)

- `flow/scripts/dft_scan_pre_global_route.tcl`
  - Intended to be set via `PRE_GLOBAL_ROUTE_TCL=...`
  - Runs:
    - `set_dft_config ...` (must match; see env vars below)
    - places scan I/O ports near their chain endpoints (reduces multi-chain “stem” wirelength):
      - controlled by `DFT_PLACE_SCAN_PORTS` (defaults to on when multi-chain is configured; see env vars)
      - `DFT_PLACE_SCAN_ENABLE_PORT` defaults to `DFT_PLACE_SCAN_PORTS`
    - `set_case_analysis 0 ...`
    - stitch scan chains (`execute_dft_plan` or `DFT_SCAN_SOLVER={scanopt_next,order_file}`)
    - for non-`execute_dft_plan` solvers, writes + imports a temporary SCANDEF (`read_def -incremental`) to populate OpenDB scan-chain objects (enables later `write_scandef`)

Optional third hook (routing-aware ordering):

- `flow/scripts/dft_scan_post_global_route.tcl`
  - Intended to be set via `POST_GLOBAL_ROUTE_TCL=...` (runs after initial global route, before repair).
  - Use with `DFT_DEFER_STITCH=1` (so the pre-global-route hook does not stitch).
  - Enables paper-style “trial route then order” when used with `DFT_SCAN_ORDER_METRIC=PIN_TO_NET`:
    - runs `report_dft_plan`-based scan port placement using the trial global-route guides
    - runs `execute_dft_plan`
    - routes only the modified nets via `global_route -start_incremental/-end_incremental`

Notes:
- This wiring is **opt-in**:
  - recommended: set `DFT_ENABLE=1` (wires the hook scripts automatically), or
  - manually: set `POST_FLOORPLAN_TCL` and `PRE_GLOBAL_ROUTE_TCL` when you run `make -C flow ...`.
- Default scan endpoint patterns:
  - enable: `scan_enable_{}`
  - in/out: `scan_in_{}`, `scan_out_{}`
  - Override with `DFT_SCAN_ENABLE_NAME_PATTERN` / `DFT_SCAN_IN_NAME_PATTERN` / `DFT_SCAN_OUT_NAME_PATTERN` to reuse existing ports/pins.
  - Configuration env vars (optional):
  - `DFT_ENABLE` (default `0`; wires hook scripts automatically)
  - `DFT_ROUTE_AWARE` (default `0`; enables the post-global-route hook + `DFT_DEFER_STITCH=1` and defaults `DFT_SCAN_ORDER_METRIC=PIN_TO_NET`)
  - `DFT_CLOCK_MIXING` (default `no_mix`)
  - `DFT_CHAIN_COUNT` (exact chains; takes priority over `DFT_MAX_CHAINS`)
  - `DFT_MAX_CHAINS` (optional max-chains cap; when unset, OpenROAD infers chain count)
  - `DFT_MAX_CHAIN_LENGTH` / `DFT_MAX_LENGTH` (max bits per chain; when `DFT_CHAIN_COUNT` is set this becomes a per-chain cap; otherwise it can enable multiple chains via chain-count inference)
  - `DFT_SCAN_ORDER_METRIC` (optional; `PLACEMENT` or `PIN_TO_NET`; default = OpenROAD DFT default)
  - `DFT_SCAN_ORDER_SOLVER` (default `SCANOPT`; OpenROAD internal solver: `HEURISTIC`, `SCANOPT`)
  - `DFT_SCANOPT_ROUNDS` / `DFT_SCANOPT_SEED` / `DFT_SCANOPT_TIME_LIMIT` / `DFT_SCANOPT_TEMP_CONTROL` / `DFT_SCANOPT_T_DIV` (`DFT_SCANOPT_TIME_LIMIT` is a total budget that OpenROAD splits across all scan chains; temp control is optional and defaults off, with `DFT_SCANOPT_T_DIV` defaulting to `100` in OpenROAD)
  - `DFT_VERTICAL_WEIGHT` (optional; preferred-direction tuning for scan ordering)
  - `DFT_MAX_IMBALANCE` (optional; max chain-length imbalance percent; default `2`)
  - `DFT_SCAN_ORDER_CONSTRAINTS_FILE` (optional; constraints file format is documented in OpenROAD DFT at `tools/OpenROAD/src/dft/README.md`)
  - `DFT_TIMING_SETUP_WEIGHT` / `DFT_TIMING_HOLD_WEIGHT` / `DFT_TIMING_CRITICAL_SLACK` (optional; timing-aware scan ordering penalties)
  - `DFT_EXCLUDE_SHIFT_REGISTERS` (optional; auto-exclude simple functional shift registers from scan_replace + planning)
    - `DFT_SHIFT_REGISTER_MIN_LENGTH` (default `4`)
  - `DFT_PREFER_QBAR` (optional; prefer using `QN`/`Q_N` as scan-out when scan-out ports aren’t tagged; can reduce load on functional `Q` nets at the cost of inverting the scan path)
  - `DFT_SCAN_ENABLE_NAME_PATTERN` / `DFT_SCAN_IN_NAME_PATTERN` / `DFT_SCAN_OUT_NAME_PATTERN` (optional; pass-through to OpenROAD `set_dft_config -scan_*_name_pattern`)
  - `DFT_SCAN_SOLVER` (optional; `openroad`, `scanopt_next`, or `order_file`)
    - `DFT_SCAN_ORDER_FILE` (when `DFT_SCAN_SOLVER=order_file`)
    - `scanopt_next` falls back to `openroad` for `PIN_TO_NET`
    - `DFT_SCAN_SOLVER_BIN` (optional external solver binary)
    - `DFT_SCAN_SOLVER_ARGS` (optional extra args)
    - `DFT_SCAN_SOLVER_SEED` / `DFT_SCAN_SOLVER_MAX_2OPT_ITERS` / `DFT_SCAN_SOLVER_DISABLE_2OPT` (bundled solver knobs)
  - `DFT_PLACE_SCAN_PORTS` (default `1` when `DFT_CHAIN_COUNT>1`/`DFT_MAX_CHAINS>1`/`DFT_MAX_CHAIN_LENGTH` is set; otherwise default `0`)
    - when enabled, re-places `scan_in_N`/`scan_out_N` near their chain endpoints
    - force-disable with `DFT_PLACE_SCAN_PORTS=0`
  - `DFT_PLACE_SCAN_ENABLE_PORT` (default = `DFT_PLACE_SCAN_PORTS`; also re-place the scan-enable endpoint when it is a top-level port)
  - `DFT_DONT_TOUCH_SCAN_NETS` (default `1`; marks most SCAN nets `dont_touch` to avoid QoR-driven resizer churn on scan-only nets; scan_enable tree is kept optimizable)
  - `DFT_BUFFER_SCAN_ENABLE` (default `1`; buffers/splits scan-enable net to reduce fanout before routing)
    - `DFT_SCAN_ENABLE_MAX_FANOUT` (default `64`)
    - `DFT_SCAN_ENABLE_BUFFER_CELL` (default = `MIN_BUF_CELL_AND_PORTS[0]`)
    - `DFT_SCAN_ENABLE_BUFFER_LEVELS` (default `3`)
  - `DFT_DEFER_STITCH` (default `0`; when `1`, skip `execute_dft_plan` in the pre-global-route hook so it can be run in `POST_GLOBAL_ROUTE_TCL`)
  - `DFT_LOCKUP_POLICY` (default `auto`; `auto`/`warn`/`error`/`off` for mixed-clock/edge chains in `clock_mix` mode)
  - `DFT_REPORT_SCAN_WIRELENGTH` (default `1`; emits scan wirelength report files + metrics in final)
  - `DFT_WRITE_SCANDEF` (default `0`; write `$RESULTS_DIR/6_final.scandef` via OpenROAD `write_scandef`)
    - `DFT_SCANDEF_FILE` (optional output path override)

Routing robustness:
- Even with `DFT_PLACE_SCAN_PORTS=0`, the pre-global-route hook ensures scan endpoints that are top-level ports have valid pin geometries (via `place_pin` on `IO_PLACER_H/V`) to avoid router errors like `GRT-0042`.

## Reproduction: Baseline vs Fixed DFT (QoR Proxy Comparison)

### Design used

- `nangate45/ibex` (`flow/designs/nangate45/ibex/config.mk`)

### OpenROAD executables used

- OpenROAD under test: `$(pwd)/tools/OpenROAD/build/bin/openroad` (build of the `tools/OpenROAD` submodule pinned by your ORFS checkout; use `ORFS-clean-DFT` for the clean baseline pinned to `b64941f4c9`)
- Baseline OpenROAD (historical): build OpenROAD at `7bc521f36a` (ideally in a separate clone/worktree/build dir) and point `OPENROAD_EXE` at that binary
  - Example build (separate build dir): `git -C tools/OpenROAD checkout 7bc521f36a && cmake -S tools/OpenROAD -B tools/OpenROAD/build_7bc521 && cmake --build tools/OpenROAD/build_7bc521 -j"$(nproc)"`

### Flow commands

- Baseline (no DFT):
  - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_base_20260104 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build/bin/openroad finish`
- Fixed DFT enabled:
  - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_dft_20260104 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build/bin/openroad DFT_ENABLE=1 finish`
- “DFT enabled” on baseline OpenROAD 7bc521 (expected broken / no-op scan planning):
  - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_dft_or7bc521_20260104 OPENROAD_EXE=/path/to/openroad_7bc521 DFT_ENABLE=1 finish`

### QoR snapshot (finish metrics)

From `flow/logs/nangate45/ibex/<variant>/6_report.json` and `5_2_route.json`:

- no DFT (`qor_scan_base_20260104`):
  - instance area `29091.6`
  - sequential area `10065.2`
  - total power `0.0960477`
  - setup WS `-0.0211463`
  - detailed-route WL `256015`
- “DFT enabled” but OpenROAD 7bc521 broken (`qor_scan_dft_or7bc521_20260104`):
  - instance area `29106`
  - sequential area `10065.2`
  - total power `0.0961684`
  - setup WS `-0.0240458`
  - detailed-route WL `256612`
  - `report_dft_plan` shows **0 chains** (scan cells not recognized)
- fixed DFT (`qor_scan_dft_20260104`):
  - instance area `31790.5` (+~9.3%)
  - sequential area `12702.3` (+~26.2%)
  - total power `0.0995975` (+~3.7%)
  - setup WS `-0.029858`
  - detailed-route WL `278902` (+~8.9%)
  - `report_dft_plan` shows **1 chain / 1931 scan cells**

Interpretation:
- Comparing DFT vs no-DFT: PPA generally degrades due to bigger flops + new scan nets.
- The valid “DFT QoR” story is DFT-vs-DFT (reduce overhead vs naive chain ordering / too-few chains), not DFT vs no-DFT.

### QoR snapshot (ibex, Nangate45, OpenROAD v2.0-26265-gae904a0624)

Runs:
- no DFT:
  - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_base_20260106_or26264 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build/bin/openroad finish`
- DFT 1 chain:
  - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_dft_1chain_20260106_or26264 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build/bin/openroad POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl finish`
- DFT max chain length examples:
  - 2 chains (`DFT_MAX_CHAIN_LENGTH=1000`):
    - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_dft_maxlen1000_20260106_or26264 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build/bin/openroad POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl DFT_MAX_CHAIN_LENGTH=1000 finish`
  - 10 chains (`DFT_MAX_CHAIN_LENGTH=200`):
    - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_dft_maxlen200_20260106_or26264 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build/bin/openroad POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl DFT_MAX_CHAIN_LENGTH=200 finish`

Extracted from `flow/logs/nangate45/ibex/<variant>/6_report.json` and `5_2_route.json`:

| variant | chains | setup WS | detailed-route WL |
| --- | ---: | ---: | ---: |
| `qor_scan_base_20260106_or26264` | 0 | `-0.0211463` | `256015` |
| `qor_scan_dft_1chain_20260106_or26264` | 1 | `-0.0264127` | `277653` |
| `qor_scan_dft_maxlen1000_20260106_or26264` | 2 | `-0.0247976` | `277722` |
| `qor_scan_dft_maxlen500_20260106_or26264` | 4 | `-0.0440121` | `278920` |
| `qor_scan_dft_maxlen200_20260106_or26264` | 10 | `-0.0377615` | `282104` |

Notes:
- More scan chains increases scan port count; without scan port placement, too many chains can hurt QoR due to extra scan-in/out routing to placed ports (use `DFT_PLACE_SCAN_PORTS=1`, which is now the default when multi-chain is configured).
- The scan-chain optimizer minimizes **intra-chain** wirelength (flop→flop). The remaining big lever is the **scan-in/out “stem”** routing (port→chain endpoint), which is dominated by scan port placement — especially for multi-chain.
- `DFT_MAX_CHAIN_LENGTH=1000` (2 chains) is a reasonable “first cut” on `ibex` here: WL is essentially unchanged vs 1 chain and setup slack is slightly improved.

### QoR snapshot (ibex, Nangate45, OpenROAD v2.0-27767-ga99b386f7c)

Extracted from `flow/logs/nangate45/ibex/<variant>/6_report.json` and `5_2_route.json`:

| variant | setup WS | inst area | seq area | power | detailed-route WL |
| --- | ---: | ---: | ---: | ---: | ---: |
| `qor_scan_base_opt_20260109` | `-0.00503686` | `29402.3` | `10065.2` | `0.101131` | `264615` |
| `qor_scan_dft_1chain_opt_20260109` | `0.00576672` | `32065.2` | `12702.8` | `0.103246` | `286299` |
| `qor_scan_dft_1chain_portplace_opt_20260109` | `-0.0046334` | `32032.5` | `12702.8` | `0.103085` | `285784` |

Delta (DFT vs no-DFT) for this run:
- detailed-route WL: `+8.0%` to `+8.2%`
- instance area: `+~9%` (sequential area `+~26%`)
- total power: `+~2%`
- setup WS: small / run-dependent (the scan path is disabled by `set_case_analysis` so this is functional mode)

## Scan-Chain Optimizer Algorithm (Hamiltonian Path / “TSP path” Heuristic)

This is the core of `execute_dft_plan` / `scan_opt`: given placed scan flops, produce an ordering (one or more directed paths) that heuristically minimizes scan-wirelength.

### What is optimized

Per scan chain, minimize a proxy cost (default `PLACEMENT` metric):

- `cost = Σ ManhattanDist(SO[i], SI[i+1])`
- plus endpoint terms when chain endpoints have locations:
  - `+ ManhattanDist(BeginPort, SI[first])`
  - `+ ManhattanDist(SO[last], EndPort)`

Where:
- `SI[k]` is the scan-in pin location of scan cell `k`, and `SO[k]` is the scan-out pin location.
- Pin locations are taken from OpenDB pin geometry when available (`dbITerm::getAvgXY` / `dbBTerm::getFirstPinLocation`), falling back to the placed instance location.

ORFS emits this same placement-based proxy as a report + metrics after stitching:
- `flow/reports/<platform>/<design>/<variant>/dft_scan_chain_cost_pregrt.rpt`
- `flow/reports/<platform>/<design>/<variant>/dft_scan_chain_cost_finish.rpt`
- metrics: `dft_scan_chain_cost_um`, `dft_scan_chain_max_step_um`, `dft_scan_chain_imbalance_percent`

The optimization target is therefore a **Hamiltonian path** problem (a “TSP path” variant, not a cycle).

### Code pointers (OpenROAD)

Planning entrypoints:
- `tools/OpenROAD/src/dft/src/Dft.cpp`
  - `Dft::reportDftPlan()` → `scanArchitect()`
  - `Dft::executeDftPlan()` → `scanArchitect()` + `ScanStitch::Stitch()`
  - `Dft::scanOpt()` → `scanArchitect()` + `ScanStitch::Stitch()` (re-stitch on latest placement)

Chain count inference (`chain_count` / `max_length` / `max_chains`):
- `tools/OpenROAD/src/dft/src/architect/ScanArchitect.cpp`
  - `ScanArchitect::inferChainCount()`
  - `ScanArchitect::inferChainCountFromMaxLength()`
  - `ScanArchitect::createScanChains()`

Constraint sanity:
- Hard infeasible requests (e.g., `chain_count * max_length < total_bits`, `max_chains` too small, or `max_imbalance` unsatisfiable) throw errors during planning.
- If `max_length` was inferred (not user-specified), OpenROAD may increase it to fit the largest constrained group/bundle.

Partition + per-chain ordering:
- `tools/OpenROAD/src/dft/src/architect/ScanArchitectHeuristic.cpp`
  - `ScanArchitectHeuristic::architect()`:
    - distributes scan cells over chains
    - when multiple chains are enabled and scan cells are placed, clusters scan cells using placement-aware reassignment (swap/move) to keep each chain spatially local (subject to per-chain max length)
    - additionally evaluates X/Y axis sweeps and a Hilbert sweep and picks the assignment with the smallest worst within-chain Manhattan diameter (tie-break by worst axis gap) to suppress multi-chain outliers (“big jumps”)
    - runs the per-chain optimizer with “mid” polarity ordering (falling-edge subset first, then rising-edge subset)

Per-chain ordering (TSP-path heuristic):
- `tools/OpenROAD/src/dft/src/architect/Opt.cpp`
  - `OptimizeScanWirelength()`:
    - start node: lower-leftmost cell (min `x+y`, tie-break by instance name) unless BeginPort is provided
    - `HEURISTIC`: greedy NN + farthest insertion + bounded 2-opt (rtree fallback for huge chains)
    - `SCANOPT`: iterated local search (double-bridge kicks + descent) over a full O(n²) cost matrix, including a superlinear long-edge penalty and worst-edge cleanup moves (worst-edge 2-opt, worst-edge segment relocate, direction-preserving 3-opt “segment swap”)

Physical stitching (netlist update):
- `tools/OpenROAD/src/dft/src/stitch/ScanStitch.cpp`
  - `ScanStitch::Stitch()`:
    - connects `scan_enable` to all scan flops
    - connects `scan_in` → first SI
    - connects `SO(i)` → `SI(i+1)`
    - connects last SO → `scan_out`

Location proxy + scan net sigtype:
- `tools/OpenROAD/src/dft/src/cells/OneBitScanCell.cpp` (`OneBitScanCell::getOrigin()` uses placement location)
- `tools/OpenROAD/src/dft/src/cells/ScanCell.hh` (`Connect()` sets newly created scan nets to `odb::dbSigType::SCAN`)

## “Traveling Salesman”-Style Metric (Scan Chain Cost)

To quantify “how good” a scan chain ordering is, we added:

- `flow/util/scan_chain_cost.py`

What it computes:
- Parses OpenROAD `report_dft_plan -verbose` to get the scan-cell order per chain.
- Extracts placed instance locations (DEF `PLACED` coordinates) by writing a DEF (`write_def`) and parsing the `COMPONENTS` section.
- Computes:
  - chain path length = sum Manhattan distance between consecutive scan cells in that order
  - a naive baseline = same cost for lexicographic instance order (`sorted(inst_names)`)

Note:
- The metric intentionally uses DEF `PLACED` coordinates (OpenDB `dbInst::getLocation()`), since `dbInst::getOrigin()` is orientation-dependent (e.g. MX/MY) and can skew comparisons/optimization.

### Example usage

- On a DFT-run placed DB:
  - `python3 flow/util/scan_chain_cost.py --openroad tools/OpenROAD/build/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_dft_20260104/3_5_place_dp.odb --sdc flow/results/nangate45/ibex/qor_scan_dft_20260104/3_place.sdc`
- With a simple “TSP-ish” nearest-neighbor baseline:
  - `python3 flow/util/scan_chain_cost.py --nearest-neighbor --openroad tools/OpenROAD/build/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_dft_20260104/3_5_place_dp.odb --sdc flow/results/nangate45/ibex/qor_scan_dft_20260104/3_place.sdc`
- With a max chain length (enables multiple chains):
  - `python3 flow/util/scan_chain_cost.py --max-length 1000 --openroad tools/OpenROAD/build/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_dft_1chain_20260106_or26264/4_cts.odb --sdc flow/results/nangate45/ibex/qor_scan_dft_1chain_20260106_or26264/4_cts.sdc`
- Visualize scan ordering (SVG):
  - `python3 flow/util/scan_chain_cost.py --out-svg temp-stash/scan.svg --openroad tools/OpenROAD/build/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_dft_1chain_20260106_or26264/4_cts.odb --sdc flow/results/nangate45/ibex/qor_scan_dft_1chain_20260106_or26264/4_cts.sdc`
- On a no-DFT placed DB (compute hypothetical scan cost by doing `scan_replace` in-memory, without re-placement):
  - `python3 flow/util/scan_chain_cost.py --openroad tools/OpenROAD/build/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_base_20260104/3_5_place_dp.odb --sdc flow/results/nangate45/ibex/qor_scan_base_20260104/3_place.sdc --scan-replace`

### Observed results (ibex, 1 chain)

- DFT (1 chain, `4_cts.odb`): `manhattan_um=9409.420`, naive lexicographic `90306.230` (ratio `9.597x`)
- no-DFT placed + hypothetical scan: `manhattan_um=9197.880`, naive `93778.840` (ratio `10.196x`)

Why DFT vs no-DFT chain cost is similar here:
- The scan chain cost is dominated by **where flops are placed** in the design.
- For `ibex` at this utilization, scan insertion didn’t significantly perturb placement, so the chain path length barely changes.

What *does* show up clearly:
- The new scan/control nets. Example (fixed DFT, routed DB):
  - `report_wire_length -net {scan_enable_0} -detailed_route` → `8033.45um`
  - ORFS also emits final-stage scan wirelength reports/metrics when `DFT_REPORT_SCAN_WIRELENGTH=1`:
    - `reports/<platform>/<design>/<variant>/dft_scan_wirelength_finish.rpt` (dedicated SCAN nets)
    - `reports/<platform>/<design>/<variant>/dft_scan_link_wirelength_finish.rpt` (nets feeding scan-in pins)
    - metrics: `dft_scan_dedicated_wl_{grt,drt}_um`, `dft_scan_link_wl_{grt,drt}_um`

### Optimizer benchmark (OpenROAD opt vs nearest-neighbor)

On the 9-design suite (`aes/ibex/jpeg × nangate45/asap7/sky130hd`), using `flow/util/scan_chain_cost.py --scan-replace --nearest-neighbor` on placed ODBs (so scan flops are inserted in-memory, then the chain is planned/ordered from the placement database):

| platform | design | cells | OpenROAD opt (um) | NN (um) | opt/NN |
| --- | --- | ---: | ---: | ---: | ---: |
| nangate45 | aes | 562 | 3571.680 | 4178.080 | 0.855 |
| nangate45 | ibex | 1931 | 9197.880 | 10545.640 | 0.872 |
| nangate45 | jpeg | 4390 | 17903.670 | 20815.750 | 0.860 |
| asap7 | aes | 562 | 1053.810 | 1222.344 | 0.862 |
| asap7 | ibex | 273 | 428.652 | 514.404 | 0.833 |
| asap7 | jpeg | 4325 | 5045.058 | 5709.204 | 0.884 |
| sky130hd | aes | 562 | 11050.640 | 13137.940 | 0.841 |
| sky130hd | ibex | 1931 | 21754.680 | 24411.360 | 0.891 |
| sky130hd | jpeg | 4390 | 50973.380 | 57692.340 | 0.884 |

Avg `opt/NN` = `0.865` (~`13.5%` shorter than NN).

Reproduce (single design):
- `python3 flow/util/scan_chain_cost.py --scan-replace --nearest-neighbor --openroad tools/OpenROAD/build/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/cmp9_or0db856_rp100_20251229_022425/3_5_place_dp.odb --sdc flow/results/nangate45/ibex/cmp9_or0db856_rp100_20251229_022425/3_place.sdc`

Notes:
- ASAP7 needs multiple libs; pass them all, e.g. `--liberty flow/platforms/asap7/lib/NLDM/*_TT_*`.

## Current Limitations / Known Gaps

- OpenROAD supports two ordering solvers via `set_dft_config -scan_order_solver`:
  - `HEURISTIC`: NN + farthest-insertion + bounded 2-opt (rtree fallback for huge chains)
  - `SCANOPT`: iterated local search (double-bridge kicks + relocate/swap/2-opt) with a superlinear long-edge penalty to suppress “jumps”
- `DFT_SCANOPT_TIME_LIMIT` is treated as a total budget and is split across chains to avoid runtime scaling with chain count.
- ORFS can benchmark external scan ordering solvers (e.g., OR-Tools/LKH) via `DFT_SCAN_SOLVER=scanopt_next` + `DFT_SCAN_SOLVER_BIN` (TSV in → order out). The bundled `scanopt_next` is a lightweight NumPy-only reference, not OR-Tools.
- ORFS exposes `DFT_CHAIN_COUNT` / `DFT_MAX_CHAIN_LENGTH` / `DFT_MAX_CHAINS` to tune chain count/length; beyond that, the main remaining lever for multi-chain QoR is scan port placement (scan-in/out “stems”). ORFS mitigates this by re-placing `scan_in_N`/`scan_out_N` near their chain endpoints (auto-enabled for multi-chain; override with `DFT_PLACE_SCAN_PORTS=0`).
- Some prebuilt `*.odb` files cannot be loaded due to OpenDB schema mismatches (e.g. “schema 0.124 > 0.122”). Use schema-compatible ODBs, or rebuild OpenROAD to match.
- If scan ports do not have a valid pin location, Begin/End endpoint costs are ignored and plots may omit the dashed I/O edges; ORFS `dft_scan_*` hooks mitigate this via `place_pin`.
- In `DFT_CLOCK_MIXING=clock_mix` mode, OpenROAD DFT requires lockup insertion for domain crossings; lockup cells/pins must be configured (e.g., `DFT_LOCKUP_CELL_RISING`/`DFT_LOCKUP_CLOCK_PIN_RISING`). ORFS defaults `DFT_LOCKUP_POLICY=auto` to fall back to `DFT_CLOCK_MIXING=no_mix` when mixed-clock/edge chains are detected.
  - A constraints file can also force polarity partitioning within a chain; mixed falling/rising within one chain is a hard constraint (see `DFT_SCAN_ORDER_CONSTRAINTS_FILE`).

## Scan-Chain Integrity Validation (Does it Actually Shift?)

QoR deltas and plan reports are necessary but not sufficient; we also want a basic structural check that scan stitching is structurally correct:
- each chain is one continuous path from `scan_in_<N>` to `scan_out_<N>`
- every scan flop appears exactly once across all chains

- `flow/util/scan_chain_validate.py` validates scan stitching from a gate-level netlist (or from an ODB by writing a temporary netlist via OpenROAD).
- It treats `assign` + inserted `BUF*/CLKBUF*` as transparent, so post-P&R buffering doesn’t cause false failures.

Example usage:

- Validate a finished netlist:
  - `python3 flow/util/scan_chain_validate.py --verilog flow/results/nangate45/ibex/qor_scan_dft_20260104/6_final.v`
- Validate multi-chain scan stitching (auto-detect `scan_in_N`/`scan_out_N` ports):
  - `python3 flow/util/scan_chain_validate.py --auto-chains --verilog flow/results/nangate45/ibex/qor_scan_dft_maxlen200_20260106_or26264/6_final.v`
- Validate from an ODB (writes a temp netlist first):
  - `python3 flow/util/scan_chain_validate.py --odb flow/results/nangate45/ibex/qor_scan_dft_20260104/6_final.odb --openroad tools/OpenROAD/build/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --sdc flow/results/nangate45/ibex/qor_scan_dft_20260104/6_final.sdc --ensure-ports`

## Scan-Chain Plotting (PNG/SVG)

- `flow/util/scan_chain_plot.py` renders stitched scan chains from `DEF + Verilog`.
- If multiple scan chains are detected, it defaults to plotting all chains (combined) unless you explicitly select a single chain with `--scan-in/--scan-out`.
- Multi-chain: `--auto-chains` writes one plot per chain as `<out>_chain_N.png` (or to a directory).
- By default it highlights the top ~2% longest segments (red) as “jump” indicators; control with `--highlight-top-k`.

Example:
- `python3 flow/util/scan_chain_plot.py --auto-chains --verilog flow/results/nangate45/ibex/qor_scan_dft_20260104/6_final.v --def flow/results/nangate45/ibex/qor_scan_dft_20260104/6_final.def --out dft_scan.png`

Alternative plotter (color per chain; dashed black I/O edges):
- `python3 highlighter.py --def flow/results/nangate45/ibex/qor_scan_dft_20260104/6_final.def --verilog flow/results/nangate45/ibex/qor_scan_dft_20260104/6_final.v --max-chain-count 4 --output-plot new_highlighter/ibex/ibex_dft.png`
- Flattened copies from bulk runs are in `highlighter_flattened/` (unique filenames to avoid overwrites).

Note on “dashed black” edges:
- These are scan I/O “stems”: `scan_in_N` (DEF pin location) → first scan cell, and last scan cell → `scan_out_N`.
- They are not intra-chain edges chosen by the ordering heuristic; if they look long, it usually means scan ports are far from chain endpoints (enable `DFT_PLACE_SCAN_PORTS=1`), or hide them with `python3 flow/util/scan_chain_plot.py --no-io-edges ...`.

## Preplaced-DB Regression (No Big Jumps)

For a placed `*.odb` + `*.sdc` where scan flops already exist, `flow/util/dft_preplaced_regress.py` runs `execute_dft_plan`, validates stitching, emits plots, and prints a table of worst-edge metrics.

Example (ibex, Nangate45):
- `python3 flow/util/dft_preplaced_regress.py --openroad tools/OpenROAD/build/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_dft_maxlen200_20260106/3_place.odb --sdc flow/results/nangate45/ibex/qor_scan_dft_maxlen200_20260106/3_place.sdc --scan-order-metric PIN_TO_NET --scan-order-solver SCANOPT --scanopt-rounds 500000 --scanopt-time-limit 600 --chain-counts 4 --max-imbalances 30 --out-prefix dft_artifacts/preplaced_runs/ibex_k4_sweepselect_t600/preplaced_ibex_p2n --out-json dft_artifacts/preplaced_runs/ibex_k4_sweepselect_t600/summary.json`

Regression results (2026-02-07 snapshot; OpenROAD `9d5965b56818` + local patches; `PIN_TO_NET` + `SCANOPT`; `scanopt_time_limit=600` total budget):

| design | chains | max_imbalance | max_step (um) | p99_step (um) | run |
| --- | ---: | ---: | ---: | ---: | --- |
| `ibex` | 4 | 30 | `18.57` | `10.64` | `dft_artifacts/preplaced_runs/ibex_k4_sweepselect_t600/` |
| `jpeg` | 4 | 30 | `28.89` | `15.52` | `dft_artifacts/preplaced_runs/sweepselect_smoke/jpeg/` |
| `gcd` | 4 | 30 | `23.39` | `23.39` | `dft_artifacts/preplaced_runs/sweepselect_smoke/gcd/` |

Sanity comparison (ibex, before multi-chain outlier suppression): `dft_artifacts/preplaced_runs/ibex_k4_600_pen4/` had `max_step_um=38.45`.

`ibex` stress-case (many chains): `k=22`, `max_imbalance=30`, `PIN_TO_NET` + `SCANOPT`, `scanopt_time_limit=15` total budget.

Repro:
- `python3 flow/util/dft_preplaced_regress.py --openroad tools/OpenROAD/build/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_dft_maxlen200_20260106/4_1_cts.odb --sdc flow/results/nangate45/ibex/qor_scan_dft_maxlen200_20260106/4_cts.sdc --scan-order-metric PIN_TO_NET --scan-order-solver SCANOPT --scanopt-time-limit 15 --chain-counts 22 --max-imbalances 30 --io-placer-h metal5 --io-placer-v metal6 --out-prefix dft_artifacts/preplaced_runs/ibex_k22_hilbert_20260207/preplaced_ibex_p2n`

Results:
- Baseline partitioning (kmeans/sweep-gap selection): `max_step_um=80.55`, `p99_step_um=35.29`
- With Hilbert sweep partition selection (worst-diameter objective): `max_step_um=42.06`, `p99_step_um=10.92`

Plots (I/O stems removed):
- baseline: `dft_artifacts/preplaced_runs/ibex_k22_hilbert_20260207/ibex_k22_kmeans_noio.png`
- hilbert: `dft_artifacts/preplaced_runs/ibex_k22_hilbert_20260207/ibex_k22_hilbert_noio.png`

## QoR + Scan Summary (Existing Runs)

`flow/util/dft_qor_summary.py` summarizes `TNS/WNS` from `6_report.json`, route wirelength from `5_2_route.json`, and scan jump metrics from `6_final.{def,v}` for a set of variants.

Example:
- `python3 flow/util/dft_qor_summary.py --platform nangate45 --design ibex --variants qor_scan_base_20260104 qor_scan_dft_20260104`
