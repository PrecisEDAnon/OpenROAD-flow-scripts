source $::env(SCRIPTS_DIR)/load.tcl
erase_non_stage_variables place
load_design 2_floorplan.odb 2_floorplan.sdc

if { [env_var_exists_and_non_empty FLOORPLAN_DEF] } {
  puts "FLOORPLAN_DEF is set. Skipping global placement without IOs"
} elseif { [all_pins_placed] } {
  puts "All pins are placed. Skipping global placement without IOs"
} else {
  if { [env_var_equals GPL_SOFT_PLACEMENT_CLUSTERS_ENABLE 1] && \
       [env_var_exists_and_non_empty GPL_SOFT_PLACEMENT_CLUSTERS_FILE] } {
    set cluster_args [list -file $::env(GPL_SOFT_PLACEMENT_CLUSTERS_FILE) -best_effort]
    append_env_var cluster_args GPL_SOFT_PLACEMENT_CLUSTERS_MIN_WEIGHT -min_weight 1
    append_env_var cluster_args GPL_SOFT_PLACEMENT_CLUSTERS_MIN_SIZE -min_cluster_size 1
    append_env_var cluster_args GPL_SOFT_PLACEMENT_CLUSTERS_MAX_SIZE -max_cluster_size 1
    append_env_var cluster_args GPL_SOFT_PLACEMENT_CLUSTERS_SPLIT_LARGE -split_large_clusters 0
    log_cmd read_soft_placement_clusters {*}$cluster_args
  }

  set global_placement_args [list -skip_io]
  append_env_var global_placement_args GPL_RANDOM_SEED -random_seed 1

  log_cmd global_placement {*}$global_placement_args \
    -density [place_density_with_lb_addon] \
    -pad_left $::env(CELL_PAD_IN_SITES_GLOBAL_PLACEMENT) \
    -pad_right $::env(CELL_PAD_IN_SITES_GLOBAL_PLACEMENT) \
    {*}[env_var_or_empty GLOBAL_PLACEMENT_ARGS]
}

orfs_write_db $::env(RESULTS_DIR)/3_1_place_gp_skip_io.odb
