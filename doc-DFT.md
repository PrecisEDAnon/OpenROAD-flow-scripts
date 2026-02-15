# DFT / Scan in ORFS (OpenROAD-flow-scripts) — Implementation Notes

This doc summarizes DFT scan insertion + scan-chain planning/stitching in ORFS (clean DFT branches). For historical context, **OpenROAD `7bc521f36a` is treated as the “baseline DFT”** (often yields 0 chains due to scan-pin recognition failures). All work here assumes a **vanilla OpenSTA** requirement (no `src/sta` parser changes required).

## Workspace snapshot (2026-02-15)

Active (no-toggle) branches (PrecisEDAnon GitHub):
- OpenROAD: `OpenROAD-clean-DFT` @ `6ab5afc529`
- ORFS: `ORFS-clean-DFT` (pins `tools/OpenROAD` to `6ab5afc529`)
- OpenSTA: `d7cb9be1` (vanilla)

Toggle variants (kept for comparison):
- OpenROAD: `OpenROAD-toggle-rebased-DFT` @ `caf412756f`
- ORFS: `ORFS-toggle-rebased-DFT` (pins `tools/OpenROAD` to `caf412756f`)

## Goal / Scope

- Make OpenROAD’s DFT scan insertion usable in ORFS:
  - `scan_replace` converts functional flops → scan flops.
  - `execute_dft_plan` stitches scan chains using placement (wirelength-aware).
- Ensure it works with **vanilla OpenSTA** (no OpenSTA parser patches required).
- Align with the v1.0 requirements captured in `dft-spec.md`.

## Status (as of 2026-02-15)

- ORFS hooks support `DFT_ENABLE=1` end-to-end: `scan_replace`, scan port creation, optional scan port placement, chain stitching, and reporting.
- OpenROAD scan ordering:
  - Metrics:
    - `PLACEMENT`: pin-based cost using SI/SO locations (scan-out → scan-in), with a superlinear long-edge penalty to suppress “jumps” and a blockage detour penalty (`DFT_BLOCKAGE_WEIGHT`, default `1.0`).
    - `PIN_TO_NET`: routing-aware pin-to-net distance to scan-out net guides/routes, plus a placement tie-break, the same long-edge penalty, and the same blockage detour penalty.
  - Solvers:
    - `HEURISTIC`: greedy NN + farthest insertion + bounded 2-opt (rtree fallback for huge chains).
    - `SCANOPT`: in-tree ScanOpt-style iterated local search (double-bridge kicks + descent over an O(n²) cost matrix).
    - `UCLA_SCANOPT`: UCLA ScanOptpack reference solver (vendored), but only works for `PLACEMENT` with fixed begin/end and no constraints (else falls back to `SCANOPT`).
- For A/B comparisons, fix `DFT_SCANOPT_SEED` and increase `DFT_SCANOPT_TIME_LIMIT` to reduce run-to-run variance from tight time budgets.
- ORFS scan-chain tooling uses pin-level asymmetric costs (scan-out → scan-in) via `report_dft_plan_pins -verbose`.
- Scan enable fanout control is handled in OpenROAD as `buffer_scan_enable`; ORFS calls it by default via `DFT_BUFFER_SCAN_ENABLE=1` (falls back to legacy `insert_buffer` if the command is unavailable).
- `polarity_mode=strict` is the default (so mixed-edge flops are split across chains unless explicitly overridden).
- ORFS `final_report.tcl` no longer hard-requires `orfs_write_db` (falls back to `write_db`), avoiding fork regressions.
- When `DFT_SCAN_ORDER_CONSTRAINTS_FILE` is set, ORFS keeps OpenROAD’s ordering (does not apply external `scanopt_next` reorder) so groups/paths/before/fixed_edge constraints are preserved.
- For how to run and validate, see `doc-DFT-howto.md` (keeps commands current).

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
- `SCANOPT` QoR focus: stronger long-edge penalty and worst-edge local moves (worst-edge 2-opt, worst-edge segment relocate, and a direction-preserving 3-opt “segment swap” inspired by UCLApack’s `tools/OpenROAD/src/dft/third_party/UCLApack-3-010411/ScanOpt/scanTourDZ.cxx`), plus a bounds fix to avoid crashes on open paths.
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
- Knobs:
  - Concise set (team-facing): see `doc.md`.
  - Exhaustive list + defaults: see `flow/scripts/variables.yaml` and `docs/user/FlowVariables.md`.

Routing robustness:
- Even with `DFT_PLACE_SCAN_PORTS=0`, the pre-global-route hook ensures scan endpoints that are top-level ports have valid pin geometries (via `place_pin` on `IO_PLACER_H/V`) to avoid router errors like `GRT-0042`.

## Scan-Chain Optimizer Algorithm (Hamiltonian Path / “TSP path” Heuristic)

This is the core of `execute_dft_plan` / `scan_opt`: given placed scan flops, produce an ordering (one or more directed paths) that heuristically minimizes scan-wirelength.

### What is optimized

Per scan chain, minimize a proxy cost (default `PLACEMENT` metric):

- `cost = Σ edge_cost(i, i+1)` where `edge_cost` is based on pin-to-pin Manhattan distance (`SO[i]` → `SI[i+1]`) with a superlinear “jump” penalty, a blockage detour penalty (`DFT_BLOCKAGE_WEIGHT`, default `1.0`), and optional timing weighting
- plus endpoint terms when chain endpoints have locations:
  - `+ point_cost(BeginPort, SI[first])`
  - `+ point_cost(SO[last], EndPort)`

Where:
- `SI[k]` is the scan-in pin location of scan cell `k`, and `SO[k]` is the scan-out pin location.
- Pin locations are taken from the pin bbox lower-left corner (`getBBox().xMin/yMin`) for both `dbITerm` and `dbBTerm`, falling back to the placed instance location when pin geometry is unavailable.

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
    - if a chain contains both falling-edge and rising-edge scan cells (i.e., `polarity_mode=mid` permits mixed polarity), sorts each subset and concatenates them as falling→rising; in `polarity_mode=strict`, hash domains are split by edge so chains are single-polarity

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

## Current Limitations / Known Gaps

- OpenROAD supports three ordering solvers via `set_dft_config -scan_order_solver`:
  - `HEURISTIC`: NN + farthest-insertion + bounded 2-opt (rtree fallback for huge chains)
  - `SCANOPT`: iterated local search (double-bridge kicks + relocate/swap/2-opt) with a superlinear long-edge penalty to suppress “jumps”
  - `UCLA_SCANOPT`: UCLA ScanOptpack reference solver (vendored), but only supports unconstrained `PLACEMENT` ordering with fixed begin/end
- `DFT_SCANOPT_TIME_LIMIT` is treated as a total budget and is split across chains to avoid runtime scaling with chain count.
- ORFS can benchmark external scan ordering solvers (e.g., OR-Tools/LKH) via `DFT_SCAN_SOLVER=scanopt_next` + `DFT_SCAN_SOLVER_BIN` (TSV in → order out). The bundled `scanopt_next` is a lightweight NumPy-only reference, not OR-Tools.
- ORFS exposes `DFT_CHAIN_COUNT` / `DFT_MAX_CHAIN_LENGTH` / `DFT_MAX_CHAINS` to tune chain count/length; beyond that, the main remaining lever for multi-chain QoR is scan port placement (scan-in/out “stems”). ORFS mitigates this by re-placing `scan_in_N`/`scan_out_N` near their chain endpoints (auto-enabled for multi-chain; override with `DFT_PLACE_SCAN_PORTS=0`).
- Some prebuilt `*.odb` files cannot be loaded due to OpenDB schema mismatches (e.g. “schema 0.124 > 0.122”). Use schema-compatible ODBs, or rebuild OpenROAD to match.
- If scan ports do not have a valid pin location, Begin/End endpoint costs are ignored; ORFS `dft_scan_*` hooks mitigate this via `place_pin`.
- In `DFT_CLOCK_MIXING=clock_mix` mode, OpenROAD DFT requires lockup insertion for domain crossings; lockup cells/pins must be configured (e.g., `DFT_LOCKUP_CELL_RISING`/`DFT_LOCKUP_CLOCK_PIN_RISING`). ORFS defaults `DFT_LOCKUP_POLICY=auto` to fall back to `DFT_CLOCK_MIXING=no_mix` when mixed-clock/edge chains are detected.
  - A constraints file can also force polarity partitioning within a chain; mixed falling/rising within one chain is a hard constraint (see `DFT_SCAN_ORDER_CONSTRAINTS_FILE`).
