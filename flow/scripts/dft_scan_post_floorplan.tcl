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

# Default behavior keeps the number of scan ports stable for QoR comparisons by
# using a single scan chain.
#
# To enable multiple chains, set either:
#   - DFT_CHAIN_COUNT (exact),
#   - DFT_MAX_CHAINS (explicit cap), and/or
#   - DFT_MAX_CHAIN_LENGTH (aka DFT_MAX_LENGTH) to bound chain length in bits.
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

# Infer how many chains will be created with the current DFT config so we can
# create the right number of scan ports before IO placement.
set chain_count 1
with_output_to_variable dft_plan_str { report_dft_plan }
if { ![regexp {Number of chains:\s*([0-9]+)} $dft_plan_str -> chain_count] } {
  puts "DFT: WARNING: couldn't parse chain count from report_dft_plan; defaulting to 1"
  set chain_count 1
}

# Scan I/O (scan_in_N/scan_out_N) + shared enable.
dft_ensure_scan_port "scan_enable_0" INPUT
for { set i 0 } { $i < $chain_count } { incr i } {
  dft_ensure_scan_port "scan_in_$i" INPUT
  dft_ensure_scan_port "scan_out_$i" OUTPUT
}

# Functional-mode assumption for STA/power (disable scan path).
set_case_analysis 0 [get_ports scan_enable_0]
