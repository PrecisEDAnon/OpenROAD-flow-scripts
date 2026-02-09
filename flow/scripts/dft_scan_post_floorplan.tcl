# DFT scan insertion hook for ORFS.
#
# Intended use: set `POST_FLOORPLAN_TCL` to this file.
#
# This runs after floorplan, before saving `2_1_floorplan.odb`, so subsequent
# stages (place/cts/route) see scan flops and scan ports.

puts "DFT: scan_replace + create scan ports"

proc dft_get_env {name default_value} {
  if { [info exists ::env($name)] && $::env($name) != "" } {
    return $::env($name)
  }
  return $default_value
}

proc dft_apply_name_pattern {pattern value} {
  # Supports one optional "{}" placeholder.
  if { [string first "{}" $pattern] >= 0 } {
    return [string map [list "{}" $value] $pattern]
  }
  return $pattern
}

proc dft_name_pattern_is_inst_pin {name_or_pattern} {
  # OpenROAD interprets an unescaped "/" as "instance/pin". We do not attempt to
  # model escaping here; treat any "/" as instance/pin.
  return [expr {[string first "/" $name_or_pattern] >= 0}]
}

proc dft_set_scan_enable_case_analysis {scan_enable_name} {
  if { [catch {
    if { [dft_name_pattern_is_inst_pin $scan_enable_name] } {
      set_case_analysis 0 [get_pins $scan_enable_name]
    } else {
      set_case_analysis 0 [get_ports $scan_enable_name]
    }
  } err] } {
    puts "DFT: WARNING: couldn't set_case_analysis on scan enable '$scan_enable_name': $err"
  }
}

# Default behavior lets OpenROAD infer the number of scan chains (and thus the
# number of scan_in_N/scan_out_N ports) based on max_length and a soft default
# target length.
#
# To force a specific chain count, set either:
#   - DFT_CHAIN_COUNT (exact),
#   - DFT_MAX_CHAINS (explicit cap), and/or
#   - DFT_MAX_CHAIN_LENGTH (aka DFT_MAX_LENGTH) to bound chain length in bits.
set clock_mixing [dft_get_env DFT_CLOCK_MIXING "no_mix"]
set polarity_mode [dft_get_env DFT_POLARITY_MODE "mid"]
set scan_enable_pattern [dft_get_env DFT_SCAN_ENABLE_NAME_PATTERN "scan_enable_{}"]
set scan_in_pattern [dft_get_env DFT_SCAN_IN_NAME_PATTERN "scan_in_{}"]
set scan_out_pattern [dft_get_env DFT_SCAN_OUT_NAME_PATTERN "scan_out_{}"]
set max_length [dft_get_env DFT_MAX_CHAIN_LENGTH ""]
if { $max_length == "" } {
	set max_length [dft_get_env DFT_MAX_LENGTH ""]
}

set chain_count [dft_get_env DFT_CHAIN_COUNT ""]
	set scan_order_metric [dft_get_env DFT_SCAN_ORDER_METRIC ""]
	set scan_order_solver [dft_get_env DFT_SCAN_ORDER_SOLVER ""]
	set scanopt_rounds [dft_get_env DFT_SCANOPT_ROUNDS ""]
	set scanopt_seed [dft_get_env DFT_SCANOPT_SEED ""]
	set scanopt_time_limit [dft_get_env DFT_SCANOPT_TIME_LIMIT ""]
	set scanopt_temp_control [dft_get_env DFT_SCANOPT_TEMP_CONTROL ""]
	set scanopt_t_div [dft_get_env DFT_SCANOPT_T_DIV ""]
	set vertical_weight [dft_get_env DFT_VERTICAL_WEIGHT ""]
	set max_imbalance [dft_get_env DFT_MAX_IMBALANCE ""]
	set constraints_file [dft_get_env DFT_SCAN_ORDER_CONSTRAINTS_FILE ""]
	set timing_setup_weight [dft_get_env DFT_TIMING_SETUP_WEIGHT ""]
	set timing_hold_weight [dft_get_env DFT_TIMING_HOLD_WEIGHT ""]
	set timing_critical_slack [dft_get_env DFT_TIMING_CRITICAL_SLACK ""]
	set exclude_shift_registers [dft_get_env DFT_EXCLUDE_SHIFT_REGISTERS ""]
	set prefer_qbar [dft_get_env DFT_PREFER_QBAR ""]
	set shift_register_min_length [dft_get_env DFT_SHIFT_REGISTER_MIN_LENGTH ""]
	set insert_lockup [dft_get_env DFT_INSERT_LOCKUP ""]
	set lockup_cell_rising [dft_get_env DFT_LOCKUP_CELL_RISING ""]
	set lockup_cell_falling [dft_get_env DFT_LOCKUP_CELL_FALLING ""]
	set lockup_in_pin [dft_get_env DFT_LOCKUP_IN_PIN ""]
	set lockup_out_pin [dft_get_env DFT_LOCKUP_OUT_PIN ""]
	set lockup_clock_pin_rising [dft_get_env DFT_LOCKUP_CLOCK_PIN_RISING ""]
	set lockup_clock_pin_falling [dft_get_env DFT_LOCKUP_CLOCK_PIN_FALLING ""]
	set timing_buffer_cell [dft_get_env DFT_TIMING_BUFFER_CELL ""]
	set timing_buffer_in_pin [dft_get_env DFT_TIMING_BUFFER_IN_PIN ""]
	set timing_buffer_out_pin [dft_get_env DFT_TIMING_BUFFER_OUT_PIN ""]

set max_chains [dft_get_env DFT_MAX_CHAINS ""]

set dft_args [list \
  -clock_mixing $clock_mixing \
  -polarity_mode $polarity_mode \
	-scan_enable_name_pattern $scan_enable_pattern \
	-scan_in_name_pattern $scan_in_pattern \
	-scan_out_name_pattern $scan_out_pattern \
]
if { $scan_order_metric != "" } {
  lappend dft_args -scan_order_metric $scan_order_metric
}
if { $scan_order_solver != "" } {
  lappend dft_args -scan_order_solver $scan_order_solver
}
if { $scanopt_rounds != "" } {
  lappend dft_args -scanopt_rounds $scanopt_rounds
}
if { $scanopt_seed != "" } {
  lappend dft_args -scanopt_seed $scanopt_seed
}
if { $scanopt_time_limit != "" } {
  lappend dft_args -scanopt_time_limit $scanopt_time_limit
}
if { $scanopt_temp_control != "" } {
  lappend dft_args -scanopt_temp_control $scanopt_temp_control
}
if { $scanopt_t_div != "" } {
  lappend dft_args -scanopt_t_div $scanopt_t_div
}
	if { $vertical_weight != "" } {
	  lappend dft_args -vertical_weight $vertical_weight
	}
	if { $max_imbalance != "" } {
	  lappend dft_args -max_imbalance $max_imbalance
	}
	if { $constraints_file != "" } {
	  lappend dft_args -scan_order_constraints_file $constraints_file
	}
if { $timing_setup_weight != "" } {
  lappend dft_args -timing_setup_weight $timing_setup_weight
}
if { $timing_hold_weight != "" } {
  lappend dft_args -timing_hold_weight $timing_hold_weight
}
if { $timing_critical_slack != "" } {
  lappend dft_args -timing_critical_slack $timing_critical_slack
}
if { $exclude_shift_registers != "" } {
  lappend dft_args -exclude_shift_registers $exclude_shift_registers
}
if { $prefer_qbar != "" } {
  lappend dft_args -prefer_qbar $prefer_qbar
}
if { $shift_register_min_length != "" } {
  lappend dft_args -shift_register_min_length $shift_register_min_length
}
if { $insert_lockup != "" } {
  lappend dft_args -insert_lockup $insert_lockup
}
if { $lockup_cell_rising != "" } {
  lappend dft_args -lockup_cell_rising $lockup_cell_rising
}
if { $lockup_cell_falling != "" } {
  lappend dft_args -lockup_cell_falling $lockup_cell_falling
}
if { $lockup_in_pin != "" } {
  lappend dft_args -lockup_in_pin $lockup_in_pin
}
if { $lockup_out_pin != "" } {
  lappend dft_args -lockup_out_pin $lockup_out_pin
}
if { $lockup_clock_pin_rising != "" } {
  lappend dft_args -lockup_clock_pin_rising $lockup_clock_pin_rising
}
if { $lockup_clock_pin_falling != "" } {
  lappend dft_args -lockup_clock_pin_falling $lockup_clock_pin_falling
}
if { $timing_buffer_cell != "" } {
  lappend dft_args -timing_buffer_cell $timing_buffer_cell
}
if { $timing_buffer_in_pin != "" } {
  lappend dft_args -timing_buffer_in_pin $timing_buffer_in_pin
}
if { $timing_buffer_out_pin != "" } {
  lappend dft_args -timing_buffer_out_pin $timing_buffer_out_pin
}
if { $max_length != "" } {
  lappend dft_args -max_length $max_length
}
if { $chain_count != "" } {
  lappend dft_args -chain_count $chain_count
} elseif { $max_chains != "" } {
  lappend dft_args -max_chains $max_chains
}
set_dft_config {*}$dft_args

# Replace functional flops with scan-capable flops.
scan_replace

proc dft_ensure_scan_port {port_name io_type} {
  set block [ord::get_db_block]

  set bterm [$block findBTerm $port_name]
  if { $bterm != "NULL" } {
    return
  }

  set net [$block findNet $port_name]
  if { $net == "NULL" } {
    set net [odb::dbNet_create $block $port_name]
    $net setSigType SCAN
  }

	set bterm [odb::dbBTerm_create $net $port_name]
	$bterm setSigType SCAN
	$bterm setIoType $io_type
}

# Ensure scan ports have valid pin geometries early (some flows run routability
# global routing inside placement before `PRE_GLOBAL_ROUTE_TCL` runs, and
# footprint-based flows may skip `place_pins`).
proc dft_place_scan_ports_minimal {scan_enable_name scan_in_pattern scan_out_pattern chain_count} {
  if { [info commands place_pin] == "" } {
    puts "DFT: WARNING: place_pin command not available; scan port geometries may be missing"
    return
  }

  if { ![info exists ::env(IO_PLACER_H)] || ![info exists ::env(IO_PLACER_V)] } {
    puts "DFT: WARNING: IO_PLACER_H/V not set; can't place scan port geometries"
    return
  }

  set block [ord::get_db_block]
  if { $block == "NULL" } {
    puts "DFT: WARNING: no db block found; can't place scan port geometries"
    return
  }
  set die_rect [$block getDieArea]
  set xMin [$die_rect xMin]
  set yMin [$die_rect yMin]
  set xMax [$die_rect xMax]
  set yMax [$die_rect yMax]
  set mid_x [expr {($xMin + $xMax) / 2}]

  set h_layer [lindex $::env(IO_PLACER_H) 0]
  set v_layer [lindex $::env(IO_PLACER_V) 0]

 proc dft_place_pin_safe {pin_name layer_name x_dbu y_dbu} {
    if { $layer_name == "" } {
      return
    }
    # Ensure IO pin locations are snapped to the manufacturing grid to avoid
    # off-grid pin shapes (e.g., DRT-0416 in pin_access/global_route).
    set db [ord::get_db]
    set tech [$db getTech]
    set mgrid [$tech getManufacturingGrid]
    if { $mgrid == "" || $mgrid <= 0 } {
      set mgrid 1
    }
    set x_dbu [expr {int(round(double($x_dbu) / $mgrid)) * $mgrid}]
    set y_dbu [expr {int(round(double($y_dbu) / $mgrid)) * $mgrid}]
    set x_um [ord::dbu_to_microns $x_dbu]
    set y_um [ord::dbu_to_microns $y_dbu]
  if { [catch {
      place_pin -pin_name $pin_name -layer $layer_name \
        -location [list $x_um $y_um]
    } err] } {
      puts "DFT: WARNING: place_pin failed for '$pin_name' on '$layer_name': $err"
    }
  }

  # Shared enable: place near bottom center.
  if { ![dft_name_pattern_is_inst_pin $scan_enable_name] } {
    set enable_layer $v_layer
    if { $enable_layer == "" } {
      set enable_layer $h_layer
    }
    dft_place_pin_safe $scan_enable_name $enable_layer $mid_x $yMin
  }

  # Scan in/out ports: distribute along left/right.
  set io_layer $h_layer
  if { $io_layer == "" } {
    set io_layer $v_layer
  }

  if { $chain_count < 1 } {
    set chain_count 1
  }
  for { set i 0 } { $i < $chain_count } { incr i } {
    set y [expr {$yMin + int((($i + 1.0) / ($chain_count + 1.0)) * ($yMax - $yMin))}]
    set in_name [dft_apply_name_pattern $scan_in_pattern $i]
    set out_name [dft_apply_name_pattern $scan_out_pattern $i]
    if { ![dft_name_pattern_is_inst_pin $in_name] } {
      dft_place_pin_safe $in_name $io_layer $xMin $y
    }
    if { ![dft_name_pattern_is_inst_pin $out_name] } {
      dft_place_pin_safe $out_name $io_layer $xMax $y
    }
  }
}

# Infer how many chains will be created with the current DFT config so we can
# create the right number of scan ports before IO placement.
set chain_count 1
with_output_to_variable dft_plan_str { report_dft_plan }
if { ![regexp {Number of chains:\s*([0-9]+)} $dft_plan_str -> chain_count] } {
  puts "DFT: WARNING: couldn't parse chain count from report_dft_plan; defaulting to 1"
  set chain_count 1
}

# Scan I/O + shared enable (only create top-level ports when patterns are ports).
set scan_enable_name [dft_apply_name_pattern $scan_enable_pattern 0]
if { ![dft_name_pattern_is_inst_pin $scan_enable_name] } {
  dft_ensure_scan_port $scan_enable_name INPUT
}

set created_ports {}
for { set i 0 } { $i < $chain_count } { incr i } {
  set in_name [dft_apply_name_pattern $scan_in_pattern $i]
  set out_name [dft_apply_name_pattern $scan_out_pattern $i]
  if { ![dft_name_pattern_is_inst_pin $in_name] } {
    dft_ensure_scan_port $in_name INPUT
    lappend created_ports $in_name
  }
  if { ![dft_name_pattern_is_inst_pin $out_name] } {
    dft_ensure_scan_port $out_name OUTPUT
    lappend created_ports $out_name
  }
}

if { ![dft_name_pattern_is_inst_pin $scan_enable_name] } {
  lappend created_ports $scan_enable_name
}

# Place minimal geometries only when we created conventional top-level ports.
if { [llength $created_ports] > 0 } {
  dft_place_scan_ports_minimal $scan_enable_name $scan_in_pattern $scan_out_pattern $chain_count
}

# Functional-mode assumption for STA/power (disable scan path).
dft_set_scan_enable_case_analysis $scan_enable_name
