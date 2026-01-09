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

proc dft_get_env_bool {name default_value} {
  set raw [dft_get_env $name $default_value]
  set v [string tolower [string trim "$raw"]]
  if { $v == "" } {
    return $default_value
  }
  if { $v in {"1" "true" "yes" "y" "on"} } {
    return 1
  }
  if { $v in {"0" "false" "no" "n" "off"} } {
    return 0
  }
  # Fall back to numeric interpretation if possible.
  if { ![catch {expr {$v != 0}} as_bool] } {
    return $as_bool
  }
  return $default_value
}

proc dft_ensure_scan_port {port_name io_type} {
  set block [ord::get_db_block]

  set bterm [$block findBTerm $port_name]
  if { $bterm != "NULL" } {
    $bterm setSigType SCAN
    $bterm setIoType $io_type
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

proc dft_boxes_intersect {ax1 ay1 ax2 ay2 bx1 by1 bx2 by2} {
  return [expr {$ax1 < $bx2 && $ax2 > $bx1 && $ay1 < $by2 && $ay2 > $by1}]
}

proc dft_get_primary_box {bterm} {
  foreach bpin [$bterm getBPins] {
    foreach box [$bpin getBoxes] {
      set layer_name [[ $box getTechLayer] getName]
      return [list $layer_name [$box xMin] [$box yMin] [$box xMax] [$box yMax]]
    }
  }
  return ""
}

proc dft_pin_overlaps_any_other_pin {pin_name layer_name xMin yMin xMax yMax} {
  set block [ord::get_db_block]
  foreach bterm [$block getBTerms] {
    set other_name [$bterm getName]
    if { $other_name == $pin_name } {
      continue
    }
    foreach bpin [$bterm getBPins] {
      foreach box [$bpin getBoxes] {
        set other_layer [[ $box getTechLayer] getName]
        if { $other_layer != $layer_name } {
          continue
        }
        if { [dft_boxes_intersect $xMin $yMin $xMax $yMax \
          [$box xMin] [$box yMin] [$box xMax] [$box yMax]] } {
          return 1
        }
      }
    }
  }
  return 0
}

proc dft_layer_track_pitch_dbu {layer_name} {
  set db [ord::get_db]
  set tech [$db getTech]
  set layer [$tech findLayer $layer_name]
  if { $layer == "NULL" } {
    return 0
  }

  set block [ord::get_db_block]
  set tg [$block findTrackGrid $layer]
  if { $tg == "NULL" } {
    return 0
  }

  set dir [$layer getDirection]
  if { $dir == "HORIZONTAL" } {
    lassign [$tg getGridPatternY 0] origin count pitch
  } else {
    lassign [$tg getGridPatternX 0] origin count pitch
  }
  return $pitch
}

proc dft_clamp {value lo hi} {
  if { $value < $lo } {
    return $lo
  }
  if { $value > $hi } {
    return $hi
  }
  return $value
}

proc dft_place_pin_no_overlap {pin_name layer_name x_dbu y_dbu} {
  set pitch [dft_layer_track_pitch_dbu $layer_name]
  if { $pitch <= 0 } {
    set pitch 1
  }

  set db [ord::get_db]
  set tech [$db getTech]
  set layer [$tech findLayer $layer_name]
  if { $layer == "NULL" } {
    puts "DFT: WARNING: can't find tech layer '$layer_name' for pin '$pin_name'"
    return 0
  }
  set dir [$layer getDirection]
  set offset_axis [expr {$dir == "HORIZONTAL" ? "y" : "x"}]

  set block [ord::get_db_block]
  set bterm [$block findBTerm $pin_name]
  if { $bterm == "NULL" } {
    puts "DFT: WARNING: can't find port '$pin_name' for pin placement"
    return 0
  }

  lassign [$bterm getFirstPinLocation] ok x0 y0
  set orig_box [dft_get_primary_box $bterm]
  set orig_layer $layer_name
  if { $orig_box != "" } {
    lassign $orig_box orig_layer bx1 by1 bx2 by2
  }

  set die_rect [$block getDieArea]
  set xMin [$die_rect xMin]
  set yMin [$die_rect yMin]
  set xMax [$die_rect xMax]
  set yMax [$die_rect yMax]

  for { set i 0 } { $i < 60 } { incr i } {
    if { $i == 0 } {
      set offset 0
    } else {
      set step [expr {($i + 1) / 2}]
      set sign [expr {($i % 2) ? 1 : -1}]
      set offset [expr {$sign * $step * $pitch}]
    }

    set cx $x_dbu
    set cy $y_dbu
    if { $offset_axis == "x" } {
      set cx [expr {$cx + $offset}]
    } else {
      set cy [expr {$cy + $offset}]
    }

    set cx [dft_clamp $cx $xMin $xMax]
    set cy [dft_clamp $cy $yMin $yMax]

    set x_um [ord::dbu_to_microns $cx]
    set y_um [ord::dbu_to_microns $cy]

    if { [catch {
      place_pin -pin_name $pin_name -layer $layer_name \
        -location [list $x_um $y_um] -force_to_die_boundary
    } err] } {
      puts "DFT: WARNING: place_pin failed for '$pin_name' on '$layer_name': $err"
      continue
    }

    set placed_box [dft_get_primary_box $bterm]
    if { $placed_box == "" } {
      continue
    }
    lassign $placed_box placed_layer px1 py1 px2 py2

    if { ![dft_pin_overlaps_any_other_pin $pin_name $placed_layer $px1 $py1 $px2 $py2] } {
      return 1
    }
  }

  # Restore original placement if we couldn't find a non-overlapping slot.
  if { $ok == 1 } {
    catch {
      place_pin -pin_name $pin_name -layer $orig_layer \
        -location [list [ord::dbu_to_microns $x0] [ord::dbu_to_microns $y0]] \
        -force_to_die_boundary
    }
  }
  puts "DFT: WARNING: couldn't place '$pin_name' without overlap; restored original location"
  return 0
}

proc dft_place_pin_near_inst {pin_name inst_name} {
  if { ![info exists ::env(IO_PLACER_H)] || ![info exists ::env(IO_PLACER_V)] } {
    puts "DFT: WARNING: IO_PLACER_H/V not set; skipping scan pin placement"
    return
  }

  set h_layer [lindex $::env(IO_PLACER_H) 0]
  set v_layer [lindex $::env(IO_PLACER_V) 0]
  if { $h_layer == "" || $v_layer == "" } {
    puts "DFT: WARNING: IO_PLACER_H/V empty; skipping scan pin placement"
    return
  }

  set block [ord::get_db_block]
  set inst [$block findInst $inst_name]
  if { $inst == "NULL" } {
    puts "DFT: WARNING: can't find instance '$inst_name' for placing '$pin_name'"
    return
  }

  lassign [$inst getLocation] x y
  set die_rect [$block getDieArea]
  set xMin [$die_rect xMin]
  set yMin [$die_rect yMin]
  set xMax [$die_rect xMax]
  set yMax [$die_rect yMax]
  set dist_left [expr {$x - $xMin}]
  set dist_right [expr {$xMax - $x}]
  set dist_bottom [expr {$y - $yMin}]
  set dist_top [expr {$yMax - $y}]

  # Pick the nearest die edge and project the pin location onto it.
  # - horizontal-track layers -> left/right edges
  # - vertical-track layers   -> top/bottom edges
  set edge left
  set min_dist $dist_left
  if { $dist_right < $min_dist } {
    set edge right
    set min_dist $dist_right
  }
  if { $dist_bottom < $min_dist } {
    set edge bottom
    set min_dist $dist_bottom
  }
  if { $dist_top < $min_dist } {
    set edge top
    set min_dist $dist_top
  }

  if { $edge == "left" } {
    dft_place_pin_no_overlap $pin_name $h_layer $xMin $y
  } elseif { $edge == "right" } {
    dft_place_pin_no_overlap $pin_name $h_layer $xMax $y
  } elseif { $edge == "bottom" } {
    dft_place_pin_no_overlap $pin_name $v_layer $x $yMin
  } else {
    dft_place_pin_no_overlap $pin_name $v_layer $x $yMax
  }
}

proc dft_place_scan_ports_from_plan {} {
  # Place scan ports near their corresponding chain endpoints to reduce scan
  # I/O wirelength (especially when multiple chains are enabled).
  set chain_count_env [dft_get_env DFT_CHAIN_COUNT ""]
  set max_chains_env [dft_get_env DFT_MAX_CHAINS ""]
  set max_length_env [dft_get_env DFT_MAX_CHAIN_LENGTH ""]
  if { $max_length_env == "" } {
    set max_length_env [dft_get_env DFT_MAX_LENGTH ""]
  }

  # Default to placing scan ports when the user explicitly configured multiple
  # chains (or a max-length bound that is typically used to create them).
  set default_place_scan_ports 0
  if { $chain_count_env != "" && $chain_count_env > 1 } {
    set default_place_scan_ports 1
  } elseif { $max_chains_env != "" && $max_chains_env > 1 } {
    set default_place_scan_ports 1
  } elseif { $max_length_env != "" } {
    set default_place_scan_ports 1
  }

  set place_scan_ports [dft_get_env_bool DFT_PLACE_SCAN_PORTS $default_place_scan_ports]
  if { !$place_scan_ports } {
    puts "DFT: skipping scan pin placement (DFT_PLACE_SCAN_PORTS=0)"
    return
  }

  # Ensure scan ports exist (post-floorplan hook should have created them,
  # but do this defensively).
  dft_ensure_scan_port "scan_enable_0" INPUT

  set plan ""
  with_output_to_variable plan { report_dft_plan -verbose }

  # OpenROAD stitches scan chains in lexicographic chain-name order, and uses
  # that ordinal to select scan_in_N/scan_out_N. Mirror that here so scan port
  # placement targets the correct chain endpoints.
  set chain_first_by_name [dict create]
  set chain_last_by_name [dict create]
  set current_chain_name ""
  set first_cell ""
  set last_cell ""

  foreach line [split $plan "\n"] {
    if { [regexp {^Scan chain '([^']+)'} $line -> chain_name] } {
      # Flush any previous chain.
      if { $current_chain_name != "" } {
        dict set chain_first_by_name $current_chain_name $first_cell
        dict set chain_last_by_name $current_chain_name $last_cell
      }
      set current_chain_name $chain_name
      set first_cell ""
      set last_cell ""
      continue
    }

    if { $current_chain_name != "" } {
      if { [regexp {^\s+([^\s]+)} $line -> token] } {
        set cell_name $token
        if { $first_cell == "" } {
          set first_cell $cell_name
        }
        set last_cell $cell_name
        continue
      }
    }
  }
  if { $current_chain_name != "" } {
    dict set chain_first_by_name $current_chain_name $first_cell
    dict set chain_last_by_name $current_chain_name $last_cell
  }

  # Place scan_in/out for each chain near its first/last scan cell.
  set chain_names [lsort -ascii [dict keys $chain_first_by_name]]
  set ordinal 0
  foreach chain_name $chain_names {
    set in_port "scan_in_$ordinal"
    set out_port "scan_out_$ordinal"
    dft_ensure_scan_port $in_port INPUT
    dft_ensure_scan_port $out_port OUTPUT

    set first [dict get $chain_first_by_name $chain_name]
    set last [dict get $chain_last_by_name $chain_name]

    if { $first != "" } {
      dft_place_pin_near_inst $in_port $first
    }
    if { $last != "" } {
      dft_place_pin_near_inst $out_port $last
    }
    incr ordinal
  }

  # Optional: place scan_enable too (default off; it is a large-fanout net and
  # re-placing it can be risky if the IO area is dense).
  set place_scan_enable [dft_get_env_bool DFT_PLACE_SCAN_ENABLE_PORT $place_scan_ports]
  if { $place_scan_enable } {
    set block [ord::get_db_block]
    set die_rect [$block getDieArea]
    set xMin [$die_rect xMin]
    set yMin [$die_rect yMin]
    set xMax [$die_rect xMax]
    set yMax [$die_rect yMax]
    set mid_x [expr {($xMin + $xMax) / 2}]
    set mid_y [expr {($yMin + $yMax) / 2}]

    set enable_layer [lindex $::env(IO_PLACER_V) 0]
    if { $enable_layer != "" } {
      dft_place_pin_no_overlap scan_enable_0 $enable_layer $mid_x $mid_y
    }
  }
}

proc dft_mark_scan_nets_dont_touch {} {
  # Skip QoR-driven optimization on scan-only nets (scan_enable/scan ports and
  # any SCAN-tagged nets created by stitching). These nets are disabled for
  # functional STA via set_case_analysis, so buffering/sizing for them can add
  # QoR overhead without improving functional timing.
  set dont_touch_scan [dft_get_env_bool DFT_DONT_TOUCH_SCAN_NETS 1]
  if { !$dont_touch_scan } {
    puts "DFT: leaving scan nets optimizable (DFT_DONT_TOUCH_SCAN_NETS=0)"
    return
  }

  set block [ord::get_db_block]
  if { $block == "NULL" } {
    puts "DFT: WARNING: no db block found; skipping scan dont_touch"
    return
  }

  set marked 0
  foreach net [$block getNets] {
    if { [$net getSigType] == "SCAN" } {
      $net setDoNotTouch true
      incr marked
    }
  }
  puts "DFT: marked $marked SCAN nets as dont_touch"
}

# Must match `flow/scripts/dft_scan_post_floorplan.tcl`.
set clock_mixing [dft_get_env DFT_CLOCK_MIXING "clock_mix"]
set max_length [dft_get_env DFT_MAX_CHAIN_LENGTH ""]
if { $max_length == "" } {
  set max_length [dft_get_env DFT_MAX_LENGTH ""]
}

set chain_count [dft_get_env DFT_CHAIN_COUNT ""]

set max_chains [dft_get_env DFT_MAX_CHAINS ""]
if { $chain_count == "" && $max_chains == "" && $max_length == "" } {
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
if { $chain_count != "" } {
  lappend dft_args -chain_count $chain_count
} elseif { $max_chains != "" } {
  lappend dft_args -max_chains $max_chains
}
set_dft_config {*}$dft_args

dft_place_scan_ports_from_plan

# Ensure functional-mode STA/power assumptions in this stage too.
set_case_analysis 0 [get_ports scan_enable_0]

execute_dft_plan

dft_mark_scan_nets_dont_touch
