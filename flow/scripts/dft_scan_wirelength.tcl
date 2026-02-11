# DFT scan wirelength reporting for ORFS.
#
# Computes routed wirelength metrics for scan-related nets and writes a small
# report under $REPORTS_DIR.
#
# This is safe to source in any stage; it no-ops if no scan ports/nets exist.

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

proc dft_name_pattern_is_inst_pin {name_or_pattern} {
  return [expr {[string first "/" $name_or_pattern] >= 0}]
}

proc dft_name_pattern_to_glob {pattern} {
  # Supports one optional "{}" placeholder.
  return [string map [list "{}" "*"] $pattern]
}

proc dft_scan_enable_glob {} {
  set pattern [dft_get_env DFT_SCAN_ENABLE_NAME_PATTERN "scan_enable_{}"]
  if { [dft_name_pattern_is_inst_pin $pattern] } {
    return ""
  }
  return [dft_name_pattern_to_glob $pattern]
}

proc dft_scan_in_glob {} {
  set pattern [dft_get_env DFT_SCAN_IN_NAME_PATTERN "scan_in_{}"]
  if { [dft_name_pattern_is_inst_pin $pattern] } {
    return ""
  }
  return [dft_name_pattern_to_glob $pattern]
}

proc dft_scan_out_glob {} {
  set pattern [dft_get_env DFT_SCAN_OUT_NAME_PATTERN "scan_out_{}"]
  if { [dft_name_pattern_is_inst_pin $pattern] } {
    return ""
  }
  return [dft_name_pattern_to_glob $pattern]
}

proc dft_scan_design_present {} {
  if { [ord::get_db_block] == "NULL" } {
    return 0
  }
  set block [ord::get_db_block]
  set enable_glob [dft_scan_enable_glob]
  if { $enable_glob != "" } {
    foreach bterm [$block getBTerms] {
      if { [string match $enable_glob [$bterm getName]] } {
        return 1
      }
    }
  }
  # Fallback: any SCAN nets.
  foreach net [$block getNets] {
    if { [$net getSigType] == "SCAN" } {
      return 1
    }
  }
  return 0
}

proc dft_collect_scan_nets_by_sigtype {} {
  set block [ord::get_db_block]
  set scan_nets {}
  foreach net [$block getNets] {
    if { [$net getSigType] == "SCAN" } {
      # Some DFT flows pre-create scan I/O nets/ports, and OpenROAD may
      # re-attach the port to a different net during stitching, leaving the
      # original SCAN net empty. Skip such nets to avoid noisy reports.
      if { [llength [$net getBTerms]] == 0 && [llength [$net getITerms]] == 0 } {
        continue
      }
      lappend scan_nets [$net getName]
    }
  }
  return $scan_nets
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

proc dft_scan_get_scan_in_iterm {inst} {
  # Common scan-data input pin names across libraries.
  set candidates [list SI SD SCD SCAN_IN SCANIN]
  return [dft_scan_find_iterm_by_candidates $inst $candidates]
}

proc dft_scan_get_scan_cells_from_plan {} {
  set plan ""
  if { [catch { with_output_to_variable plan { report_dft_plan -verbose } } err] } {
    puts "DFT: WARNING: report_dft_plan failed; can't infer scan cells for scan-link WL: $err"
    return {}
  }

  set cells {}
  set in_chain 0
  foreach line [split $plan "\n"] {
    if { [regexp {^Scan chain '([^']+)'} $line] } {
      set in_chain 1
      continue
    }
    if { !$in_chain } {
      continue
    }
    if { [regexp {^\s+([^\s]+)} $line -> token] } {
      lappend cells $token
    }
  }
  return $cells
}

proc dft_collect_scan_link_nets {} {
  # "Scan-link nets" = the nets that the scan-in pins of scan cells are
  # connected to. On libraries that use Q as scan-out, these are typically
  # functional nets (Q nets) plus the top-level scan_in_* nets for chain heads.
  set block [ord::get_db_block]

  set cells [dft_scan_get_scan_cells_from_plan]
  if { [llength $cells] == 0 } {
    return {}
  }

  set net_set [dict create]
  foreach cell_name $cells {
    set inst [$block findInst $cell_name]
    if { $inst == "NULL" } {
      continue
    }
    set si [dft_scan_get_scan_in_iterm $inst]
    if { $si == "NULL" } {
      continue
    }
    set net [$si getNet]
    if { $net == "NULL" } {
      continue
    }
    dict set net_set [$net getName] 1
  }
  return [lsort -ascii [dict keys $net_set]]
}

proc dft_scan_wirelength_is_scan_enable_net {net_name} {
  set enable_glob [dft_scan_enable_glob]
  if { $enable_glob != "" && [string match $enable_glob $net_name] } {
    return 1
  }
  return [expr {
    [string match "scan_enable_*" $net_name] ||
    [string match "dft_scan_enable_net*" $net_name]
  }]
}

proc dft_scan_wirelength_is_scan_io_net {net_name} {
  set in_glob [dft_scan_in_glob]
  set out_glob [dft_scan_out_glob]
  if { $in_glob != "" && [string match $in_glob $net_name] } {
    return 1
  }
  if { $out_glob != "" && [string match $out_glob $net_name] } {
    return 1
  }
  return [expr {
    [string match "scan_in_*" $net_name] ||
    [string match "scan_out_*" $net_name]
  }]
}

proc dft_parse_report_wire_length_file {file_path} {
  set totals [dict create]
  dict set totals grt_total 0.0
  dict set totals drt_total 0.0
  dict set totals grt_scan_enable 0.0
  dict set totals drt_scan_enable 0.0
  dict set totals grt_scan_io 0.0
  dict set totals drt_scan_io 0.0
  dict set totals grt_other 0.0
  dict set totals drt_other 0.0
  dict set totals nets_seen 0

  if { ![file exists $file_path] } {
    return $totals
  }

  set fh [open $file_path r]
  while { [gets $fh line] >= 0 } {
    set line [string trim $line]
    if { $line == "" } {
      continue
    }
    if { [string match "tool net*" $line] } {
      continue
    }
    set fields [split $line]
    if { [llength $fields] < 4 } {
      continue
    }
    set tool [string trimright [lindex $fields 0] ":"]
    set net_name [lindex $fields 1]
    set wl [lindex $fields 2]
    if { ![string is double -strict $wl] } {
      continue
    }
    if { $tool != "grt" && $tool != "drt" } {
      continue
    }

    dict incr totals nets_seen 1

    if { $tool == "grt" } {
      dict set totals grt_total [expr {[dict get $totals grt_total] + $wl}]
    } else {
      dict set totals drt_total [expr {[dict get $totals drt_total] + $wl}]
    }

    if { [dft_scan_wirelength_is_scan_enable_net $net_name] } {
      if { $tool == "grt" } {
        dict set totals grt_scan_enable [expr {[dict get $totals grt_scan_enable] + $wl}]
      } else {
        dict set totals drt_scan_enable [expr {[dict get $totals drt_scan_enable] + $wl}]
      }
    } elseif { [dft_scan_wirelength_is_scan_io_net $net_name] } {
      if { $tool == "grt" } {
        dict set totals grt_scan_io [expr {[dict get $totals grt_scan_io] + $wl}]
      } else {
        dict set totals drt_scan_io [expr {[dict get $totals drt_scan_io] + $wl}]
      }
    } else {
      if { $tool == "grt" } {
        dict set totals grt_other [expr {[dict get $totals grt_other] + $wl}]
      } else {
        dict set totals drt_other [expr {[dict get $totals drt_other] + $wl}]
      }
    }
  }
  close $fh

  return $totals
}

proc dft_report_scan_wirelength {{tag "final"}} {
  set enable [dft_get_env_bool DFT_REPORT_SCAN_WIRELENGTH 1]
  if { !$enable } {
    return
  }
  if { ![dft_scan_design_present] } {
    return
  }

  if { ![info exists ::env(REPORTS_DIR)] } {
    puts "DFT: WARNING: REPORTS_DIR not set; skipping scan wirelength report"
    return
  }

  set block [ord::get_db_block]
  set have_grt 0
  set have_drt 0
  if { [info commands grt::have_routes] != "" && [grt::have_routes] } {
    set have_grt 1
  }
  if { [info commands grt::have_detailed_route] != "" && [grt::have_detailed_route $block] } {
    set have_drt 1
  }

  # Prefer detailed-route numbers when available; fall back to global-route.
  set wl_flags {}
  if { $have_drt } {
    lappend wl_flags -detailed_route
  } elseif { $have_grt } {
    lappend wl_flags -global_route
  } else {
    puts "DFT: scan wirelength: no global/detailed routes found; skipping"
    return
  }

  set scan_nets [dft_collect_scan_nets_by_sigtype]
  set scan_link_nets [dft_collect_scan_link_nets]

  set report_dir $::env(REPORTS_DIR)
  set out_dedicated "$report_dir/dft_scan_wirelength_${tag}.rpt"
  set out_links "$report_dir/dft_scan_link_wirelength_${tag}.rpt"

  if { [llength $scan_nets] > 0 } {
    catch { file delete -force $out_dedicated }
    if { [catch {
      report_wire_length -net $scan_nets {*}$wl_flags -file $out_dedicated
    } err] } {
      puts "DFT: WARNING: report_wire_length failed for SCAN nets: $err"
    }
  }

  if { [llength $scan_link_nets] > 0 } {
    catch { file delete -force $out_links }
    if { [catch {
      report_wire_length -net $scan_link_nets {*}$wl_flags -file $out_links
    } err] } {
      puts "DFT: WARNING: report_wire_length failed for scan-link nets: $err"
    }
  }

  # Emit metrics for easier comparison across variants.
  if { [llength $scan_nets] > 0 && [file exists $out_dedicated] } {
    set totals [dft_parse_report_wire_length_file $out_dedicated]
    set grt_total [expr {$have_grt ? [dict get $totals grt_total] : -1.0}]
    set drt_total [expr {$have_drt ? [dict get $totals drt_total] : -1.0}]
    set grt_enable [expr {$have_grt ? [dict get $totals grt_scan_enable] : -1.0}]
    set drt_enable [expr {$have_drt ? [dict get $totals drt_scan_enable] : -1.0}]
    set grt_io [expr {$have_grt ? [dict get $totals grt_scan_io] : -1.0}]
    set drt_io [expr {$have_drt ? [dict get $totals drt_scan_io] : -1.0}]
    set grt_other [expr {$have_grt ? [dict get $totals grt_other] : -1.0}]
    set drt_other [expr {$have_drt ? [dict get $totals drt_other] : -1.0}]

    utl::metric_float "dft_scan_dedicated_wl_grt_um" $grt_total
    utl::metric_float "dft_scan_dedicated_wl_drt_um" $drt_total
    utl::metric_float "dft_scan_enable_wl_grt_um" $grt_enable
    utl::metric_float "dft_scan_enable_wl_drt_um" $drt_enable
    utl::metric_float "dft_scan_io_wl_grt_um" $grt_io
    utl::metric_float "dft_scan_io_wl_drt_um" $drt_io
    utl::metric_float "dft_scan_other_wl_grt_um" $grt_other
    utl::metric_float "dft_scan_other_wl_drt_um" $drt_other
    utl::metric_int "dft_scan_dedicated_net_count" [llength $scan_nets]
  }

  if { [llength $scan_link_nets] > 0 && [file exists $out_links] } {
    set totals [dft_parse_report_wire_length_file $out_links]
    set grt_total [expr {$have_grt ? [dict get $totals grt_total] : -1.0}]
    set drt_total [expr {$have_drt ? [dict get $totals drt_total] : -1.0}]
    utl::metric_float "dft_scan_link_wl_grt_um" $grt_total
    utl::metric_float "dft_scan_link_wl_drt_um" $drt_total
    utl::metric_int "dft_scan_link_net_count" [llength $scan_link_nets]
  }

  puts "DFT: scan wirelength report(s):"
  if { [file exists $out_dedicated] } {
    puts "DFT:  - $out_dedicated"
  }
  if { [file exists $out_links] } {
    puts "DFT:  - $out_links"
  }
}
