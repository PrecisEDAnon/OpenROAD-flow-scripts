# DFT / Scan in ORFS (OpenROAD-flow-scripts)

Quickstart: `doc-DFT-howto.md`

This branch wires OpenROAD’s DFT scan insertion into the ORFS flow (opt-in hook scripts, or `DFT_ENABLE=1`), plus:

- scan stitching validation (`flow/util/scan_chain_validate.py`)
- scan-only routed wirelength reporting (`flow/scripts/dft_scan_wirelength.tcl`)
- scan-chain visualization (`flow/util/scan_chain_plot.py`)
- scan-order cost proxy (`flow/util/scan_chain_cost.py`)

## Required OpenROAD

`tools/OpenROAD` is pinned to `https://github.com/PrecisEDAnon/OpenROAD` (`OpenROAD-clean-DFT`) at:

- `b60cadb4dc3eeaeda4e3a5b6c0f4aeb7e11f82aa`

Key DFT-facing additions in that branch:

- Works with vanilla OpenSTA (fallback scan-pin inference by common names).
- Adds `set_dft_config -chain_count` and `-scan_order_metric {PLACEMENT|PIN_TO_NET}`.
- Fixes scan stitching correctness regressions.

## ORFS Flow Integration

The flow already has generic hook points; DFT uses them:

- `flow/scripts/dft_scan_post_floorplan.tcl` (`POST_FLOORPLAN_TCL=...`)
  - `set_dft_config ...` (supports `DFT_CHAIN_COUNT`, `DFT_MAX_CHAIN_LENGTH`, name patterns, order metric)
  - `scan_replace`
  - ensures scan ports exist (ports or instance/pin endpoints)
  - sets functional-mode case analysis (`DFT_SCAN_ENABLE_DISABLED_VALUE`, default `0`)

- `flow/scripts/dft_scan_pre_global_route.tcl` (`PRE_GLOBAL_ROUTE_TCL=...`)
  - applies the same DFT config
  - optionally places scan ports near chain endpoints (`DFT_PLACE_SCAN_PORTS`)
  - stitches scan chains (`execute_dft_plan`) unless `DFT_DEFER_STITCH=1`
  - optional QoR hygiene:
    - buffer/split scan_enable (`DFT_BUFFER_SCAN_ENABLE=1` by default)
    - mark most SCAN nets `dont_touch` (`DFT_DONT_TOUCH_SCAN_NETS=1` by default)

- `flow/scripts/dft_scan_post_global_route.tcl` (`POST_GLOBAL_ROUTE_TCL=...`, optional)
  - runs after the initial global route, before repair
  - intended for routing-aware ordering (`DFT_SCAN_ORDER_METRIC=PIN_TO_NET`):
    - stitch scan chains after trial routing
    - incrementally global-route only the modified nets so `route.guide` includes scan nets

Recommended enablement:

- `DFT_ENABLE=1` (auto-wires post-floorplan + pre-global-route hooks)
- `DFT_ENABLE=1 DFT_ROUTE_AWARE=1` (also wires post-global-route hook and defaults `DFT_SCAN_ORDER_METRIC=PIN_TO_NET`)

## Key Knobs

- `DFT_CHAIN_COUNT`: exact number of scan chains
- `DFT_MAX_CHAIN_LENGTH` / `DFT_MAX_LENGTH`: cap bits/chain (also used to infer chain count)
- `DFT_SCAN_ORDER_METRIC`: `PLACEMENT` (shorter hops) or `PIN_TO_NET` (routing-aware; can look “jumpy”)
- `DFT_CLOCK_MIXING`: `no_mix` or `clock_mix`
- `DFT_LOCKUP_POLICY`: `auto`/`warn`/`error`/`off` for mixed clock/edge chains in `clock_mix`
- `DFT_SCAN_ENABLE_DISABLED_VALUE`: functional-mode value for scan enable (set to `1` for active-low scan enable)

## Scan Wirelength Reporting (Paper-style metric proxy)

`flow/scripts/dft_scan_wirelength.tcl` emits two reports under `$REPORTS_DIR`:

- `dft_scan_wirelength_<tag>.rpt`: dedicated SCAN nets (including scan_enable + scan_in/out)
- `dft_scan_link_wirelength_<tag>.rpt`: “scan-link nets” inferred from scan-in connectivity

`flow/scripts/final_report.tcl` calls `dft_report_scan_wirelength finish` by default; disable with `DFT_REPORT_SCAN_WIRELENGTH=0`.

## Visualizing “huge hops”

`PIN_TO_NET` minimizes distance to existing routed geometry, not Manhattan between flop centers. If a flop’s data/scan-out net already spans the die, the next best scan-in can be far away in placement but still “close” to that net’s routed shape.

The polyline plot (center→center) will show this as large jumps; it does not necessarily imply the routed scan wire is equally bad (use the scan wirelength reports above to judge the routed result).
