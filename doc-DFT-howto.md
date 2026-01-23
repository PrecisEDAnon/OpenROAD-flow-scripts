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

Note:
- If your design contains mixed clock domains and/or negedge flops, ORFS defaults `DFT_LOCKUP_POLICY=auto` and may fall back to `DFT_CLOCK_MIXING=no_mix`, which can increase the number of scan chains/ports.
  - To force `clock_mix` even when mixed clock/edge chains are detected, set `DFT_LOCKUP_POLICY=off` (you are responsible for lockup/timing correctness).
- If your library uses active-low scan enable, set `DFT_SCAN_ENABLE_DISABLED_VALUE=1` so functional STA/power disables scan paths correctly.

## Optional: Routing-aware ordering (trial route, then stitch)

If you want scan ordering to use trial global-route guides (paper-style “routing-aware” ordering), defer stitching until after the first global route:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_route_aware DFT_ENABLE=1 DFT_ROUTE_AWARE=1 finish`

## Optional: ScanOpt-next ordering (bundled solver)

To use the bundled “ScanOpt-next” reference solver (placement-based ordering) instead of OpenROAD’s built-in heuristic:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft_scanopt_next DFT_ENABLE=1 DFT_SCAN_SOLVER=scanopt_next finish`

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

## Sanity Checks

- Report the plan (from OpenROAD, after `scan_replace`):
  - `report_dft_plan -verbose`
- Validate chain integrity from a finished netlist:
  - `python3 flow/util/scan_chain_validate.py --verilog flow/results/<platform>/<design>/<variant>/6_final.v`
  - For multi-chain designs (`scan_in_0/scan_out_0`, `scan_in_1/scan_out_1`, ...), use `--auto-chains`.
- Or validate from an ODB (runs `scan_replace` + `execute_dft_plan` in-memory and writes a temp netlist):
  - `python3 flow/util/scan_chain_validate.py --odb flow/results/<platform>/<design>/<variant>/3_5_place_dp.odb --openroad $OPENROAD_EXE --liberty <lib> --sdc flow/results/<platform>/<design>/<variant>/3_place.sdc --ensure-ports --scan-replace --execute-dft-plan`

## Compare “Before vs After” QoR

- Routed wirelength / timing: compare `flow/results/<...>/metrics.json` and the OpenROAD/OpenSTA reports between `baseline_no_dft` and `with_dft`.
- Scan-chain wire metric on a fixed placement (also runs an NN heuristic for comparison):
  - `python3 flow/util/scan_chain_cost.py --scan-replace --nearest-neighbor --openroad $OPENROAD_EXE --liberty <lib> --odb flow/results/<...>/3_5_place_dp.odb --sdc flow/results/<...>/3_place.sdc`

## Visualize scan chain “jumps”

To see the scan chain polyline between placed scan cells (and highlight the longest hops in red), generate a PNG (works in Codex CLI):

- `python3 flow/util/scan_chain_plot.py --verilog flow/results/<platform>/<design>/<variant>/6_final.v --def flow/results/<platform>/<design>/<variant>/6_final.def --out flow/reports/<platform>/<design>/<variant>/dft_scan_chain.png`

To generate SVG instead, use a `.svg` output path (or pass `--format svg`).
