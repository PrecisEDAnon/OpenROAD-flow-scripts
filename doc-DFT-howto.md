# DFT / Scan in ORFS — How To Run (All Modes)

This branch integrates OpenROAD DFT scan insertion into the ORFS flow.

What you get:
- `scan_replace` (functional flops → scan flops)
- scan stitching (`execute_dft_plan`) with multiple ordering modes
- optional “trial route → stitch → incremental route” for routing-aware ordering
- validation + reporting + visualization utilities

## Prerequisites

- `tools/OpenROAD` is pinned to `PrecisEDAnon/OpenROAD` (`OpenROAD-clean-DFT`) at `2fadc72cd524c1eec3c5c5c7a1eeede739b0a805`.
- Build tools (if needed): `./build_openroad.sh --local`
  - ORFS defaults `OPENROAD_EXE` to `tools/install/OpenROAD/bin/openroad`.

## 0) Baseline (No DFT)

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=baseline_no_dft finish`

## 1) Enable DFT (Default Mode)

Recommended:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft DFT_ENABLE=1 finish`

This auto-wires hook scripts into the existing ORFS hook points:

- `POST_FLOORPLAN_TCL=flow/scripts/dft_scan_post_floorplan.tcl`
  - applies `set_dft_config ...` and runs `scan_replace`
  - creates scan ports (or uses instance/pin endpoints if configured)
- `PRE_GLOBAL_ROUTE_TCL=flow/scripts/dft_scan_pre_global_route.tcl`
  - optionally places scan ports near chain endpoints
  - stitches scan chains (unless `DFT_DEFER_STITCH=1`)

Defaults are deliberately “stable for comparisons”:
- single chain unless you set multi-chain knobs (below)
- OpenROAD internal ordering unless you set solver/order knobs (below)

## 2) Multi-Chain Configuration

Pick one of these:

- Exact chain count: `DFT_CHAIN_COUNT=<N>`
- Cap bits per chain: `DFT_MAX_CHAIN_LENGTH=<bits>` (alias: `DFT_MAX_LENGTH`)
- Cap chains (upper bound): `DFT_MAX_CHAINS=<N>`

Examples:

- 4 chains:
  - `... DFT_ENABLE=1 DFT_CHAIN_COUNT=4 finish`
- Cap length to 500 bits/chain (chain count inferred):
  - `... DFT_ENABLE=1 DFT_MAX_CHAIN_LENGTH=500 finish`

Validation tip: multi-chain designs should be checked with `--auto-chains` (see below).

## 3) Ordering Modes (How Cells Are Sequenced Within Each Chain)

### 3a) OpenROAD internal (default)

- `DFT_SCAN_SOLVER=openroad` (default)
- `DFT_SCAN_ORDER_METRIC=PLACEMENT` (default in OpenROAD DFT)
- `DFT_SCAN_ORDER_SOLVER=HEURISTIC` (default in OpenROAD DFT)

Optional OpenROAD knobs:
- `DFT_SCAN_ORDER_METRIC={PLACEMENT|PIN_TO_NET}`
- `DFT_SCAN_ORDER_SOLVER={HEURISTIC|SCANOPT|MIN_FEEDTHROUGH}`
- `DFT_SCANOPT_ROUNDS=<int>` / `DFT_SCANOPT_SEED=<int>`
- `DFT_VERTICAL_WEIGHT=<float>` (vertical penalty vs horizontal)
- `DFT_TIMING_SETUP_WEIGHT=<float>` / `DFT_TIMING_HOLD_WEIGHT=<float>` / `DFT_TIMING_CRITICAL_SLACK=<float>`
- `DFT_SCAN_ORDER_CONSTRAINTS_FILE=/path/to/constraints.txt`

### 3b) Routing-aware ordering (“trial route then stitch”)

Use trial global-route guides for ordering (`PIN_TO_NET`):

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_route_aware DFT_ENABLE=1 DFT_ROUTE_AWARE=1 finish`

What `DFT_ROUTE_AWARE=1` does:
- wires `POST_GLOBAL_ROUTE_TCL=flow/scripts/dft_scan_post_global_route.tcl`
- sets `DFT_DEFER_STITCH=1` so stitching happens after the first GRT pass
- defaults `DFT_SCAN_ORDER_METRIC=PIN_TO_NET`

### 3c) OpenROAD ScanOpt (internal)

Enable the built-in ScanOpt-style iterated local search:

- `... DFT_ENABLE=1 DFT_SCAN_SOLVER=openroad DFT_SCAN_ORDER_SOLVER=SCANOPT finish`

Optional knobs:
- `DFT_SCANOPT_ROUNDS=<int>` (more rounds = slower, usually better)
- `DFT_SCANOPT_SEED=<int>` (deterministic runs)

### 3d) Preferred-direction / min-feedthrough (internal)

Row-sweep DP heuristic for minimizing feedthroughs (penalize vertical movement):

- `... DFT_ENABLE=1 DFT_SCAN_SOLVER=openroad DFT_SCAN_ORDER_SOLVER=MIN_FEEDTHROUGH DFT_VERTICAL_WEIGHT=10.0 finish`

Notes:
- This mode is intended for “prefer horizontal wiring” regimes; tune with `DFT_VERTICAL_WEIGHT`.
- Timing-aware weights are ignored in this mode.

### 3e) Timing-aware (internal)

Heuristic extension that penalizes stitching off timing-critical scan-out nets (setup/hold slack proxy):

- `... DFT_ENABLE=1 DFT_SCAN_SOLVER=openroad DFT_TIMING_SETUP_WEIGHT=1.0 DFT_TIMING_HOLD_WEIGHT=1.0 DFT_TIMING_CRITICAL_SLACK=0.0 finish`

Notes:
- `DFT_TIMING_CRITICAL_SLACK=0.0` means “only negative slack is critical”.
- With timing weights enabled, edge costs become asymmetric; OpenROAD switches to a directed heuristic.

### 3f) ScanOpt-next (bundled reference solver)

Runs a dependency-light external solver to re-order cells by placement, then stitches from that explicit order:

- `... DFT_ENABLE=1 DFT_SCAN_SOLVER=scanopt_next finish`

Optional knobs:
- `DFT_SCAN_SOLVER_BIN=/path/to/solver` (use an external binary instead of the bundled script)
- `DFT_SCAN_SOLVER_SEED=<int>`
- `DFT_SCAN_SOLVER_MAX_2OPT_ITERS=<int>`
- `DFT_SCAN_SOLVER_DISABLE_2OPT=1`

### 3g) Explicit order file

Force an exact per-chain order:

- `... DFT_ENABLE=1 DFT_SCAN_SOLVER=order_file DFT_SCAN_ORDER_FILE=/path/to/order.txt finish`

Format:
- One chain per line: `chain_name inst0 inst1 inst2 ...`
- Single-chain shorthand is allowed: `inst0 inst1 inst2 ...`

## 4) Reuse Existing Scan Ports / Custom Naming

OpenROAD DFT endpoints are configured via name patterns:

- `DFT_SCAN_ENABLE_NAME_PATTERN=<name-or-inst/pin>`
- `DFT_SCAN_IN_NAME_PATTERN=<name-or-inst/pin-with-{}>`
- `DFT_SCAN_OUT_NAME_PATTERN=<name-or-inst/pin-with-{}>`

Notes:
- `{}` is replaced with the chain ordinal (`0`, `1`, ...).
- If the string contains an unescaped `/`, OpenROAD treats it as `instance/pin` instead of a top-level port.

Example (top-level ports `se`, `si_0`, `so_0`, ...):
- `... DFT_ENABLE=1 DFT_SCAN_ENABLE_NAME_PATTERN=se DFT_SCAN_IN_NAME_PATTERN=si_{} DFT_SCAN_OUT_NAME_PATTERN=so_{} finish`

## 5) Polarity / Mixed Clock Domains (Avoiding “Broken” Chains)

### 5a) Mixed clock/edge chains (lockup not inserted)

OpenROAD DFT does not insert lockup elements. If `DFT_CLOCK_MIXING=clock_mix` produces mixed-clock/edge chains, ORFS handles it with:

- `DFT_LOCKUP_POLICY=auto` (default): detect mixed chains and fall back to `DFT_CLOCK_MIXING=no_mix`
- Alternatives:
  - `DFT_LOCKUP_POLICY=warn` / `error` / `off`

### 5b) Active-low scan enable

Functional-mode STA/power is done by disabling scan enable with `set_case_analysis`. Default is `0`.

- If your library uses active-low scan enable, set `DFT_SCAN_ENABLE_DISABLED_VALUE=1`.

## 6) Sanity Checks, Reports, and Metrics

Plan/report (inside OpenROAD):
- `report_dft_plan -verbose`

Validate from a finished netlist:
- `python3 flow/util/scan_chain_validate.py --verilog flow/results/<platform>/<design>/<variant>/6_final.v`
- Multi-chain: add `--auto-chains`.

Validate from an ODB (writes a temporary netlist via OpenROAD):
- `python3 flow/util/scan_chain_validate.py --odb flow/results/<platform>/<design>/<variant>/3_5_place_dp.odb --openroad $OPENROAD_EXE --liberty <lib> --sdc flow/results/<platform>/<design>/<variant>/3_place.sdc --ensure-ports --scan-replace --execute-dft-plan`

Scan-only routed wirelength (paper-style proxy):
- `flow/scripts/final_report.tcl` calls `flow/scripts/dft_scan_wirelength.tcl` by default.
- Reports land in `flow/reports/<platform>/<design>/<variant>/`:
  - `dft_scan_wirelength_finish.rpt` (dedicated SCAN nets)
  - `dft_scan_link_wirelength_finish.rpt` (scan-link nets inferred from scan-in connectivity)
- Disable with `DFT_REPORT_SCAN_WIRELENGTH=0`.

## 7) Visualize Chains / Debug “Huge Hops”

Generate a PNG overlay of the scan chain polyline between placed scan cells (red = longest hops):

- `python3 flow/util/scan_chain_plot.py --verilog flow/results/<platform>/<design>/<variant>/6_final.v --def flow/results/<platform>/<design>/<variant>/6_final.def --out flow/reports/<platform>/<design>/<variant>/dft_scan_chain.png`

Why hops can be huge (especially with `PIN_TO_NET`):
- `PIN_TO_NET` minimizes incremental distance to existing routed net geometry, not Manhattan distance between flop centers.
- A long/spanning source net can make a physically distant next flop look “cheap”.
- Use the scan wirelength reports above to judge routed impact; the center-to-center polyline is only a visualization.

## 8) Example Images (Ibex / Nangate45)

These are reference PNGs produced by `flow/util/scan_chain_plot.py` for a single design across modes:

- `docs/images/dft/scan_chain_ibex_mode_openroad.png`
- `docs/images/dft/scan_chain_ibex_mode_or_scanopt.png`
- `docs/images/dft/scan_chain_ibex_mode_min_feedthrough.png`
- `docs/images/dft/scan_chain_ibex_mode_pin_to_net.png`
- `docs/images/dft/scan_chain_ibex_mode_timing_aware.png`
- `docs/images/dft/scan_chain_ibex_mode_scanopt_next.png`
