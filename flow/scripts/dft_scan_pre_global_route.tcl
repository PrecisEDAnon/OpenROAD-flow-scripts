# DFT scan insertion hook for ORFS.
#
# Intended use: set `PRE_GLOBAL_ROUTE_TCL` to this file.
#
# This runs after CTS, before global routing, so scan-chain connections are
# included in routing.

puts "DFT: execute_dft_plan (stitch scan chains)"

# Must match `flow/scripts/dft_scan_post_floorplan.tcl`.
set_dft_config -max_chains 1 -clock_mixing clock_mix

# Ensure functional-mode STA/power assumptions in this stage too.
set_case_analysis 0 [get_ports scan_enable_0]

execute_dft_plan

