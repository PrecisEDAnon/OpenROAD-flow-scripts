utl::set_metrics_stage "surrogate__{}"

source $::env(SCRIPTS_DIR)/load.tcl

if { [llength [info commands surrogate_optimize]] == 0 } {
  puts stderr "ERROR: surrogate_optimize is not available."
  puts stderr "Build OpenROAD with ENABLE_SURROGATE=ON and run with OPENROAD_ENABLE_SURROGATE=1."
  exit 1
}

erase_non_stage_variables floorplan
load_design 1_synth.v 1_synth.sdc

set space_file ""
if { [env_var_exists_and_non_empty SURROGATE_SPACE_FILE] } {
  set space_file $::env(SURROGATE_SPACE_FILE)
} else {
  set space_file [file join $::env(DESIGN_DIR) surrogate_space.json]
}

set objective "effective_clock_period"
if { [env_var_exists_and_non_empty SURROGATE_OBJECTIVE] } {
  set objective $::env(SURROGATE_OBJECTIVE)
}

set samples 20000
if { [env_var_exists_and_non_empty SURROGATE_SAMPLES] } {
  set samples $::env(SURROGATE_SAMPLES)
}

set top_n 1
if { [env_var_exists_and_non_empty SURROGATE_TOP_N] } {
  set top_n $::env(SURROGATE_TOP_N)
}

set base_params_file ""
if { [env_var_exists_and_non_empty SURROGATE_BASE_PARAMS_FILE] } {
  set base_params_file $::env(SURROGATE_BASE_PARAMS_FILE)
} else {
  set default_base_params [file join $::env(DESIGN_DIR) surrogate_base_params.json]
  if { [file exists $default_base_params] } {
    set base_params_file $default_base_params
  }
}

set seed "auto"
if { [env_var_exists_and_non_empty SURROGATE_SEED] } {
  set seed $::env(SURROGATE_SEED)
}

set noise 0.0
if { [env_var_exists_and_non_empty SURROGATE_NOISE] } {
  set noise $::env(SURROGATE_NOISE)
}

set fidelity 2
if { [env_var_exists_and_non_empty SURROGATE_FIDELITY] } {
  set fidelity $::env(SURROGATE_FIDELITY)
}

set multi_fidelity 0
if { [env_var_exists_and_non_empty SURROGATE_MULTI_FIDELITY] } {
  set multi_fidelity $::env(SURROGATE_MULTI_FIDELITY)
}

set shrink 0.15
if { [env_var_exists_and_non_empty SURROGATE_SHRINK] } {
  set shrink $::env(SURROGATE_SHRINK)
}

set time_budget_s ""
if { [env_var_exists_and_non_empty SURROGATE_TIME_BUDGET_S] } {
  set time_budget_s $::env(SURROGATE_TIME_BUDGET_S)
}

set freeze "clock_period"
if { [env_var_exists_and_non_empty SURROGATE_FREEZE] } {
  set freeze $::env(SURROGATE_FREEZE)
}

set calibrate_ws ""
if { [env_var_exists_and_non_empty SURROGATE_CALIBRATE_WS_FILE] } {
  set calibrate_ws $::env(SURROGATE_CALIBRATE_WS_FILE)
}

set calibrate_wl ""
if { [env_var_exists_and_non_empty SURROGATE_CALIBRATE_WL_FILE] } {
  set calibrate_wl $::env(SURROGATE_CALIBRATE_WL_FILE)
}

set reset_calibration 0
if { [env_var_exists_and_non_empty SURROGATE_RESET_CALIBRATION] } {
  set reset_calibration $::env(SURROGATE_RESET_CALIBRATION)
}

set output_file [file join $::env(RESULTS_DIR) surrogate_optimize.json]
if { [env_var_exists_and_non_empty SURROGATE_OUTPUT] } {
  set output_file $::env(SURROGATE_OUTPUT)
}

puts "Running surrogate_optimize..."
puts "  space_file: $space_file"
puts "  objective:  $objective"
puts "  samples:    $samples"
puts "  top_n:      $top_n"
puts "  seed:       $seed"
puts "  noise:      $noise"
puts "  fidelity:   $fidelity"
puts "  multi_fid:  $multi_fidelity"
puts "  shrink:     $shrink"
puts "  budget_s:   $time_budget_s"
puts "  freeze:     $freeze"
puts "  calib_ws:   $calibrate_ws"
puts "  calib_wl:   $calibrate_wl"
puts "  reset_cal:  $reset_calibration"
puts "  base_params:$base_params_file"
puts "  output:     $output_file"

set extra_args {}
if { $base_params_file != "" } {
  lappend extra_args -base_params_file $base_params_file
}
if { $freeze != "" } {
  lappend extra_args -freeze $freeze
}
if { $calibrate_ws != "" } {
  lappend extra_args -calibrate_ws_file $calibrate_ws
}
if { $calibrate_wl != "" } {
  lappend extra_args -calibrate_wl_file $calibrate_wl
}
if { $reset_calibration != 0 } {
  lappend extra_args -reset_calibration
}
if { $fidelity != "" } {
  lappend extra_args -fidelity $fidelity
}
if { $multi_fidelity != 0 } {
  lappend extra_args -multi_fidelity
  lappend extra_args -shrink $shrink
}
if { $time_budget_s != "" } {
  lappend extra_args -time_budget_s $time_budget_s
}

set result [surrogate_optimize \
  -builtin \
  -space_file $space_file \
  -objective $objective \
  -minimize \
  -samples $samples \
  -top_n $top_n \
  -seed $seed \
  -noise $noise \
  {*}$extra_args \
  -format simple \
  -output $output_file \
  -include_features]

puts $result
