# DFT / Scan — Quickstart (Before vs After)

This is a short “how to run” guide. For implementation details, limitations, and scan-order benchmarks, see `doc-DFT.md`.

## Before (Baseline: no DFT)

Run the flow normally:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=baseline_no_dft finish`

## After (DFT Enabled: scan flops + stitched chain)

Enable the two DFT hook scripts:

- `POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl`
  - runs `scan_replace` (functional flops → scan flops)
  - creates scan ports: `scan_enable_0`, `scan_in_0`, `scan_out_0`
  - sets `set_case_analysis 0 [get_ports scan_enable_0]` (functional-mode timing)
- `PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl`
  - runs `execute_dft_plan` (stitches the scan chain using placement)

Example:

- `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=with_dft POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl finish`

## Sanity Checks

- Report the plan (from OpenROAD, after `scan_replace`):
  - `report_dft_plan -verbose`
- Validate chain integrity from a finished netlist:
  - `python3 flow/util/scan_chain_validate.py --verilog flow/results/<platform>/<design>/<variant>/6_final.v`
- Or validate from an ODB (runs `scan_replace` + `execute_dft_plan` in-memory and writes a temp netlist):
  - `python3 flow/util/scan_chain_validate.py --odb flow/results/<platform>/<design>/<variant>/3_5_place_dp.odb --openroad $OPENROAD_EXE --liberty <lib> --sdc flow/results/<platform>/<design>/<variant>/3_place.sdc --ensure-ports --scan-replace --execute-dft-plan`

## Compare “Before vs After” QoR

- Routed wirelength / timing: compare `flow/results/<...>/metrics.json` and the OpenROAD/OpenSTA reports between `baseline_no_dft` and `with_dft`.
- Scan-chain wire metric on a fixed placement (also runs an NN heuristic for comparison):
  - `python3 flow/util/scan_chain_cost.py --scan-replace --nearest-neighbor --openroad $OPENROAD_EXE --liberty <lib> --odb flow/results/<...>/3_5_place_dp.odb --sdc flow/results/<...>/3_place.sdc`

