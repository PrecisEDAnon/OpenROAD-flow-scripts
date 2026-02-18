# DFT / Scan — Quickstart (Before vs After)

This is a short “how to run” guide. For implementation details and limitations, see `doc-DFT.md`.

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
  - defaults to `DFT_SCAN_ORDER_SOLVER=SCANOPT` (UCLA ScanOptpack) with `DFT_SCANOPT_ROUNDS=500000` (mapped to UCLA major loops). `DFT_SCANOPT_TIME_LIMIT` applies to `DFT_SCAN_ORDER_SOLVER=ILS` (and to `SCANOPT` when it falls back to `ILS` for `PIN_TO_NET`).

Note on solver naming + time budgeting:
- `DFT_SCAN_ORDER_SOLVER=SCANOPT` selects the vendored UCLA ScanOptpack solver. **Upstream UCLA ScanOptpack has no time-based stopping condition**, so `DFT_SCANOPT_TIME_LIMIT` is ignored for `PLACEMENT` ordering; use `DFT_SCANOPT_ROUNDS` to control runtime.
- `DFT_SCAN_ORDER_SOLVER=ILS` selects the OpenROAD in-tree iterated local search solver; `DFT_SCANOPT_TIME_LIMIT` applies here.
- `report_dft_config` prints `Scan Order Solver: ScanOpt` when UCLA `SCANOPT` is selected, and `Scan Order Solver: ILS` when the in-tree solver is selected.

Note:
- If you set `DFT_CLOCK_MIXING=clock_mix`, mixed-clock/edge chains require lockup insertion during stitching. ORFS defaults `DFT_LOCKUP_POLICY=auto`, which will automatically re-run with `DFT_CLOCK_MIXING=no_mix` when mixed domains are detected (so `clock_mix` becomes a no-op unless you change the policy).
  - To keep `clock_mix`, set `DFT_LOCKUP_POLICY=warn` (or `off`) and configure lockup insertion (at minimum: `DFT_LOCKUP_CELL_RISING` + `DFT_LOCKUP_CLOCK_PIN_RISING`, and likewise `*_FALLING` if negedge scan flops exist).
- Polarity defaults to `DFT_POLARITY_MODE=strict` (no mixed polarity within a chain). To allow mixed polarity, set `DFT_POLARITY_MODE=mid` (falling-edge flops are stitched before rising-edge flops within each chain).

## Reproducer: DFT Replicator (JPEG-REAL1)

This repo includes a self-contained harness to exercise scan planning/stitching
against a fixed Sky130HD JPEG database:

- Run: `./replicator/run_all.sh`
- Docs: `replicator/README.md`
- Outputs:
  - Logs: `replicator/<section>/logs/<test>.log`
  - Artifacts: `replicator/<section>/runs/<test>/plot.png` and `post.odb`

Expected behavior:
- Only `5c`, `6c`, `6e` are infeasible by construction (and should error with `Scan architect constraints infeasible`).
- All other cases should produce a stitched solution and a `plot.png`.

## Optional: Routing-aware ordering (trial route, then stitch)

If you want scan ordering to use trial global-route guides (paper-style “routing-aware” ordering), defer stitching until after the first global route:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_route_aware DFT_ENABLE=1 DFT_ROUTE_AWARE=1 finish`

## Optional: OpenROAD ILS ordering (internal, iterated local search)

To use OpenROAD’s in-tree iterated local search solver (better QoR than the greedy heuristic, and supports `PIN_TO_NET`), set:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_or_scanopt DFT_ENABLE=1 DFT_SCAN_ORDER_SOLVER=ILS finish`

Tuning knobs:
- `DFT_SCANOPT_ROUNDS` (default `500000`)
- `DFT_SCANOPT_SEED` (default `1`)
- `DFT_SCANOPT_TIME_LIMIT` (seconds; default `300`, `0` = unlimited)
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

## Optional: Blockage-aware ordering (macro/blockage detour penalty)

To penalize scan links that would cross hard macros / placement blockages, set:

- `DFT_BLOCKAGE_WEIGHT=<nonnegative float>` (default `1.0`, `0` disables)

## Optional: Chain length balancing (max imbalance)

To constrain chain lengths to be reasonably balanced (reduces tester time at the cost of potentially more scan chains), set:

- `DFT_MAX_IMBALANCE=<nonnegative float>` (percent; default `2`)

## Optional: ScanOpt-style ordering constraints (groups + fixed edges)

Provide a constraints file to enforce contiguity (groups) and directed adjacency (fixed edges):

- `DFT_SCAN_ORDER_CONSTRAINTS_FILE=/path/to/scan_constraints.txt`

File format:
- Documented in OpenROAD DFT at `tools/OpenROAD/src/dft/README.md` (“Scan Ordering Constraints File”).
- Note: begin/end coordinates are in DBU (the same units used by DEF/ODB).
- Semantics note: `group` enforces **contiguity** in scan order; it does not, by itself, force all members into a single chain when `K>1`. To force same-chain membership, use `assign <chain> <inst|group...>`.

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

## Optional: Import SCANDEF and stitch (user-defined scan path)

If you already have a SCANDEF/DEF `SCANCHAINS` section describing the exact per-chain ordering you want, you can import and stitch it:

- ORFS flow (hook scripts):
  - `make -C flow ... DFT_ENABLE=1 DFT_USE_EXISTING_SCAN_CHAINS=1 DFT_IMPORT_SCANDEF_FILE=/path/to/your.scandef finish`
- `scan_replace`
- `read_def -incremental /path/to/your.scandef`
- `set_dft_config -use_existing_scan_chains 1`
- `execute_dft_plan`

## Optional: Export SCANDEF (ATPG)

To export a standalone DEF-style `SCANCHAINS` section for ATPG/external tooling:

- `DFT_WRITE_SCANDEF=1` (writes to `$RESULTS_DIR/6_final.scandef`)
- `DFT_SCANDEF_FILE=/path/to/output.scandef` (optional override)

Note:
- OpenROAD `write_scandef` currently prints the `ORDERED` list in the opposite direction of scan shifting (the first listed cell is adjacent to `scan_out_*`). For “scan_in → scan_out” order, reverse the list.

## Optional: Split multi-scan-port scan cells (MBFF-like)

If your scan library contains cells with multiple external scan-in/out pairs (e.g., `SI0/SO0` and `SI1/SO1`) and you want ordering/stitching to treat them as separate scan elements, enable:

- `DFT_SPLIT_MULTIBIT_SCAN_CELLS=1`

## Optional: Fail on power-domain crossings

By default, scan edges that cross voltage/switchable power domains are warned about (no automatic level shifter/isolation insertion). To make these crossings fatal:

- `DFT_ERROR_ON_POWER_DOMAIN_CROSSINGS=1`

## Sanity Checks

- Report the plan (from OpenROAD, after `scan_replace`):
  - `report_dft_plan -verbose`
  - `report_dft_plan_pins -verbose` (includes SI/SO pin coordinates)
- Validate chain integrity from a finished netlist:
  - `python3 flow/util/scan_chain_validate.py --verilog flow/results/<platform>/<design>/<variant>/6_final.v`
  - For multi-chain designs (`scan_in_0/scan_out_0`, `scan_in_1/scan_out_1`, ...), use `--auto-chains`.
- Or validate from an ODB (runs `scan_replace` + `execute_dft_plan` in-memory and writes a temp netlist):
  - `python3 flow/util/scan_chain_validate.py --odb flow/results/<platform>/<design>/<variant>/3_5_place_dp.odb --openroad $OPENROAD_EXE --liberty <lib> --sdc flow/results/<platform>/<design>/<variant>/3_place.sdc --ensure-ports --scan-replace --execute-dft-plan`
