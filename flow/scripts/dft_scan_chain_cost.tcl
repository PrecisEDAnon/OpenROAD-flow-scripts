# DFT scan chain cost reporting for ORFS.
#
# Computes a placement-based Manhattan cost for the stitched scan chain(s),
# including endpoint-to-first and last-to-end terms when available.
#
# This is safe to source in any stage; it no-ops if no scan chains exist.

proc dft_get_env {name default_value} {
  if { [info exists ::env($name)] && $::env($name) != "" } {
    return $::env($name)
  }
  return $default_value
}

proc dft_get_env_bool {name default_value} {
  if { [info exists ::env($name)] && $::env($name) != "" } {
    set raw $::env($name)
  } else {
    set raw $default_value
  }

  set v [string tolower [string trim "$raw"]]
  if { $v in {"1" "true" "yes" "y" "on"} } {
    return 1
  }
  if { $v in {"0" "false" "no" "n" "off"} } {
    return 0
  }
  if { ![catch {expr {$v != 0}} as_bool] } {
    return $as_bool
  }
  return $default_value
}

proc dft_manhattan_dbu {x1 y1 x2 y2} {
  return [expr {abs($x1 - $x2) + abs($y1 - $y2)}]
}

proc dft_split_inst_pin {inst_pin} {
  set idx [string last "/" $inst_pin]
  if { $idx <= 0 } {
    return ""
  }
  if { $idx == [expr {[string length $inst_pin] - 1}] } {
    return ""
  }
  set inst_name [string range $inst_pin 0 [expr {$idx - 1}]]
  set pin_name [string range $inst_pin [expr {$idx + 1}] end]
  return [list $inst_name $pin_name]
}

proc dft_get_bterm_xy {bterm} {
  if { $bterm == "NULL" } {
    return ""
  }
  if { [catch { lassign [$bterm getFirstPinLocation] ok x y } err] } {
    return ""
  }
  if { !$ok } {
    return ""
  }
  return [list $x $y]
}

proc dft_get_iterm_xy {iterm} {
  if { $iterm == "NULL" } {
    return ""
  }
  if { [catch { lassign [$iterm getAvgXY] ok x y } err] } {
    return ""
  }
  if { !$ok } {
    return ""
  }
  return [list $x $y]
}

proc dft_get_term_xy {term_name} {
  set block [ord::get_db_block]
  if { $block == "NULL" } {
    return ""
  }

  if { [string first "/" $term_name] >= 0 } {
    set pair [dft_split_inst_pin $term_name]
    if { $pair == "" } {
      return ""
    }
    lassign $pair inst_name pin_name
    set inst [$block findInst $inst_name]
    if { $inst == "NULL" } {
      return ""
    }
    set iterm [$inst findITerm $pin_name]
    set xy [dft_get_iterm_xy $iterm]
    if { $xy != "" } {
      return $xy
    }
    # Fallback: instance location.
    return [$inst getLocation]
  }

  set bterm [$block findBTerm $term_name]
  set xy [dft_get_bterm_xy $bterm]
  if { $xy != "" } {
    return $xy
  }

  return ""
}

proc dft_find_iterm_by_candidates {inst candidate_names} {
  foreach pin $candidate_names {
    set iterm [$inst findITerm $pin]
    if { $iterm != "NULL" } {
      return $iterm
    }
  }
  return "NULL"
}

proc dft_scan_get_in_iterm {inst} {
  set candidates [list SI SD SCD SCAN_IN SCANIN]
  return [dft_find_iterm_by_candidates $inst $candidates]
}

proc dft_scan_get_out_iterm {inst} {
  # Prefer explicit scan-out pins; fall back to Q/QN if necessary.
  set candidates [list SO SCO SCAN_OUT SCANOUT Q QN Q_N]
  return [dft_find_iterm_by_candidates $inst $candidates]
}

proc dft_scan_pin_xy {inst_name pin_kind} {
  set block [ord::get_db_block]
  if { $block == "NULL" } {
    return ""
  }
  set inst [$block findInst $inst_name]
  if { $inst == "NULL" } {
    return ""
  }

  if { $pin_kind == "in" } {
    set iterm [dft_scan_get_in_iterm $inst]
  } else {
    set iterm [dft_scan_get_out_iterm $inst]
  }

  set xy [dft_get_iterm_xy $iterm]
  if { $xy != "" } {
    return $xy
  }
  return [$inst getLocation]
}

proc dft_write_tmp_scandef {tag} {
  if { [info commands write_scandef] == "" } {
    return ""
  }
  set out_dir "/tmp"
  if { [info exists ::env(REPORTS_DIR)] && $::env(REPORTS_DIR) != "" } {
    set out_dir $::env(REPORTS_DIR)
  }
  set stamp [clock milliseconds]
  set out_path [file join $out_dir "dft_scan_chain_cost_${tag}_${stamp}.scandef"]
  if { [catch { write_scandef -file $out_path } err] } {
    catch { file delete -force $out_path }
    return ""
  }
  return $out_path
}

proc dft_parse_scandef_chains {path} {
  set out [dict create]
  if { $path == "" || ![file exists $path] } {
    return $out
  }

  set fh [open $path r]
  set current ""
  set in_ordered 0
  while { [gets $fh raw] >= 0 } {
    set line [string trim $raw]
    if { $line == "" } {
      continue
    }
    if { [string match "- *" $line] } {
      set name [string trim [string range $line 1 end]]
      set current $name
      dict set out $current [dict create cells {} start "" stop ""]
      set in_ordered 0
      continue
    }
    if { $current == "" } {
      continue
    }

    if { [regexp {^\+\s+ORDERED} $line] } {
      set in_ordered 1
      continue
    }
    if { [regexp {^\+\s+PARTITION} $line] } {
      set in_ordered 0
      continue
    }

    if { [regexp {^\+\s+START\s+PIN\s+(\S+)} $line -> port] } {
      dict set out $current start [string trimright $port ";"]
      continue
    }
    if { [regexp {^\+\s+START\s+(\S+)\s+(\S+)} $line -> inst pin] } {
      dict set out $current start "${inst}/${pin}"
      continue
    }
    if { [regexp {^\+\s+STOP\s+PIN\s+(\S+)} $line -> port] } {
      dict set out $current stop [string trimright $port ";"]
      continue
    }
    if { [regexp {^\+\s+STOP\s+(\S+)\s+(\S+)} $line -> inst pin] } {
      dict set out $current stop "${inst}/${pin}"
      continue
    }

    if { $in_ordered } {
      set inst_name [lindex $line 0]
      if { $inst_name != "" } {
        dict lappend out $current cells $inst_name
      }
    }
  }
  close $fh
  return $out
}

proc dft_parse_report_dft_plan_verbose_chains {} {
  set out [dict create]
  set plan ""
  if { [catch { with_output_to_variable plan { report_dft_plan -verbose } } err] } {
    return $out
  }
  set current ""
  foreach line [split $plan "\n"] {
    if { [regexp {^Scan chain '([^']+)'} $line -> chain_name] } {
      set current $chain_name
      dict set out $current [dict create cells {} start "" stop ""]
      continue
    }
    if { $current == "" } {
      continue
    }
    if { [regexp {^\s+([^\s]+)} $line -> token] } {
      dict lappend out $current cells $token
    }
  }
  return $out
}

proc dft_report_scan_chain_cost {{tag "final"}} {
  # Align enablement with scan wirelength reporting (both are "scan QoR" aids).
  set enable [dft_get_env_bool DFT_REPORT_SCAN_WIRELENGTH 1]
  if { !$enable } {
    return
  }

  if { [ord::get_db_block] == "NULL" } {
    return
  }

  set tmp_scandef [dft_write_tmp_scandef $tag]
  set chains [dft_parse_scandef_chains $tmp_scandef]
  if { $chains == "" || [dict size $chains] == 0 } {
    set chains [dft_parse_report_dft_plan_verbose_chains]
  }
  if { $tmp_scandef != "" } {
    catch { file delete -force $tmp_scandef }
  }
  if { [dict size $chains] == 0 } {
    return
  }

  set report_dir ""
  if { [info exists ::env(REPORTS_DIR)] } {
    set report_dir $::env(REPORTS_DIR)
  }
  set out_path ""
  if { $report_dir != "" } {
    set out_path "$report_dir/dft_scan_chain_cost_${tag}.rpt"
  }

  set total_cells 0
  set min_cells -1
  set max_cells 0

  set total_cost_dbu 0
  set global_max_step_dbu 0

  set per_chain_rows {}

  foreach chain_name [dict keys $chains] {
    set cells [dict get $chains $chain_name cells]
    set n [llength $cells]
    if { $n == 0 } {
      continue
    }

    set total_cells [expr {$total_cells + $n}]
    if { $min_cells < 0 || $n < $min_cells } {
      set min_cells $n
    }
    if { $n > $max_cells } {
      set max_cells $n
    }

    set chain_cost_dbu 0
    set chain_max_step_dbu 0

    set start_term [dict get $chains $chain_name start]
    set stop_term [dict get $chains $chain_name stop]

    if { $start_term != "" } {
      set p0 [dft_get_term_xy $start_term]
      if { $p0 != "" } {
        lassign $p0 sx sy
        set p1 [dft_scan_pin_xy [lindex $cells 0] in]
        if { $p1 != "" } {
          lassign $p1 x y
          set d [dft_manhattan_dbu $sx $sy $x $y]
          set chain_cost_dbu [expr {$chain_cost_dbu + $d}]
          if { $d > $chain_max_step_dbu } {
            set chain_max_step_dbu $d
          }
        }
      }
    }

    for { set i 0 } { $i < [expr {$n - 1}] } { incr i } {
      set a [lindex $cells $i]
      set b [lindex $cells [expr {$i + 1}]]
      set pa [dft_scan_pin_xy $a out]
      set pb [dft_scan_pin_xy $b in]
      if { $pa == "" || $pb == "" } {
        continue
      }
      lassign $pa ax ay
      lassign $pb bx by
      set d [dft_manhattan_dbu $ax $ay $bx $by]
      set chain_cost_dbu [expr {$chain_cost_dbu + $d}]
      if { $d > $chain_max_step_dbu } {
        set chain_max_step_dbu $d
      }
    }

    if { $stop_term != "" } {
      set pn [dft_get_term_xy $stop_term]
      if { $pn != "" } {
        lassign $pn ex ey
        set plast [dft_scan_pin_xy [lindex $cells end] out]
        if { $plast != "" } {
          lassign $plast lx ly
          set d [dft_manhattan_dbu $lx $ly $ex $ey]
          set chain_cost_dbu [expr {$chain_cost_dbu + $d}]
          if { $d > $chain_max_step_dbu } {
            set chain_max_step_dbu $d
          }
        }
      }
    }

    set total_cost_dbu [expr {$total_cost_dbu + $chain_cost_dbu}]
    if { $chain_max_step_dbu > $global_max_step_dbu } {
      set global_max_step_dbu $chain_max_step_dbu
    }

    lappend per_chain_rows [list $chain_name $n $chain_cost_dbu $chain_max_step_dbu]
  }

  if { $total_cells == 0 } {
    return
  }

  set chain_count [llength $per_chain_rows]
  set total_cost_um [ord::dbu_to_microns $total_cost_dbu]
  set max_step_um [ord::dbu_to_microns $global_max_step_dbu]

  set imbalance_percent 0.0
  if { $chain_count > 1 && $min_cells > 0 } {
    set ratio [expr {double($max_cells) / double($min_cells)}]
    set imbalance_percent [expr {100.0 * ($ratio - 1.0)}]
  }

  utl::metric_int "dft_scan_chain_count" $chain_count
  utl::metric_int "dft_scan_chain_cells_total" $total_cells
  utl::metric_float "dft_scan_chain_cost_dbu" $total_cost_dbu
  utl::metric_float "dft_scan_chain_cost_um" $total_cost_um
  utl::metric_float "dft_scan_chain_max_step_um" $max_step_um
  utl::metric_float "dft_scan_chain_imbalance_percent" $imbalance_percent

  if { $out_path != "" } {
    catch { file delete -force $out_path }
    set fh [open $out_path w]
    puts $fh "# Scan chain cost (placement proxy): Manhattan(SO->SI) + endpoints"
    puts $fh "# tag=$tag"
    puts $fh "# total_cost_um=$total_cost_um"
    puts $fh "# chain_count=$chain_count total_cells=$total_cells min_cells=$min_cells max_cells=$max_cells imbalance_percent=$imbalance_percent"
    puts $fh "#"
    puts $fh "# chain_name cells cost_um max_step_um"
    foreach row [lsort -index 0 -ascii $per_chain_rows] {
      lassign $row chain_name n cost_dbu max_step_dbu
      puts $fh "$chain_name $n [ord::dbu_to_microns $cost_dbu] [ord::dbu_to_microns $max_step_dbu]"
    }
    close $fh
    puts "DFT: scan chain cost report: $out_path"
  }

  puts "DFT: scan chain cost ($tag): total=${total_cost_um}um, chains=$chain_count, imbalance=${imbalance_percent}%, max_step=${max_step_um}um"
}

