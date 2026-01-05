# DFT scan insertion hook for ORFS.
#
# Intended use: set `POST_FLOORPLAN_TCL` to this file.
#
# This runs after floorplan, before saving `2_1_floorplan.odb`, so subsequent
# stages (place/cts/route) see scan flops and scan ports.

puts "DFT: scan_replace + create scan ports"

# Keep the number of scan ports stable for QoR comparisons.
# With clock mixing enabled, all scan cells share one hash domain, so -max_chains
# applies globally.
set_dft_config -max_chains 1 -clock_mixing clock_mix

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

# One-chain scan I/O + shared enable.
dft_ensure_scan_port "scan_enable_0" INPUT
dft_ensure_scan_port "scan_in_0" INPUT
dft_ensure_scan_port "scan_out_0" OUTPUT

# Functional-mode assumption for STA/power (disable scan path).
set_case_analysis 0 [get_ports scan_enable_0]

