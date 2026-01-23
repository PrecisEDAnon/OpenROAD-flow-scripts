# DFT scan insertion hook for ORFS (routing-aware variant).
#
# Intended use: set `POST_GLOBAL_ROUTE_TCL` to this file, together with:
#   - `POST_FLOORPLAN_TCL=.../dft_scan_post_floorplan.tcl` (scan_replace + ports)
#   - `PRE_GLOBAL_ROUTE_TCL=.../dft_scan_pre_global_route.tcl` with `DFT_DEFER_STITCH=1`
#
# This runs after the initial global route completes, before repair_design /
# repair_timing, so we can:
#   - use the trial global-route guides for routing-aware scan ordering
#   - stitch scan chains (execute_dft_plan)
#   - route only the modified nets incrementally

puts "DFT: post-global-route execute_dft_plan (routing-aware ordering)"

# We expect helper procs from dft_scan_pre_global_route.tcl.
if { [info commands dft_apply_dft_config] == "" || [info commands dft_get_env] == "" || [info commands dft_get_env_bool] == "" } {
  error "DFT: expected DFT helper procs from dft_scan_pre_global_route.tcl; set PRE_GLOBAL_ROUTE_TCL first"
}

dft_apply_dft_config

# Optionally re-place scan ports using the *routing-aware* plan (based on the
# trial global-route guides).
if { [info commands dft_place_scan_ports_from_plan] != "" } {
  dft_place_scan_ports_from_plan
}

# Ensure functional-mode STA/power assumptions in this stage too.
if { [info commands dft_set_scan_enable_case_analysis] != "" } {
  dft_set_scan_enable_case_analysis
} else {
  # Backward compatibility.
  set_case_analysis 0 [get_ports scan_enable_0]
}

# Capture netlist changes (scan stitching + buffering) and only route the
# modified nets.
log_cmd global_route -start_incremental

if { [info commands dft_stitch_scan_chains] != "" } {
  dft_stitch_scan_chains "postgrt"
} else {
  execute_dft_plan
}

if { [info commands dft_buffer_scan_enable_net] != "" } {
  dft_buffer_scan_enable_net
}

if { [info commands dft_mark_scan_nets_dont_touch] != "" } {
  dft_mark_scan_nets_dont_touch
}

set local_res_aware ""
if { [info exists res_aware] } {
  set local_res_aware $res_aware
}

log_cmd global_route -end_incremental {*}$local_res_aware \
  -congestion_report_file $::env(REPORTS_DIR)/congestion_post_dft.rpt
