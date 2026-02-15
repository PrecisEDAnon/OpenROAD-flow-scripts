# DFT / Scan — Quickstart (Before vs After)

This is a short “how to run” guide. For implementation details, limitations, and scan-order benchmarks, see `doc-DFT.md`.

## Before (Baseline: no DFT)

Run the flow normally:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=baseline_no_dft finish`

## After (DFT Enabled: scan flops + stitched chain)

Enable DFT (recommended):

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft DFT_ENABLE=1 finish`

This auto-wires the two ORFS DFT hook scripts:

- `POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl`
  - runs `scan_replace` (functional flops → scan flops)
  - creates scan ports: `scan_enable_0`, `scan_in_0`, `scan_out_0`
  - sets `set_case_analysis 0 [get_ports scan_enable_0]` (functional-mode timing)
- `PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl`
  - runs `execute_dft_plan` (stitches the scan chain using placement)
  - defaults to `DFT_SCAN_ORDER_SOLVER=SCANOPT` with `DFT_SCANOPT_ROUNDS=500000` and `DFT_SCANOPT_TIME_LIMIT=15` (total budget across all chains)

Note:
- If your design contains mixed clock domains and/or negedge flops, ORFS defaults `DFT_LOCKUP_POLICY=auto` and may fall back to `DFT_CLOCK_MIXING=no_mix`, which can increase the number of scan chains/ports.
  - To keep `clock_mix`, set `DFT_LOCKUP_POLICY=off` and configure lockup insertion (at minimum: `DFT_LOCKUP_CELL_RISING` + `DFT_LOCKUP_CLOCK_PIN_RISING`, and likewise `*_FALLING` if negedge scan flops exist).
- Polarity defaults to `DFT_POLARITY_MODE=strict` (no mixed polarity within a chain). To allow mixed polarity, set `DFT_POLARITY_MODE=mid` (falling-edge flops are stitched before rising-edge flops within each chain).

## Optional: Routing-aware ordering (trial route, then stitch)

If you want scan ordering to use trial global-route guides (paper-style “routing-aware” ordering), defer stitching until after the first global route:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_route_aware DFT_ENABLE=1 DFT_ROUTE_AWARE=1 finish`

## Optional: OpenROAD ScanOpt ordering (internal, iterated local search)

ORFS defaults to OpenROAD’s built-in ScanOpt-style iterated local search (higher quality than the greedy heuristic). To explicitly set it:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_or_scanopt DFT_ENABLE=1 DFT_SCAN_ORDER_SOLVER=SCANOPT finish`

Tuning knobs:
- `DFT_SCANOPT_ROUNDS` (default `500000`)
- `DFT_SCANOPT_SEED` (default `1`)
- `DFT_SCANOPT_TIME_LIMIT` (seconds; default `15`, `0` = unlimited)
  - Note: this is a total budget; OpenROAD splits it across chains.
  - For “heavier than default” runs, try `DFT_SCANOPT_TIME_LIMIT=600` (10 minutes) or higher.

## Optional: ScanOpt-next ordering (bundled solver)

To use the bundled “ScanOpt-next” reference solver (placement-based ordering) instead of OpenROAD’s built-in heuristic:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_scanopt_next DFT_ENABLE=1 DFT_SCAN_SOLVER=scanopt_next finish`

## Optional: Timing-aware ordering (setup/hold penalties)

To bias scan ordering away from timing-critical sources (heuristic penalty based on STA pin slack at the source scan-out pin):

- `DFT_TIMING_SETUP_WEIGHT=<nonnegative float>`
- `DFT_TIMING_HOLD_WEIGHT=<nonnegative float>`
- `DFT_TIMING_CRITICAL_SLACK=<nonnegative float>` (0 = only penalize negative slack)

Example:
- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_timing_aware DFT_ENABLE=1 DFT_TIMING_SETUP_WEIGHT=1.0 DFT_TIMING_CRITICAL_SLACK=0.05 finish`

## Optional: Preferred wiring direction (vertical weighting)

To bias scan ordering against vertical movement (preferred horizontal wiring), set:

- `DFT_VERTICAL_WEIGHT=<positive float>` (default `1.0`)

## Optional: Chain length balancing (max imbalance)

To constrain chain lengths to be reasonably balanced (reduces tester time at the cost of potentially more scan chains), set:

- `DFT_MAX_IMBALANCE=<nonnegative float>` (percent; default `2`)

## Optional: ScanOpt-style ordering constraints (groups + fixed edges)

Provide a constraints file to enforce contiguity (groups) and directed adjacency (fixed edges):

- `DFT_SCAN_ORDER_CONSTRAINTS_FILE=/path/to/scan_constraints.txt`

File format:
- Documented in OpenROAD DFT at `tools/OpenROAD/src/dft/README.md` (“Scan Ordering Constraints File”).
- Note: begin/end coordinates are in DBU (the same units used by DEF/ODB).

## Optional: Exclude functional shift registers

If your design contains functional shift registers (direct Q→D chains) that you do not
want included in scan stitching, enable automatic detection/exclusion:

- `DFT_EXCLUDE_SHIFT_REGISTERS=1`
- `DFT_SHIFT_REGISTER_MIN_LENGTH=<int>` (default `4`)

## Optional: Reuse existing scan ports / custom naming

To stitch against existing scan ports (or to change scan port naming), override the OpenROAD DFT name patterns:

- `DFT_SCAN_ENABLE_NAME_PATTERN=<name-or-inst/pin>`
- `DFT_SCAN_IN_NAME_PATTERN=<name-or-inst/pin-with-{}>`
- `DFT_SCAN_OUT_NAME_PATTERN=<name-or-inst/pin-with-{}>`

Example (top-level ports named `se`, `si_0`, `so_0`, ...):
- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_custom_ports DFT_ENABLE=1 DFT_SCAN_ENABLE_NAME_PATTERN=se DFT_SCAN_IN_NAME_PATTERN=si_{} DFT_SCAN_OUT_NAME_PATTERN=so_{} finish`

## Optional: Explicit scan ordering (order file)

To force an exact scan ordering per chain (user-defined scan path), provide an order file:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_order_file DFT_ENABLE=1 DFT_SCAN_SOLVER=order_file DFT_SCAN_ORDER_FILE=/path/to/order.txt finish`

File format: `chain_name inst0 inst1 inst2 ...` (one chain per line). For single-chain designs, a single line `inst0 inst1 ...` is also accepted.

## Optional: Export SCANDEF (ATPG)

To export a standalone DEF-style `SCANCHAINS` section for ATPG/external tooling:

- `DFT_WRITE_SCANDEF=1` (writes to `$RESULTS_DIR/6_final.scandef`)
- `DFT_SCANDEF_FILE=/path/to/output.scandef` (optional override)

## Sanity Checks

- Report the plan (from OpenROAD, after `scan_replace`):
  - `report_dft_plan -verbose`
  - `report_dft_plan_pins -verbose` (includes SI/SO pin coordinates)
- Validate chain integrity from a finished netlist:
  - `python3 flow/util/scan_chain_validate.py --verilog flow/results/<platform>/<design>/<variant>/6_final.v`
  - For multi-chain designs (`scan_in_0/scan_out_0`, `scan_in_1/scan_out_1`, ...), use `--auto-chains`.
- Or validate from an ODB (runs `scan_replace` + `execute_dft_plan` in-memory and writes a temp netlist):
  - `python3 flow/util/scan_chain_validate.py --odb flow/results/<platform>/<design>/<variant>/3_5_place_dp.odb --openroad $OPENROAD_EXE --liberty <lib> --sdc flow/results/<platform>/<design>/<variant>/3_place.sdc --ensure-ports --scan-replace --execute-dft-plan`

## Compare “Before vs After” QoR

- Routed wirelength / timing: compare `flow/results/<...>/metrics.json` and the OpenROAD/OpenSTA reports between `baseline_no_dft` and `with_dft`.
- Scan chain cost proxy (Manhattan): emitted automatically (default) as `flow/reports/<platform>/<design>/<variant>/dft_scan_chain_cost_{pregrt,finish}.rpt` and as metrics `dft_scan_chain_cost_um` / `dft_scan_chain_max_step_um` in the stage JSONs.
- Scan-chain wire metric on a fixed placement (also runs an NN heuristic for comparison):
  - `python3 flow/util/scan_chain_cost.py --scan-replace --nearest-neighbor --openroad $OPENROAD_EXE --liberty <lib> --odb flow/results/<...>/3_5_place_dp.odb --sdc flow/results/<...>/3_place.sdc`

## Visualize scan chain “jumps”

To see the scan chain polyline between placed scan cells (and highlight the longest hops in red), generate a PNG (works in Codex CLI). By default it also draws dashed black edges from `scan_in_N/scan_out_N` port locations (DEF PINS) to the first/last scan cell. If multiple scan chains are detected, the plotter will default to showing all chains (combined) unless you explicitly select a single chain via `--scan-in/--scan-out`.

- `python3 flow/util/scan_chain_plot.py --verilog flow/results/<platform>/<design>/<variant>/6_final.v --def flow/results/<platform>/<design>/<variant>/6_final.def --out flow/reports/<platform>/<design>/<variant>/dft_scan_chain.png`
- Pin-level (SI/SO) plot directly from ODB:
  - `openroad -python -exit flow/util/scan_chain_plot_openroad.py --odb flow/results/<platform>/<design>/<variant>/<stage>.odb --out flow/reports/<platform>/<design>/<variant>/dft_scan_chain_pins.png`

To generate one plot per chain, use `--auto-chains --out <dir>`. To generate SVG instead, use a `.svg` output path (or pass `--format svg`).
