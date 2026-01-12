# DFT scan insertion hook for ORFS.
#
# Intended use: set `PRE_GLOBAL_ROUTE_TCL` to this file.
#
# This runs after CTS, before global routing, so scan-chain connections are
# included in routing.

puts "DFT: pre-global-route scan hook"

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

proc dft_scan_enable_name {} {
  set pattern [dft_get_env DFT_SCAN_ENABLE_NAME_PATTERN "scan_enable_{}"]
  return [dft_apply_name_pattern $pattern 0]
}

proc dft_scan_in_name {ordinal} {
  set pattern [dft_get_env DFT_SCAN_IN_NAME_PATTERN "scan_in_{}"]
  return [dft_apply_name_pattern $pattern $ordinal]
}

proc dft_scan_out_name {ordinal} {
  set pattern [dft_get_env DFT_SCAN_OUT_NAME_PATTERN "scan_out_{}"]
  return [dft_apply_name_pattern $pattern $ordinal]
}

proc dft_set_scan_enable_case_analysis {} {
  set enable_name [dft_scan_enable_name]
  if { [catch {
    if { [dft_name_pattern_is_inst_pin $enable_name] } {
      set_case_analysis 0 [get_pins $enable_name]
    } else {
      set_case_analysis 0 [get_ports $enable_name]
    }
  } err] } {
    puts "DFT: WARNING: couldn't set_case_analysis on scan enable '$enable_name': $err"
  }
}

proc dft_split_inst_pin {inst_pin} {
  set idx [string last "/" $inst_pin]
  if { $idx < 0 } {
    return ""
  }
  if { $idx == 0 } {
    return ""
  }
  if { $idx == [expr {[string length $inst_pin] - 1}] } {
    return ""
  }
  set inst_name [string range $inst_pin 0 [expr {$idx - 1}]]
  set pin_name [string range $inst_pin [expr {$idx + 1}] end]
  return [list $inst_name $pin_name]
}

proc dft_resolve_endpoint_term {name io_type} {
  set block [ord::get_db_block]
  if { $block == "NULL" } {
    return "NULL"
  }

  if { [dft_name_pattern_is_inst_pin $name] } {
    set pair [dft_split_inst_pin $name]
    if { $pair == "" } {
      return "NULL"
    }
    lassign $pair inst_name pin_name
    set inst [$block findInst $inst_name]
    if { $inst == "NULL" } {
      return "NULL"
    }
    return [$inst findITerm $pin_name]
  }

  dft_ensure_scan_port $name $io_type
  return [$block findBTerm $name]
}

proc dft_scan_enable_net_name {} {
  set enable_term [dft_resolve_endpoint_term [dft_scan_enable_name] INPUT]
  if { $enable_term == "NULL" } {
    return ""
  }
  set net [$enable_term getNet]
  if { $net == "NULL" } {
    return ""
  }
  return [$net getName]
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

proc dft_pin_has_geometry {pin_name} {
  set block [ord::get_db_block]
  if { $block == "NULL" } {
    return 0
  }
  set bterm [$block findBTerm $pin_name]
  if { $bterm == "NULL" } {
    return 0
  }
  return [expr {[dft_get_primary_box $bterm] != ""}]
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

  # Try edges in ascending distance order. When IO pin density is high, the
  # closest edge may not have a legal non-overlapping slot near the projected
  # location, so fall back to the next-closest edge rather than forcing a large
  # shift along the boundary.
  #
  # - horizontal-track layers -> left/right edges
  # - vertical-track layers   -> top/bottom edges
  set candidates [list \
    [list $dist_left left] \
    [list $dist_right right] \
    [list $dist_bottom bottom] \
    [list $dist_top top] \
  ]
  set candidates [lsort -integer -index 0 $candidates]

  foreach cand $candidates {
    set edge [lindex $cand 1]
    if { $edge == "left" } {
      if { [dft_place_pin_no_overlap $pin_name $h_layer $xMin $y] } { return }
    } elseif { $edge == "right" } {
      if { [dft_place_pin_no_overlap $pin_name $h_layer $xMax $y] } { return }
    } elseif { $edge == "bottom" } {
      if { [dft_place_pin_no_overlap $pin_name $v_layer $x $yMin] } { return }
    } else {
      if { [dft_place_pin_no_overlap $pin_name $v_layer $x $yMax] } { return }
    }
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
  set place_scan_enable [dft_get_env_bool DFT_PLACE_SCAN_ENABLE_PORT $place_scan_ports]

  set enable_name [dft_scan_enable_name]
  set enable_is_port [expr {![dft_name_pattern_is_inst_pin $enable_name]}]
  set in0_name [dft_scan_in_name 0]
  set out0_name [dft_scan_out_name 0]
  set io_is_port [expr {![dft_name_pattern_is_inst_pin $in0_name] && ![dft_name_pattern_is_inst_pin $out0_name]}]

  if { $place_scan_ports && !$io_is_port } {
    puts "DFT: WARNING: DFT_PLACE_SCAN_PORTS requested but scan_in/out name patterns use instance/pin; skipping scan port placement"
    set place_scan_ports 0
  }
  if { $place_scan_enable && !$enable_is_port } {
    puts "DFT: WARNING: DFT_PLACE_SCAN_ENABLE_PORT requested but scan_enable name pattern uses instance/pin; skipping scan_enable placement"
    set place_scan_enable 0
  }

  # Ensure scan ports exist (post-floorplan hook should have created them,
  # but do this defensively).
  if { $enable_is_port } {
    dft_ensure_scan_port $enable_name INPUT
  }

  # Infer chain count (needed even when scan pin placement is disabled) so we
  # can ensure scan ports have valid geometry before routing.
  set chain_count 1
  with_output_to_variable dft_plan_str { report_dft_plan }
  if { ![regexp {Number of chains:\s*([0-9]+)} $dft_plan_str -> chain_count] } {
    puts "DFT: WARNING: couldn't parse chain count from report_dft_plan; defaulting to 1"
    set chain_count 1
  }

  # Detect misconfiguration where scan-in/out name patterns cannot generate
  # distinct endpoints for multiple chains (e.g. patterns without "{}").
  #
  # Note: OpenROAD `execute_dft_plan` can also take per-chain begin/end ports
  # from the constraints file (and will error on duplicate endpoints), so keep
  # this check limited to cases where ORFS does its own stitching.
  if { $chain_count > 1 && [dft_scan_solver] != "openroad" } {
    if { [dft_scan_in_name 0] == [dft_scan_in_name 1] } {
      error "DFT: multiple chains require distinct scan-in endpoints when DFT_SCAN_SOLVER!=openroad; set DFT_SCAN_IN_NAME_PATTERN with \"{}\""
    }
    if { [dft_scan_out_name 0] == [dft_scan_out_name 1] } {
      error "DFT: multiple chains require distinct scan-out endpoints when DFT_SCAN_SOLVER!=openroad; set DFT_SCAN_OUT_NAME_PATTERN with \"{}\""
    }
  }

  # Ensure scan ports exist and have at least one pin geometry box so global/
  # detailed route won't error out (e.g. GRT-0042). This is especially
  # important for footprint-based flows that skip `place_pins`.
  if { $enable_is_port && $io_is_port && [info exists ::env(IO_PLACER_H)] && [info exists ::env(IO_PLACER_V)] } {
    set h_layer [lindex $::env(IO_PLACER_H) 0]
    set v_layer [lindex $::env(IO_PLACER_V) 0]

    set block [ord::get_db_block]
    set die_rect [$block getDieArea]
    set xMin [$die_rect xMin]
    set yMin [$die_rect yMin]
    set xMax [$die_rect xMax]
    set yMax [$die_rect yMax]
    set mid_x [expr {($xMin + $xMax) / 2}]
    set span_y [expr {$yMax - $yMin}]

    if { ![dft_pin_has_geometry $enable_name] && $v_layer != "" } {
      dft_place_pin_no_overlap $enable_name $v_layer $mid_x $yMin
    }

    if { $chain_count < 1 } {
      set chain_count 1
    }
    for { set i 0 } { $i < $chain_count } { incr i } {
      set in_port [dft_scan_in_name $i]
      set out_port [dft_scan_out_name $i]
      dft_ensure_scan_port $in_port INPUT
      dft_ensure_scan_port $out_port OUTPUT

      # Spread pins along left/right edges by default.
      set y [expr {$yMin + (($i + 1) * $span_y) / ($chain_count + 1)}]
      if { ![dft_pin_has_geometry $in_port] && $h_layer != "" } {
        dft_place_pin_no_overlap $in_port $h_layer $xMin $y
      }
      if { ![dft_pin_has_geometry $out_port] && $h_layer != "" } {
        dft_place_pin_no_overlap $out_port $h_layer $xMax $y
      }
    }
  } else {
    if { $enable_is_port && $io_is_port } {
      puts "DFT: WARNING: IO_PLACER_H/V not set; scan ports may be missing geometries"
    }
  }

  if { !$place_scan_ports } {
    puts "DFT: leaving scan_in/out pin placement as-is (DFT_PLACE_SCAN_PORTS=0)"
  }

  if { !$place_scan_ports && !$place_scan_enable } {
    return
  }

  set plan ""
  with_output_to_variable plan { report_dft_plan -verbose }

  # Preserve the chain order as reported by OpenROAD. scan_in_N/scan_out_N
  # binding depends on the chain ordinal ordering in the plan.
  set chain_cells_by_name [dict create]
  set chain_names_in_order {}
  set current_chain_name ""
  set current_cells {}

  foreach line [split $plan "\n"] {
    if { [regexp {^Scan chain '([^']+)'} $line -> chain_name] } {
      # Flush any previous chain.
      if { $current_chain_name != "" } {
        dict set chain_cells_by_name $current_chain_name $current_cells
      }
      lappend chain_names_in_order $chain_name
      set current_chain_name $chain_name
      set current_cells {}
      continue
    }

    if { $current_chain_name != "" } {
      if { [regexp {^\s+([^\s]+)} $line -> token] } {
        lappend current_cells $token
        continue
      }
    }
  }
  if { $current_chain_name != "" } {
    dict set chain_cells_by_name $current_chain_name $current_cells
  }
  set ::dft_chain_names_in_order $chain_names_in_order

  set chain_order_by_name $chain_cells_by_name

  # Optional: override per-chain ordering with an external solver.
  set scan_solver [dft_scan_solver]
  set metric [string toupper [string trim [dft_get_env DFT_SCAN_ORDER_METRIC ""]]]
  if { $scan_solver == "scanopt_next" && $metric == "PIN_TO_NET" } {
    puts "DFT: WARNING: DFT_SCAN_SOLVER=scanopt_next doesn't support PIN_TO_NET; using OpenROAD order"
    set scan_solver "openroad"
  }
  if { $scan_solver == "order_file" } {
    set chain_order_by_name [dft_order_file_reorder $chain_cells_by_name]
  } elseif { $scan_solver == "scanopt_next" } {
    set chain_order_by_name [dft_scanopt_next_reorder $chain_cells_by_name "pregrt"]
  }

  # Cache for later stitching.
  set ::dft_chain_order_by_name $chain_order_by_name

  # Place scan_in/out for each chain near its first/last scan cell.
  if { $place_scan_ports } {
    set chain_names $chain_names_in_order
    if { [llength $chain_names] == 0 } {
      set chain_names [lsort -ascii [dict keys $chain_order_by_name]]
    }
    set ordinal 0
    foreach chain_name $chain_names {
      set in_port [dft_scan_in_name $ordinal]
      set out_port [dft_scan_out_name $ordinal]
      dft_ensure_scan_port $in_port INPUT
      dft_ensure_scan_port $out_port OUTPUT

      set cells [dict get $chain_order_by_name $chain_name]
      set first ""
      set last ""
      if { [llength $cells] > 0 } {
        set first [lindex $cells 0]
        set last [lindex $cells [expr {[llength $cells] - 1}]]
      }

      if { $first != "" } {
        dft_place_pin_near_inst $in_port $first
      }
      if { $last != "" } {
        dft_place_pin_near_inst $out_port $last
      }
      incr ordinal
    }
  }

  # Optional: place scan_enable too (default off; it is a large-fanout net and
  # re-placing it can be risky if the IO area is dense).
  if { $place_scan_enable } {
    set block [ord::get_db_block]
    set die_rect [$block getDieArea]
    set xMin [$die_rect xMin]
    set yMin [$die_rect yMin]
    set xMax [$die_rect xMax]
    set yMax [$die_rect yMax]
    set mid_x [expr {($xMin + $xMax) / 2}]
    set mid_y [expr {($yMin + $yMax) / 2}]

    set enable_layer ""
    if { [info exists ::env(IO_PLACER_V)] } {
      set enable_layer [lindex $::env(IO_PLACER_V) 0]
    }
    if { $enable_layer == "" && [info exists ::env(IO_PLACER_H)] } {
      set enable_layer [lindex $::env(IO_PLACER_H) 0]
    }
    if { $enable_layer != "" } {
      dft_place_pin_no_overlap $enable_name $enable_layer $mid_x $mid_y
    } else {
      puts "DFT: WARNING: IO_PLACER_H/V not set; skipping scan_enable placement"
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

  set enable_net_name [dft_scan_enable_net_name]
  set marked 0
  foreach net [$block getNets] {
    if { [$net getSigType] == "SCAN" } {
      # Keep scan_enable optimizable so repair_design/repair_timing can insert
      # buffering/splitting to handle the high fanout.
      set net_name [$net getName]
      if { ($enable_net_name != "" && $net_name == $enable_net_name) \
        || [string match "scan_enable_*" $net_name] \
        || [string match "dft_scan_enable_net*" $net_name] } {
        continue
      }
      $net setDoNotTouch true
      incr marked
    }
  }
  puts "DFT: marked $marked SCAN nets as dont_touch"
}

proc dft_compare_by_xy {a b} {
  set ax [lindex $a 1]
  set bx [lindex $b 1]
  if { $ax < $bx } {
    return -1
  }
  if { $ax > $bx } {
    return 1
  }
  set ay [lindex $a 2]
  set by [lindex $b 2]
  if { $ay < $by } {
    return -1
  }
  if { $ay > $by } {
    return 1
  }
  return [string compare [lindex $a 0] [lindex $b 0]]
}

proc dft_buffer_scan_enable_net {} {
  set enable_buffering [dft_get_env_bool DFT_BUFFER_SCAN_ENABLE 1]
  if { !$enable_buffering } {
    puts "DFT: skipping scan_enable buffering (DFT_BUFFER_SCAN_ENABLE=0)"
    return
  }

  if { ![info exists ::env(MIN_BUF_CELL_AND_PORTS)] || $::env(MIN_BUF_CELL_AND_PORTS) == "" } {
    puts "DFT: WARNING: MIN_BUF_CELL_AND_PORTS not set; can't buffer scan_enable"
    return
  }

  set default_buffer_cell [lindex $::env(MIN_BUF_CELL_AND_PORTS) 0]
  set buffer_cell [dft_get_env DFT_SCAN_ENABLE_BUFFER_CELL $default_buffer_cell]
  set max_fanout [dft_get_env DFT_SCAN_ENABLE_MAX_FANOUT 64]
  set max_levels [dft_get_env DFT_SCAN_ENABLE_BUFFER_LEVELS 3]

  set scan_enable_net_name [dft_scan_enable_net_name]
  if { $scan_enable_net_name == "" } {
    puts "DFT: WARNING: can't resolve scan_enable net name; skipping buffering"
    return
  }

  set block [ord::get_db_block]
  if { $block == "NULL" } {
    puts "DFT: WARNING: no db block found; skipping scan_enable buffering"
    return
  }

  set total_inserted 0

  for { set level 0 } { $level < $max_levels } { incr level } {
    set net [$block findNet $scan_enable_net_name]
    if { $net == "NULL" } {
      puts "DFT: WARNING: can't find net '$scan_enable_net_name' for buffering"
      return
    }

    # Collect load pins on the scan_enable net.
    set loads_with_locs {}
    foreach iterm [$net getITerms] {
      if { [$iterm getIoType] != "INPUT" } {
        continue
      }
      set inst [$iterm getInst]
      if { $inst == "NULL" } {
        continue
      }
      set mterm [$iterm getMTerm]
      if { $mterm == "NULL" } {
        continue
      }
      set inst_name [$inst getName]
      set pin_name [$mterm getName]
      lassign [$inst getLocation] x y
      lappend loads_with_locs [list "${inst_name}/${pin_name}" $x $y]
    }

    set fanout [llength $loads_with_locs]
    if { $fanout <= $max_fanout } {
      if { $level == 0 } {
        puts "DFT: scan_enable fanout=$fanout (<= $max_fanout); no buffering needed"
      } else {
        puts "DFT: scan_enable buffering complete at level $level (fanout=$fanout)"
      }
      break
    }

    set loads_with_locs [lsort -command dft_compare_by_xy $loads_with_locs]

    # Split the net into buffered groups.
    set idx 0
    while { $idx < $fanout } {
      set end [expr {$idx + $max_fanout - 1}]
      if { $end >= $fanout } {
        set end [expr {$fanout - 1}]
      }
      set group [lrange $loads_with_locs $idx $end]
      set idx [expr {$end + 1}]

      if { [llength $group] == 0 } {
        continue
      }

      set pins {}
      set sum_x 0
      set sum_y 0
      foreach item $group {
        lappend pins [lindex $item 0]
        set sum_x [expr {$sum_x + [lindex $item 1]}]
        set sum_y [expr {$sum_y + [lindex $item 2]}]
      }

      set count [llength $group]
      set cx [expr {round(double($sum_x) / $count)}]
      set cy [expr {round(double($sum_y) / $count)}]
      set x_um [ord::dbu_to_microns $cx]
      set y_um [ord::dbu_to_microns $cy]

      if { [catch {
        insert_buffer -buffer_cell $buffer_cell -net $scan_enable_net_name \
          -load_pins $pins -location [list $x_um $y_um] \
          -buffer_name "dft_scan_enable_buf" -net_name "dft_scan_enable_net"
      } err] } {
        puts "DFT: WARNING: insert_buffer failed while buffering scan_enable: $err"
        return
      }
      incr total_inserted
    }
  }

  if { $total_inserted > 0 } {
    puts "DFT: inserted $total_inserted buffer(s) for scan_enable"
    # Legalize any newly inserted buffers.
    catch { detailed_placement }
  }
}

proc dft_get_env_lower {name default_value} {
  return [string tolower [string trim [dft_get_env $name $default_value]]]
}

proc dft_scan_solver {} {
  set solver [dft_get_env_lower DFT_SCAN_SOLVER "openroad"]
  if { $solver == "" || $solver in {"openroad" "internal" "or"} } {
    return "openroad"
  }
  if { $solver in {"scanopt_next" "scanopt-next" "scanopt"} } {
    return "scanopt_next"
  }
  if { $solver in {"order_file" "order-file" "explicit" "file"} } {
    return "order_file"
  }
  puts "DFT: WARNING: unknown DFT_SCAN_SOLVER '$solver'; using openroad"
  return "openroad"
}

proc dft_parse_dft_plan_verbose_cells_by_chain {} {
  set plan ""
  if { [catch { with_output_to_variable plan { report_dft_plan -verbose } } err] } {
    puts "DFT: WARNING: report_dft_plan -verbose failed: $err"
    return [dict create]
  }

  # Preserve the chain order as reported by OpenROAD. Tcl dict key ordering is
  # not guaranteed, but scan_in_N/scan_out_N binding depends on the chain
  # ordinal ordering in the plan.
  set ::dft_chain_names_in_order {}

  set chain_cells_by_name [dict create]
  set current_chain_name ""
  set current_cells {}
  foreach line [split $plan "\n"] {
    if { [regexp {^Scan chain '([^']+)'} $line -> chain_name] } {
      if { $current_chain_name != "" } {
        dict set chain_cells_by_name $current_chain_name $current_cells
      }
      lappend ::dft_chain_names_in_order $chain_name
      set current_chain_name $chain_name
      set current_cells {}
      continue
    }
    if { $current_chain_name == "" } {
      continue
    }
    if { [regexp {^\s+([^\s]+)} $line -> token] } {
      lappend current_cells $token
    }
  }
  if { $current_chain_name != "" } {
    dict set chain_cells_by_name $current_chain_name $current_cells
  }
  return $chain_cells_by_name
}

proc dft_scanopt_next_reorder {chain_cells_by_name {tag "pregrt"}} {
  if { [dict size $chain_cells_by_name] == 0 } {
    return $chain_cells_by_name
  }

  set block [ord::get_db_block]
  if { $block == "NULL" } {
    puts "DFT: WARNING: no db block found; can't run scanopt_next"
    return $chain_cells_by_name
  }

  set out_dir "/tmp"
  if { [info exists ::env(REPORTS_DIR)] && $::env(REPORTS_DIR) != "" } {
    set out_dir $::env(REPORTS_DIR)
  }
  set stamp [clock milliseconds]
  set in_file [file join $out_dir "dft_scanopt_next_${tag}_${stamp}.tsv"]
  set out_file [file join $out_dir "dft_scanopt_next_${tag}_${stamp}.out"]

  set fh [open $in_file w]
  foreach chain_name [dict keys $chain_cells_by_name] {
    foreach inst_name [dict get $chain_cells_by_name $chain_name] {
      set inst [$block findInst $inst_name]
      if { $inst == "NULL" } {
        continue
      }
      lassign [$inst getLocation] x y
      puts $fh "${chain_name}\t${inst_name}\t${x}\t${y}"
    }
  }
  close $fh

  set solver_bin [dft_get_env DFT_SCAN_SOLVER_BIN ""]
  set seed [dft_get_env DFT_SCAN_SOLVER_SEED 0]
  set max_2opt [dft_get_env DFT_SCAN_SOLVER_MAX_2OPT_ITERS 20000]
  set disable_2opt [dft_get_env_bool DFT_SCAN_SOLVER_DISABLE_2OPT 0]
  set extra_args [dft_get_env DFT_SCAN_SOLVER_ARGS ""]

  if { $solver_bin != "" } {
    set cmd [list $solver_bin --input $in_file --output $out_file]
  } else {
    if { ![info exists ::env(PYTHON_EXE)] || $::env(PYTHON_EXE) == "" } {
      puts "DFT: WARNING: PYTHON_EXE not set; can't run scanopt_next"
      return $chain_cells_by_name
    }
    if { ![info exists ::env(UTILS_DIR)] || $::env(UTILS_DIR) == "" } {
      puts "DFT: WARNING: UTILS_DIR not set; can't run scanopt_next"
      return $chain_cells_by_name
    }
    set solver_script [file join $::env(UTILS_DIR) "scan_opt_next.py"]
    set cmd [list $::env(PYTHON_EXE) $solver_script --input $in_file --output $out_file]
  }
  lappend cmd --seed $seed --max-2opt-iters $max_2opt
  if { $disable_2opt } {
    lappend cmd --disable-2opt
  }
  if { $extra_args != "" } {
    lappend cmd {*}$extra_args
  }

  puts "DFT: scan solver (scanopt_next): $cmd"
  if { [catch { exec {*}$cmd } err] } {
    puts "DFT: WARNING: scanopt_next failed; falling back to OpenROAD order: $err"
    return $chain_cells_by_name
  }

  if { ![file exists $out_file] } {
    puts "DFT: WARNING: scanopt_next produced no output; falling back to OpenROAD order"
    return $chain_cells_by_name
  }

  set out [dict create]
  set out_fh [open $out_file r]
  while { [gets $out_fh line] >= 0 } {
    set line [string trim $line]
    if { $line == "" || [string match "#*" $line] } {
      continue
    }
    set fields [split $line]
    if { [llength $fields] < 2 } {
      continue
    }
    set chain_name [lindex $fields 0]
    set order [lrange $fields 1 end]
    dict set out $chain_name $order
  }
  close $out_fh

  # Validate output: per-chain membership must match input.
  foreach chain_name [dict keys $chain_cells_by_name] {
    if { ![dict exists $out $chain_name] } {
      puts "DFT: WARNING: scanopt_next missing chain '$chain_name'; falling back to OpenROAD order"
      return $chain_cells_by_name
    }
    set in_cells [dict get $chain_cells_by_name $chain_name]
    set out_cells [dict get $out $chain_name]

    if { [llength $in_cells] != [llength $out_cells] } {
      puts "DFT: WARNING: scanopt_next size mismatch for chain '$chain_name'; falling back"
      return $chain_cells_by_name
    }
    if { [llength $out_cells] != [llength [lsort -unique $out_cells]] } {
      puts "DFT: WARNING: scanopt_next returned duplicate cells for chain '$chain_name'; falling back"
      return $chain_cells_by_name
    }
    if { [lsort -ascii $in_cells] != [lsort -ascii $out_cells] } {
      puts "DFT: WARNING: scanopt_next membership mismatch for chain '$chain_name'; falling back"
      return $chain_cells_by_name
    }
  }

  return $out
}

proc dft_order_file_reorder {chain_cells_by_name} {
  if { [dict size $chain_cells_by_name] == 0 } {
    return $chain_cells_by_name
  }

  set order_file [dft_get_env DFT_SCAN_ORDER_FILE ""]
  if { $order_file == "" } {
    puts "DFT: WARNING: DFT_SCAN_SOLVER=order_file but DFT_SCAN_ORDER_FILE is not set; using OpenROAD order"
    return $chain_cells_by_name
  }
  if { ![file exists $order_file] } {
    puts "DFT: WARNING: DFT_SCAN_ORDER_FILE not found: $order_file; using OpenROAD order"
    return $chain_cells_by_name
  }

  set out [dict create]
  set fh [open $order_file r]
  while { [gets $fh line] >= 0 } {
    set line [string trim $line]
    if { $line == "" || [string match "#*" $line] } {
      continue
    }
    set fields [split $line]
    if { [llength $fields] < 2 } {
      continue
    }

    set first [lindex $fields 0]
    if { [dict size $chain_cells_by_name] == 1 && ![dict exists $chain_cells_by_name $first] } {
      # Single-chain shorthand: line is "inst0 inst1 inst2 ...".
      set chain_name [lindex [dict keys $chain_cells_by_name] 0]
      dict set out $chain_name $fields
      break
    }

    set chain_name $first
    set order [lrange $fields 1 end]
    dict set out $chain_name $order
  }
  close $fh

  # Validate output: per-chain membership must match input.
  foreach chain_name [dict keys $chain_cells_by_name] {
    if { ![dict exists $out $chain_name] } {
      puts "DFT: WARNING: order file missing chain '$chain_name'; using OpenROAD order"
      return $chain_cells_by_name
    }
    set in_cells [dict get $chain_cells_by_name $chain_name]
    set out_cells [dict get $out $chain_name]

    if { [llength $in_cells] != [llength $out_cells] } {
      puts "DFT: WARNING: order file size mismatch for chain '$chain_name'; using OpenROAD order"
      return $chain_cells_by_name
    }
    if { [llength $out_cells] != [llength [lsort -unique $out_cells]] } {
      puts "DFT: WARNING: order file returned duplicate cells for chain '$chain_name'; using OpenROAD order"
      return $chain_cells_by_name
    }
    if { [lsort -ascii $in_cells] != [lsort -ascii $out_cells] } {
      puts "DFT: WARNING: order file membership mismatch for chain '$chain_name'; using OpenROAD order"
      return $chain_cells_by_name
    }
  }

  return $out
}

proc dft_scan_find_iterm_by_candidates {inst candidate_names} {
  foreach pin $candidate_names {
    set iterm [$inst findITerm $pin]
    if { $iterm != "NULL" } {
      return $iterm
    }
  }
  return "NULL"
}

proc dft_scan_get_enable_iterm {inst} {
  set candidates [list SE SCE SCAN_EN SCAN_ENABLE SCANENABLE TE]
  return [dft_scan_find_iterm_by_candidates $inst $candidates]
}

proc dft_scan_get_in_iterm {inst} {
  set candidates [list SI SD SCD SCAN_IN SCANIN]
  return [dft_scan_find_iterm_by_candidates $inst $candidates]
}

proc dft_scan_get_out_iterm {inst} {
  # Prefer explicit scan-out pins; fall back to Q if necessary.
  set candidates [list SO SCO SCAN_OUT SCANOUT Q]
  return [dft_scan_find_iterm_by_candidates $inst $candidates]
}

proc dft_scan_term_connect_preserve {driver load} {
  set driver_net [$driver getNet]
  if { $driver_net != "NULL" && $driver_net != "" } {
    $load connect $driver_net
    return $driver_net
  }

  set load_net [$load getNet]
  if { $load_net != "NULL" && $load_net != "" } {
    $driver connect $load_net
    return $load_net
  }

  set block [ord::get_db_block]
  set net_name [$driver getName]
  set net [$block findNet $net_name]
  if { $net == "NULL" } {
    set net [odb::dbNet_create $block $net_name]
  }
  $net setSigType SCAN
  $driver connect $net
  $load connect $net
  return $net
}

proc dft_scan_stitch_from_order {chain_order_by_name} {
  set block [ord::get_db_block]
  if { $block == "NULL" } {
    puts "DFT: WARNING: no db block found; can't stitch scan chains"
    return
  }

  set chain_names {}
  if { [info exists ::dft_chain_names_in_order] && [llength $::dft_chain_names_in_order] > 0 } {
    set chain_names $::dft_chain_names_in_order
  } else {
    set chain_names [lsort -ascii [dict keys $chain_order_by_name]]
  }
  if { [llength $chain_names] == 0 } {
    puts "DFT: WARNING: no scan chains found; skipping stitching"
    return
  }

  # Resolve scan_enable endpoint (port or instance/pin).
  set enable_name [dft_scan_enable_name]
  set scan_enable_term [dft_resolve_endpoint_term $enable_name INPUT]
  if { $scan_enable_term == "NULL" } {
    error "DFT: missing scan_enable endpoint '$enable_name'; can't stitch scan chains"
  }

  # Connect scan_enable to all scan cells in the plan.
  set enable_connected 0
  foreach chain_name $chain_names {
    foreach inst_name [dict get $chain_order_by_name $chain_name] {
      set inst [$block findInst $inst_name]
      if { $inst == "NULL" } {
        continue
      }
      set se [dft_scan_get_enable_iterm $inst]
      if { $se == "NULL" } {
        puts "DFT: WARNING: can't find scan enable pin on '$inst_name'"
        continue
      }
      dft_scan_term_connect_preserve $scan_enable_term $se
      incr enable_connected
    }
  }
  puts "DFT: connected scan_enable to $enable_connected scan flops"

  # Stitch each chain (scan_in -> first -> ... -> last -> scan_out).
  set ordinal 0
  foreach chain_name $chain_names {
    set cells [dict get $chain_order_by_name $chain_name]
    set n [llength $cells]
    if { $n == 0 } {
      incr ordinal
      continue
    }

    set in_name [dft_scan_in_name $ordinal]
    set out_name [dft_scan_out_name $ordinal]
    set in_term [dft_resolve_endpoint_term $in_name INPUT]
    set out_term [dft_resolve_endpoint_term $out_name OUTPUT]
    if { $in_term == "NULL" || $out_term == "NULL" } {
      error "DFT: missing scan endpoints for chain ordinal $ordinal; can't stitch chain '$chain_name' ($in_name/$out_name)"
    }

    # Head: scan_in -> first.SI
    set first_name [lindex $cells 0]
    set first_inst [$block findInst $first_name]
    if { $first_inst == "NULL" } {
      puts "DFT: WARNING: missing first instance '$first_name' for chain '$chain_name'"
      incr ordinal
      continue
    }
    set first_si [dft_scan_get_in_iterm $first_inst]
    if { $first_si == "NULL" } {
      puts "DFT: WARNING: can't find scan in pin on '$first_name'"
      incr ordinal
      continue
    }
    dft_scan_term_connect_preserve $in_term $first_si

    # Internal links.
    for { set i 0 } { $i < ($n - 1) } { incr i } {
      set a_name [lindex $cells $i]
      set b_name [lindex $cells [expr {$i + 1}]]
      set a_inst [$block findInst $a_name]
      set b_inst [$block findInst $b_name]
      if { $a_inst == "NULL" || $b_inst == "NULL" } {
        continue
      }
      set a_out [dft_scan_get_out_iterm $a_inst]
      set b_in [dft_scan_get_in_iterm $b_inst]
      if { $a_out == "NULL" || $b_in == "NULL" } {
        puts "DFT: WARNING: can't find scan pins for link '$a_name' -> '$b_name'"
        continue
      }
      dft_scan_term_connect_preserve $a_out $b_in
    }

    # Tail: last.OUT -> scan_out
    set last_name [lindex $cells [expr {$n - 1}]]
    set last_inst [$block findInst $last_name]
    if { $last_inst != "NULL" } {
      set last_out [dft_scan_get_out_iterm $last_inst]
      if { $last_out != "NULL" } {
        dft_scan_term_connect_preserve $last_out $out_term
      } else {
        puts "DFT: WARNING: can't find scan out pin on '$last_name'"
      }
    }

    incr ordinal
  }
}

proc dft_scan_write_scandef {chain_order_by_name out_path} {
  set block [ord::get_db_block]
  if { $block == "NULL" } {
    error "DFT: can't write SCANDEF: no db block found"
  }

  set fh [open $out_path w]
  puts $fh "VERSION 5.8 ;"
  puts $fh "DIVIDERCHAR \"/\" ;"
  puts $fh {BUSBITCHARS "[]" ;}
  puts $fh "DESIGN [$block getName] ;"
  puts $fh ""

  set chain_names {}
  if { [info exists ::dft_chain_names_in_order] && [llength $::dft_chain_names_in_order] > 0 } {
    set chain_names $::dft_chain_names_in_order
  } else {
    set chain_names [lsort -ascii [dict keys $chain_order_by_name]]
  }
  puts $fh "SCANCHAINS [llength $chain_names] ;"
  puts $fh ""

  set ordinal 0
  foreach chain_name $chain_names {
    puts $fh "- $chain_name"

    set in_name [dft_scan_in_name $ordinal]
    set out_name [dft_scan_out_name $ordinal]
    dft_scan_write_scandef_endpoint $fh START $in_name 0

    puts $fh "+ ORDERED"
    foreach inst_name [dict get $chain_order_by_name $chain_name] {
      set inst [$block findInst $inst_name]
      if { $inst == "NULL" } {
        continue
      }
      set si [dft_scan_get_in_iterm $inst]
      set so [dft_scan_get_out_iterm $inst]
      if { $si == "NULL" || $so == "NULL" } {
        puts "DFT: WARNING: can't find scan pins for scandef line '$inst_name'"
        continue
      }
      set si_name [[$si getMTerm] getName]
      set so_name [[$so getMTerm] getName]
      puts $fh "  $inst_name ( IN $si_name ) ( OUT $so_name )"
    }

    puts $fh "+ PARTITION default"
    dft_scan_write_scandef_endpoint $fh STOP $out_name 1
    puts $fh ""
    incr ordinal
  }

  puts $fh "END SCANCHAINS"
  puts $fh ""
  puts $fh "END DESIGN"
  close $fh
}

proc dft_scan_write_scandef_endpoint {fh keyword endpoint_name add_semicolon} {
  if { [string first "/" $endpoint_name] >= 0 } {
    set idx [string last "/" $endpoint_name]
    set inst_name [string range $endpoint_name 0 [expr {$idx - 1}]]
    set pin_name [string range $endpoint_name [expr {$idx + 1}] end]
    puts -nonewline $fh "+ $keyword $inst_name $pin_name"
  } else {
    puts -nonewline $fh "+ $keyword PIN $endpoint_name"
  }
  if { $add_semicolon } {
    puts $fh " ;"
  } else {
    puts $fh ""
  }
}

proc dft_scan_store_scan_chains_in_odb {chain_order_by_name {tag "pregrt"}} {
  if { ![info exists ::env(RESULTS_DIR)] } {
    puts "DFT: WARNING: RESULTS_DIR not set; can't store scan chains for export"
    return
  }

  set out_path [file join $::env(RESULTS_DIR) "dft_scan_${tag}.scandef"]
  dft_scan_write_scandef $chain_order_by_name $out_path

  # Import the generated SCANDEF to populate OpenDB's DFT database so later
  # stages (e.g., final_report) can export scan chains via `write_scandef`.
  read_def -incremental $out_path
  puts "DFT: stored scan chains in OpenDB via '$out_path'"
}

proc dft_scan_get_chain_order_by_name {{tag "pregrt"}} {
  # Cached by dft_place_scan_ports_from_plan when scan port placement runs.
  if { [info exists ::dft_chain_order_by_name] && [dict size $::dft_chain_order_by_name] > 0 } {
    return $::dft_chain_order_by_name
  }

  set chain_cells_by_name [dft_parse_dft_plan_verbose_cells_by_chain]
  if { [dict size $chain_cells_by_name] == 0 } {
    return $chain_cells_by_name
  }

  set solver [dft_scan_solver]
  set metric [string toupper [string trim [dft_get_env DFT_SCAN_ORDER_METRIC ""]]]
  if { $solver == "scanopt_next" && $metric == "PIN_TO_NET" } {
    puts "DFT: WARNING: DFT_SCAN_SOLVER=scanopt_next doesn't support PIN_TO_NET; using OpenROAD order"
    set solver "openroad"
  }

  if { $solver == "order_file" } {
    set chain_cells_by_name [dft_order_file_reorder $chain_cells_by_name]
  } elseif { $solver == "scanopt_next" } {
    set chain_cells_by_name [dft_scanopt_next_reorder $chain_cells_by_name $tag]
  }

  set ::dft_chain_order_by_name $chain_cells_by_name
  return $chain_cells_by_name
}

proc dft_build_dft_config_args {{clock_mixing_override ""}} {
  # Must match `flow/scripts/dft_scan_post_floorplan.tcl`.
  set clock_mixing [dft_get_env DFT_CLOCK_MIXING "no_mix"]
  if { $clock_mixing_override != "" } {
    set clock_mixing $clock_mixing_override
  }

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

  return $dft_args
}

proc dft_apply_dft_config {{clock_mixing_override ""}} {
  set args [dft_build_dft_config_args $clock_mixing_override]
  set_dft_config {*}$args
}

proc dft_check_scan_clock_mixing_policy {} {
  # OpenROAD DFT can generate mixed-clock/edge chains in clock_mix mode. That
  # implies lockup insertion between domains during stitching; if lockup cells
  # are not configured, stitching will fail. Make this visible (or fatal) based
  # on a user policy (with AUTO fallback to no_mix).
  set policy [dft_get_env_lower DFT_LOCKUP_POLICY "auto"]
  if { $policy in {"0" "off" "false" "no"} } {
    return {}
  }

  set clock_mixing [dft_get_env_lower DFT_CLOCK_MIXING "no_mix"]

  set plan ""
  if { [catch { with_output_to_variable plan { report_dft_plan -verbose } } err] } {
    puts "DFT: WARNING: report_dft_plan -verbose failed; can't check clock mixing: $err"
    return {}
  }

  set current_chain ""
  set cur_clock ""
  set cur_edge ""
  set domains [dict create]

  foreach line [split $plan "\n"] {
    if { [regexp {^Scan chain '([^']+)'} $line -> chain_name] } {
      set current_chain $chain_name
      set cur_clock ""
      set cur_edge ""
      continue
    }
    if { $current_chain == "" } {
      continue
    }
    if { [regexp {^\s+([^\s]+)(?:\s+\(([^,]+),\s*([^)]+)\))?} $line -> cell clock edge] } {
      if { $clock != "" && $edge != "" } {
        set cur_clock [string trim $clock]
        set cur_edge [string trim $edge]
      }
      if { $cur_clock != "" && $cur_edge != "" } {
        # In `no_mix`, edge polarity is allowed within-chain (handled by
        # OpenROAD's "mid" polarity ordering). Only mixing different clock
        # *names* is a violation. In `clock_mix`, treat clock+edge together for
        # lockup policy checks.
        if { $clock_mixing == "no_mix" } {
          dict set domains $current_chain $cur_clock 1
        } else {
          dict set domains $current_chain "${cur_clock}/${cur_edge}" 1
        }
      }
    }
  }

  set mixed_chains {}
  foreach chain_name [dict keys $domains] {
    set chain_domains [dict get $domains $chain_name]
    if { [llength [dict keys $chain_domains]] > 1 } {
      lappend mixed_chains $chain_name
    }
  }

  if { [llength $mixed_chains] == 0 } {
    return {}
  }

  if { $clock_mixing == "no_mix" } {
    error "DFT: clock mixing violation: DFT_CLOCK_MIXING=no_mix but chains are mixed: $mixed_chains"
  }

  set msg "DFT: clock_mix produced mixed-clock/edge chains ($mixed_chains); lockup elements are required. Configure lockup cells (set_dft_config -lockup_*) or use DFT_CLOCK_MIXING=no_mix."
  if { $policy in {"error" "fatal"} } {
    error $msg
  }
  if { $policy != "auto" } {
    puts "DFT: WARNING: $msg"
  }
  return $mixed_chains
}

proc dft_stitch_scan_chains {{tag "pregrt"}} {
  set policy [dft_get_env_lower DFT_LOCKUP_POLICY "auto"]
  set mixed_chains [dft_check_scan_clock_mixing_policy]
  if { $policy == "auto" && [llength $mixed_chains] > 0 } {
    puts "DFT: AUTO: mixed-clock/edge chains detected ($mixed_chains); re-running with DFT_CLOCK_MIXING=no_mix"
    dft_apply_dft_config "no_mix"
    catch { unset ::dft_chain_order_by_name }
    dft_place_scan_ports_from_plan
    set mixed_chains [dft_check_scan_clock_mixing_policy]
  }

  set solver [dft_scan_solver]
  set metric [string toupper [string trim [dft_get_env DFT_SCAN_ORDER_METRIC ""]]]
  if { $solver == "scanopt_next" && $metric == "PIN_TO_NET" } {
    puts "DFT: WARNING: DFT_SCAN_SOLVER=scanopt_next doesn't support PIN_TO_NET; using execute_dft_plan"
    set solver "openroad"
  }

  if { $solver == "openroad" } {
    execute_dft_plan
    return
  }

  set chain_order_by_name [dft_scan_get_chain_order_by_name $tag]
  dft_scan_stitch_from_order $chain_order_by_name
  dft_scan_store_scan_chains_in_odb $chain_order_by_name $tag
}

dft_apply_dft_config

dft_place_scan_ports_from_plan

# Ensure functional-mode STA/power assumptions in this stage too.
dft_set_scan_enable_case_analysis

set defer_stitch [dft_get_env_bool DFT_DEFER_STITCH 0]
if { $defer_stitch } {
  puts "DFT: deferring execute_dft_plan (DFT_DEFER_STITCH=1)"
} else {
  puts "DFT: stitch scan chains"
  dft_stitch_scan_chains "pregrt"
  dft_buffer_scan_enable_net
  dft_mark_scan_nets_dont_touch
  # QoR proxy: placement-based scan chain cost (prints + emits metrics).
  catch {
    source [file join [file dirname [info script]] dft_scan_chain_cost.tcl]
    dft_report_scan_chain_cost "pregrt"
  }
}
