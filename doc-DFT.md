# DFT / Scan in ORFS (OpenROAD-flow-scripts) — What We Changed + How To Reproduce

This document summarizes the DFT/scan-chain work done in this repo, with **OpenROAD commit `7bc521f36a` treated as the baseline** and a **vanilla OpenSTA** requirement (no `src/sta` parser changes needed).

## Goal / Scope

- Make OpenROAD’s DFT scan insertion usable in ORFS:
  - `scan_replace` converts functional flops → scan flops.
  - `execute_dft_plan` stitches scan chains using placement (wirelength-aware).
- Ensure it works with **vanilla OpenSTA** (no OpenSTA parser patches required).
- Provide a practical way to compare:
  - **7bc521 “baseline DFT”** (broken / mostly no-op) vs
  - **fixed DFT** (actually produces scan flops + stitched chains),
  - using QoR proxies and a scan-chain “TSP-like” cost metric.

## Baselines, Branches, and Key Commits

### OpenROAD submodule (`tools/OpenROAD`)

- Baseline reference branch: `orfs-baseline-7bc521`
  - pinned at `7bc521f36a`
- Fixed DFT/scan branch (vanilla OpenSTA): `orfs-dft-scan`
  - `ae904a0624` (current)
  - `5649f22868` (DFT enablement milestone; history)
- Older variant (kept for history): `orfs-dft-scan-with-opensta`
  - `5d3e1e243c`

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
- Add scan-out fallback to `Q` if scan-out pin metadata isn’t tagged.

### 4) Add/enable regression coverage

- Added a DFT regression `scan_architect_no_mix_nangate45` and wired it into OpenROAD’s CMake test setup.
- Verified DFT tests pass in the fixed OpenROAD build (`ctest -R '^dft\.'`).

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
    - `execute_dft_plan` (stitch chains)

Notes:
- This wiring is **opt-in**: you enable it by setting `POST_FLOORPLAN_TCL` and `PRE_GLOBAL_ROUTE_TCL` when you run `make -C flow ...`.
- The scripts set scan port name patterns explicitly:
  - enable: `scan_enable_{}`
  - in/out: `scan_in_{}`, `scan_out_{}`
- Configuration env vars (optional):
  - `DFT_CLOCK_MIXING` (default `clock_mix`)
  - `DFT_CHAIN_COUNT` (exact chains; takes priority over `DFT_MAX_CHAINS`)
  - `DFT_MAX_CHAINS` (max chains; default `1` unless `DFT_MAX_CHAIN_LENGTH`/`DFT_MAX_LENGTH` is set)
  - `DFT_MAX_CHAIN_LENGTH` / `DFT_MAX_LENGTH` (max bits per chain; when `DFT_CHAIN_COUNT` is set this becomes a per-chain cap; otherwise it can enable multiple chains via chain-count inference)
  - `DFT_PLACE_SCAN_PORTS` (default `1` when `DFT_CHAIN_COUNT>1`/`DFT_MAX_CHAINS>1`/`DFT_MAX_CHAIN_LENGTH` is set; otherwise default `0`)
    - when enabled, re-places `scan_in_N`/`scan_out_N` near their chain endpoints
    - force-disable with `DFT_PLACE_SCAN_PORTS=0`
  - `DFT_PLACE_SCAN_ENABLE_PORT` (default = `DFT_PLACE_SCAN_PORTS`; also re-place `scan_enable_0`)
  - `DFT_DONT_TOUCH_SCAN_NETS` (default `1`; marks SCAN nets `dont_touch` so `repair_design`/`repair_timing` won’t buffer/resize for scan-only nets)

## Reproduction: Baseline vs Fixed DFT (QoR Proxy Comparison)

### Design used

- `nangate45/ibex` (`flow/designs/nangate45/ibex/config.mk`)

### OpenROAD executables used

- Fixed OpenROAD (DFT works): `tools/OpenROAD/build_gate7bc521/bin/openroad` (reports `v2.0-26265-gae904a0624`)
- Baseline OpenROAD 7bc521 (DFT mostly broken): `tools/OpenROAD_7bc521/build_gate7bc521/bin/openroad`
  - built from a detached worktree at `7bc521f36a` (version string prints `HEAD-HASH-NOTFOUND` due to git-describe failure in that worktree)

### Flow commands

- Baseline (no DFT):
  - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_base_20260104 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build_gate7bc521/bin/openroad finish`
- Fixed DFT enabled:
  - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_dft_20260104 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build_gate7bc521/bin/openroad POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl finish`
- Baseline OpenROAD 7bc521 “DFT enabled” (shows it’s broken/no-op):
  - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_dft_or7bc521_20260104 OPENROAD_EXE=$(pwd)/tools/OpenROAD_7bc521/build_gate7bc521/bin/openroad POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl finish`

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
  - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_base_20260106_or26264 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build_gate7bc521/bin/openroad finish`
- DFT 1 chain:
  - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_dft_1chain_20260106_or26264 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build_gate7bc521/bin/openroad POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl finish`
- DFT max chain length examples:
  - 2 chains (`DFT_MAX_CHAIN_LENGTH=1000`):
    - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_dft_maxlen1000_20260106_or26264 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build_gate7bc521/bin/openroad POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl DFT_MAX_CHAIN_LENGTH=1000 finish`
  - 10 chains (`DFT_MAX_CHAIN_LENGTH=200`):
    - `make -C flow DESIGN_CONFIG=./designs/nangate45/ibex/config.mk FLOW_VARIANT=qor_scan_dft_maxlen200_20260106_or26264 OPENROAD_EXE=$(pwd)/tools/OpenROAD/build_gate7bc521/bin/openroad POST_FLOORPLAN_TCL=$(pwd)/flow/scripts/dft_scan_post_floorplan.tcl PRE_GLOBAL_ROUTE_TCL=$(pwd)/flow/scripts/dft_scan_pre_global_route.tcl DFT_MAX_CHAIN_LENGTH=200 finish`

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

Per scan chain, minimize a proxy cost:

- `cost = Σ ManhattanDist(p[i], p[i+1])`
- where `p[i]` is the *placed instance location* (OpenDB `dbInst::getLocation()`), not the exact SI/SO pin location.

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

Partition + per-chain ordering:
- `tools/OpenROAD/src/dft/src/architect/ScanArchitectHeuristic.cpp`
  - `ScanArchitectHeuristic::architect()`:
    - distributes scan cells over chains
    - when multiple chains are enabled and scan cells are placed, clusters scan cells using placement-aware reassignment (swap/move) to keep each chain spatially local (subject to per-chain max length)
    - runs the per-chain optimizer for falling-edge and rising-edge subsets

Per-chain optimizer (heuristic TSP-path):
- `tools/OpenROAD/src/dft/src/architect/Opt.cpp`
  - `OptimizeScanWirelength()`:
    - start node: lower-leftmost cell (min `x+y`, tie-break by instance name)
    - construction: greedy nearest-neighbor and farthest-insertion (bounded to `~10k` cells)
    - local improvement: bounded 2-opt passes (bounds depend on chain length)
    - large-chain fallback: rtree-based nearest candidate search

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
  - `python3 flow/util/scan_chain_cost.py --openroad tools/OpenROAD/build_gate7bc521/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_dft_20260104/3_5_place_dp.odb --sdc flow/results/nangate45/ibex/qor_scan_dft_20260104/3_place.sdc`
- With a simple “TSP-ish” nearest-neighbor baseline:
  - `python3 flow/util/scan_chain_cost.py --nearest-neighbor --openroad tools/OpenROAD/build_gate7bc521/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_dft_20260104/3_5_place_dp.odb --sdc flow/results/nangate45/ibex/qor_scan_dft_20260104/3_place.sdc`
- With a max chain length (enables multiple chains):
  - `python3 flow/util/scan_chain_cost.py --max-length 1000 --openroad tools/OpenROAD/build_gate7bc521/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_dft_1chain_20260106_or26264/4_cts.odb --sdc flow/results/nangate45/ibex/qor_scan_dft_1chain_20260106_or26264/4_cts.sdc`
- Visualize scan ordering (SVG):
  - `python3 flow/util/scan_chain_cost.py --out-svg temp-stash/scan.svg --openroad tools/OpenROAD/build_gate7bc521/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_dft_1chain_20260106_or26264/4_cts.odb --sdc flow/results/nangate45/ibex/qor_scan_dft_1chain_20260106_or26264/4_cts.sdc`
- On a no-DFT placed DB (compute hypothetical scan cost by doing `scan_replace` in-memory, without re-placement):
  - `python3 flow/util/scan_chain_cost.py --openroad tools/OpenROAD/build_gate7bc521/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/qor_scan_base_20260104/3_5_place_dp.odb --sdc flow/results/nangate45/ibex/qor_scan_base_20260104/3_place.sdc --scan-replace`

### Observed results (ibex, 1 chain)

- DFT (1 chain, `4_cts.odb`): `manhattan_um=9409.420`, naive lexicographic `90306.230` (ratio `9.597x`)
- no-DFT placed + hypothetical scan: `manhattan_um=9197.880`, naive `93778.840` (ratio `10.196x`)

Why DFT vs no-DFT chain cost is similar here:
- The scan chain cost is dominated by **where flops are placed** in the design.
- For `ibex` at this utilization, scan insertion didn’t significantly perturb placement, so the chain path length barely changes.

What *does* show up clearly:
- The new scan/control nets. Example (fixed DFT, routed DB):
  - `report_wire_length -net {scan_enable_0} -detailed_route` → `8033.45um`

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
- `python3 flow/util/scan_chain_cost.py --scan-replace --nearest-neighbor --openroad tools/OpenROAD/build_gate7bc521/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --odb flow/results/nangate45/ibex/cmp9_or0db856_rp100_20251229_022425/3_5_place_dp.odb --sdc flow/results/nangate45/ibex/cmp9_or0db856_rp100_20251229_022425/3_place.sdc`

Notes:
- ASAP7 needs multiple libs; pass them all, e.g. `--liberty flow/platforms/asap7/lib/NLDM/*_TT_*`.

## Current Limitations / Known Gaps

- `scan_opt` is implemented in OpenROAD DFT and re-stitches scan chains using the latest placement
  (without re-running `scan_replace`). The scan-chain optimizer uses NN + farthest-insertion + bounded 2-opt (with an rtree fallback for huge chains).
- ORFS exposes `DFT_CHAIN_COUNT` / `DFT_MAX_CHAIN_LENGTH` / `DFT_MAX_CHAINS` to tune chain count/length; beyond that, the main remaining lever for multi-chain QoR is scan port placement (scan-in/out “stems”). ORFS mitigates this by re-placing `scan_in_N`/`scan_out_N` near their chain endpoints (auto-enabled for multi-chain; override with `DFT_PLACE_SCAN_PORTS=0`).
- Clock-domain correctness constraints (lockups, strict no-mix, etc.) are not yet wired through ORFS configuration beyond `-clock_mixing`.

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
  - `python3 flow/util/scan_chain_validate.py --odb flow/results/nangate45/ibex/qor_scan_dft_20260104/6_final.odb --openroad tools/OpenROAD/build_gate7bc521/bin/openroad --liberty flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib --sdc flow/results/nangate45/ibex/qor_scan_dft_20260104/6_final.sdc --ensure-ports`
