#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/../"

OPENROAD_EXE=${OPENROAD_EXE:-"$(cd .. && pwd)/tools/OpenROAD/build/bin/openroad"}
if [ ! -x "$OPENROAD_EXE" ]; then
  echo "ERROR: OPENROAD_EXE not found/executable: $OPENROAD_EXE" >&2
  exit 1
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

out_v="$tmpdir/dft_polarity.v"
log="$tmpdir/openroad.log"
tcl="$tmpdir/run.tcl"

repo_root="$(cd .. && pwd)"
sky130_lef_tech="$repo_root/tools/OpenROAD/src/dft/test/sky130hd/sky130hd.tlef"
sky130_lef_sc="$repo_root/tools/OpenROAD/src/dft/test/sky130hd/sky130_fd_sc_hd_merged.lef"
sky130_lib="$repo_root/tools/OpenROAD/src/dft/test/sky130hd/sky130_fd_sc_hd__tt_025C_1v80.lib"
rtl="$repo_root/tools/OpenROAD/src/dft/test/scan_architect_sky130.v"

cat >"$tcl" <<EOF
set ::env(DFT_CLOCK_MIXING) clock_mix
set ::env(DFT_MAX_CHAIN_LENGTH) 3
set ::env(DFT_UCLA_MAJOR_LOOPS) 10
set ::env(DFT_SCANOPT_ROUNDS) 200
set ::env(DFT_SCANOPT_TIME_LIMIT) 0
set ::env(DFT_LOCKUP_POLICY) auto
set ::env(DFT_BUFFER_SCAN_ENABLE) 0
set ::env(DFT_DONT_TOUCH_SCAN_NETS) 0

read_lef $sky130_lef_tech
read_lef $sky130_lef_sc
read_liberty $sky130_lib

read_verilog $rtl
link_design scan_architect

create_clock -name clock1 -period 2.0000 -waveform {0.0000 1.0000} [get_ports {clock1}]
create_clock -name clock2 -period 2.0000 -waveform {0.0000 1.0000} [get_ports {clock2}]

source $repo_root/flow/scripts/dft_scan_post_floorplan.tcl
source $repo_root/flow/scripts/dft_scan_pre_global_route.tcl

write_verilog $out_v
exit
EOF

"$OPENROAD_EXE" -exit "$tcl" | tee "$log"

# Ensure the AUTO fallback was exercised (mixed pos/neg clocks exist in this RTL).
grep -qF "DFT: AUTO: mixed-clock/edge chains detected" "$log"

python3 util/scan_chain_validate.py --auto-chains --verilog "$out_v"

echo "PASS: DFT polarity regression (posedge+negedge scan flops) validated"
