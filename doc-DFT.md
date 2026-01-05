# DFT / Scan in ORFS (OpenROAD-flow-scripts)

Quickstart: `doc-DFT-howto.md`

This repo wires OpenROAD’s DFT scan insertion into the ORFS flow via **opt-in** hook scripts, plus utilities to measure scan-chain wirelength and validate scan stitching.

## Required OpenROAD

This branch pins the `tools/OpenROAD` submodule to **OpenROAD-clean-DFT**:

- Base: `7bc521f36a`
- +1 commit (DFT fixes): `661abebbc3c70c59b4a3991acd176a5cc785f0d4`

The key point: it works with **vanilla OpenSTA** (no OpenSTA parser patch required).

## ORFS Flow Integration (Where DFT Happens)

Two hook scripts are provided:

- `flow/scripts/dft_scan_post_floorplan.tcl`
  - Intended use: `POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl`
  - Runs after floorplan, before saving `2_1_floorplan.odb`:
    - `set_dft_config -max_chains 1 -clock_mixing clock_mix`
    - `scan_replace` (functional flops → scan flops)
    - creates scan ports: `scan_enable_0`, `scan_in_0`, `scan_out_0`
    - `set_case_analysis 0 [get_ports scan_enable_0]` (functional-mode timing)

- `flow/scripts/dft_scan_pre_global_route.tcl`
  - Intended use: `PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl`
  - Runs after CTS, before global routing:
    - `set_dft_config ...` (must match the post-floorplan config)
    - `set_case_analysis 0 [get_ports scan_enable_0]`
    - `execute_dft_plan` (stitches the scan chain using placement)

Notes:
- The scripts currently hardcode `-max_chains 1` to keep scan I/O stable for comparisons.
- `set_case_analysis 0` ensures STA uses functional-mode arcs for scan flops.

## OpenROAD-side Fixes (Summary)

The OpenROAD-clean-DFT commit includes the minimum required fixes to make DFT “alive” on top of `7bc521f36a`:

- Scan pin identification works in vanilla STA (`src/dbSta/src/dbSta.cc`):
  - removes an overly-strict `extPort()` guard
  - adds/uses fallback scan pin inference by common names (`SI/SE/SO`, etc.)
- DFT correctness fixes and functionality (DFT subsystem):
  - scan stitching fixes (no dropped links)
  - avoids reliance on `sta::TestCell`
  - scan-out fallback behavior
  - includes a small DFT regression (`scan_architect_no_mix_nangate45`)

## Scan-Ordering Benchmark (OpenROAD vs Nearest-Neighbor)

`flow/util/scan_chain_cost.py` runs OpenROAD’s `report_dft_plan -verbose`, computes total Manhattan scan-chain length, and can also compute a simple nearest-neighbor (NN) heuristic for comparison.

Opt/NN results (lower is better; `openroad_over_nn < 1` means OpenROAD is shorter than NN):

| platform | design | flops | openroad_um | nn_um | openroad_over_nn |
|---|---|---:|---:|---:|---:|
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

- `python3 flow/util/scan_chain_cost.py --scan-replace --nearest-neighbor --openroad tools/install/OpenROAD/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/cmp9_or0db856_rp100_20251229_022425/3_5_place_dp.odb --sdc flow/results/nangate45/ibex/cmp9_or0db856_rp100_20251229_022425/3_place.sdc`

Notes:
- ASAP7 needs multiple libs; pass them all, e.g. `--liberty flow/platforms/asap7/lib/NLDM/*_TT_*`.

## Scan-Chain Integrity Validation (Does It Actually Shift?)

QoR deltas and plan reports are necessary but not sufficient; we also want a basic structural check that the scan path is one continuous chain from `scan_in_0` to `scan_out_0`.

- `flow/util/scan_chain_validate.py` validates scan stitching from a gate-level netlist (or from an ODB by writing a temporary netlist via OpenROAD).
- It treats `assign` + inserted `BUF*/CLKBUF*` as transparent, so post-P&R buffering doesn’t cause false failures.

Example usage:

- Validate a finished netlist:
  - `python3 flow/util/scan_chain_validate.py --verilog flow/results/nangate45/ibex/with_dft/6_final.v`
- Validate from an ODB (writes a temp netlist first):
  - `python3 flow/util/scan_chain_validate.py --odb flow/results/nangate45/ibex/with_dft/6_final.odb --openroad tools/install/OpenROAD/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --sdc flow/results/nangate45/ibex/with_dft/6_final.sdc --ensure-ports`

