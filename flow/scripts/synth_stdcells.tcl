# Read standard cells and macros as blackbox inputs
if { [info exists ::env(SYNTH_CELL_MODEL_FILES)] && $::env(SYNTH_CELL_MODEL_FILES) != "" } {
  # When functional Verilog stdcell models are loaded (e.g. Nangate45 `cells.v`),
  # avoid overwriting those modules while still creating blackbox stubs for
  # any cell modules not covered by the model file (sequential variants, macros).
  read_liberty -nooverwrite -setattr liberty_cell -lib {*}$::env(LIB_FILES)
  set wb_args [list -nooverwrite -setattr liberty_cell]
} else {
  read_liberty -overwrite -setattr liberty_cell -lib {*}$::env(LIB_FILES)
  set wb_args [list -overwrite -setattr liberty_cell]
}

# When doing a gate-level remap, we flatten Liberty-derived whiteboxes into the
# design. Avoid `-unit_delay` here because it imports combinational timing arcs
# as `specify` statements which OpenSTA cannot parse after flattening.
if { ![env_var_equals SYNTH_GATE_REMAP 1] } {
  lappend wb_args -unit_delay
}
lappend wb_args -wb -ignore_miss_func -ignore_buses
read_liberty {*}$wb_args {*}$::env(LIB_FILES)
