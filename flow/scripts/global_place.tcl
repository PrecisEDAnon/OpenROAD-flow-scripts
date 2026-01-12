utl::set_metrics_stage "globalplace__{}"
source $::env(SCRIPTS_DIR)/load.tcl
erase_non_stage_variables place
load_design 3_2_place_iop.odb 2_floorplan.sdc

set_dont_use $::env(DONT_USE_CELLS)

if { $::env(GPL_TIMING_DRIVEN) } {
  remove_buffers
}

# Do not buffer chip-level designs
# by default, IO ports will be buffered
# to not buffer IO ports, set environment variable
# DONT_BUFFER_PORT = 1
if { ![env_var_exists_and_non_empty FOOTPRINT] } {
  if { !$::env(DONT_BUFFER_PORTS) } {
    puts "Perform port buffering..."
    buffer_ports {*}[env_var_or_empty BUFFER_PORTS_ARGS]
  }
}

# Optional guidance: placement clusters derived from external "soft assignments".
# CSV format: inst_name,cluster_id[,weight]
if { [env_var_equals GPL_SOFT_PLACEMENT_CLUSTERS_ENABLE 1] && \
     [env_var_exists_and_non_empty GPL_SOFT_PLACEMENT_CLUSTERS_FILE] } {
  set cluster_args [list -file $::env(GPL_SOFT_PLACEMENT_CLUSTERS_FILE)]
  append_env_var cluster_args GPL_SOFT_PLACEMENT_CLUSTERS_MIN_WEIGHT -min_weight 1
  append_env_var cluster_args GPL_SOFT_PLACEMENT_CLUSTERS_MIN_SIZE -min_cluster_size 1
  append_env_var cluster_args GPL_SOFT_PLACEMENT_CLUSTERS_MAX_SIZE -max_cluster_size 1
  append_env_var cluster_args GPL_SOFT_PLACEMENT_CLUSTERS_SPLIT_LARGE -split_large_clusters 0
  append_env_var cluster_args GPL_SOFT_PLACEMENT_CLUSTERS_BEST_EFFORT -best_effort 0
  log_cmd read_soft_placement_clusters {*}$cluster_args
}

set global_placement_args {}

# Randomness control for placement "views" (used by external tomography loops).
append_env_var global_placement_args GPL_RANDOM_SEED -random_seed 1

# Parameters for routability mode in global placement
append_env_var global_placement_args GPL_ROUTABILITY_DRIVEN -routability_driven 0

# Parameters for timing driven mode in global placement
if { $::env(GPL_TIMING_DRIVEN) } {
  lappend global_placement_args {-timing_driven}
  if { [info exists ::env(GPL_KEEP_OVERFLOW)] } {
    lappend global_placement_args -keep_resize_below_overflow $::env(GPL_KEEP_OVERFLOW)
  }
}

# Parameters for phi coefficients in global placement
set min_phi $::env(MIN_PLACE_STEP_COEF)
set max_phi $::env(MAX_PLACE_STEP_COEF)

if { $min_phi > $max_phi } {
  utl::error GPL 200 \
    "MIN_PLACE_STEP_COEF ($min_phi) cannot be greater than \
MAX_PLACE_STEP_COEF ($max_phi)"
}

lappend global_placement_args -min_phi_coef $::env(MIN_PLACE_STEP_COEF)
lappend global_placement_args -max_phi_coef $::env(MAX_PLACE_STEP_COEF)

proc do_placement { global_placement_args } {
  set all_args [concat [list -density [place_density_with_lb_addon] \
    -pad_left $::env(CELL_PAD_IN_SITES_GLOBAL_PLACEMENT) \
    -pad_right $::env(CELL_PAD_IN_SITES_GLOBAL_PLACEMENT)] \
    $global_placement_args]

  lappend all_args {*}[env_var_or_empty GLOBAL_PLACEMENT_ARGS]

  log_cmd global_placement {*}$all_args
}

set result [catch { do_placement $global_placement_args } errMsg]
if { $result != 0 } {
  orfs_write_db $::env(RESULTS_DIR)/3_3_place_gp-failed.odb
  error $errMsg
}

log_cmd estimate_parasitics -placement

if { $::env(CLUSTER_FLOPS) } {
  cluster_flops
  log_cmd estimate_parasitics -placement
}

report_metrics 3 "global place" false false

orfs_write_db $::env(RESULTS_DIR)/3_3_place_gp.odb
