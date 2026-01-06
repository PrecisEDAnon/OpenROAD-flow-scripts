# DFT scan insertion hook for ORFS.
#
# Intended use: set `PRE_GLOBAL_ROUTE_TCL` to this file.
#
# This runs after CTS, before global routing, so scan-chain connections are
# included in routing.

puts "DFT: execute_dft_plan (stitch scan chains)"

proc dft_get_env {name default_value} {
  if { [info exists ::env($name)] && $::env($name) != "" } {
    return $::env($name)
  }
  return $default_value
}

# Must match `flow/scripts/dft_scan_post_floorplan.tcl`.
set clock_mixing [dft_get_env DFT_CLOCK_MIXING "clock_mix"]
set max_length [dft_get_env DFT_MAX_CHAIN_LENGTH ""]
if { $max_length == "" } {
  set max_length [dft_get_env DFT_MAX_LENGTH ""]
}

set max_chains [dft_get_env DFT_MAX_CHAINS ""]
if { $max_chains == "" && $max_length == "" } {
  set max_chains 1
}

set dft_args [list \
  -clock_mixing $clock_mixing \
  -scan_enable_name_pattern "scan_enable_{}" \
  -scan_in_name_pattern "scan_in_{}" \
  -scan_out_name_pattern "scan_out_{}" \
]
if { $max_length != "" } {
  lappend dft_args -max_length $max_length
}
if { $max_chains != "" } {
  lappend dft_args -max_chains $max_chains
}
set_dft_config {*}$dft_args

# Ensure functional-mode STA/power assumptions in this stage too.
set_case_analysis 0 [get_ports scan_enable_0]

execute_dft_plan
