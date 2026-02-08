#!/usr/bin/env bash
set -euo pipefail

# Sweep `(DFT_MAX_CHAINS, DFT_MAX_CHAIN_LENGTH)` for nangate45/ibex using the
# routing-aware scan ordering flow (trial GRT guides -> pin-to-net ordering).
#
# Usage:
#   flow/util/run_dft_sweep_ibex.sh [variant_prefix]
#
# Optional env:
#   OPENROAD_EXE=/path/to/openroad
#
# Output:
#   - Writes flow variants under `flow/results/nangate45/ibex/<variant>/`
#   - Writes a Markdown table to `flow/logs/nangate45/ibex/<prefix>_sweep.md`

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FLOW_DIR="$ROOT/flow"
cd "$ROOT"

OPENROAD_EXE="${OPENROAD_EXE:-$ROOT/tools/OpenROAD/build/bin/openroad}"
if [[ ! -x "$OPENROAD_EXE" ]]; then
  echo "ERROR: OPENROAD_EXE not found or not executable: $OPENROAD_EXE" >&2
  exit 2
fi

DESIGN_CONFIG="./designs/nangate45/ibex/config.mk"
HOOK_POST_FLOORPLAN="$ROOT/flow/scripts/dft_scan_post_floorplan.tcl"
HOOK_PRE_GRT="$ROOT/flow/scripts/dft_scan_pre_global_route.tcl"
HOOK_POST_GRT="$ROOT/flow/scripts/dft_scan_post_global_route.tcl"
VALIDATE_PY="$ROOT/flow/util/scan_chain_validate.py"

PREFIX="${1:-dft_p2n_ibex_$(date +%Y%m%d_%H%M%S)}"
OUT_MD="$FLOW_DIR/logs/nangate45/ibex/${PREFIX}_sweep.md"

mkdir -p "$(dirname "$OUT_MD")"
cat >"$OUT_MD" <<'MD'
| max_chain_count | max_length | tns | wns | wirelength | chains | min_len | median | max_len |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
MD

declare -a CASES=(
  "2:966"  "2:1159" "2:1352" "2:1545" "2:1738" "2:1931"
  "4:483"  "4:772"  "4:1062" "4:1351" "4:1641" "4:1931"
  "8:242"  "8:579"  "8:917"  "8:1255" "8:1593" "8:1931"
)

for entry in "${CASES[@]}"; do
  IFS=":" read -r max_chains max_length <<<"$entry"
  variant="${PREFIX}_k${max_chains}_len${max_length}"

  final_v="$FLOW_DIR/results/nangate45/ibex/${variant}/6_final.v"
  if [[ -f "$final_v" ]]; then
    echo "SKIP (already done): $variant"
  else
    echo "RUN: $variant"
    make -C "$FLOW_DIR" \
      DESIGN_CONFIG="$DESIGN_CONFIG" \
      FLOW_VARIANT="$variant" \
      OPENROAD_EXE="$OPENROAD_EXE" \
      POST_FLOORPLAN_TCL="$HOOK_POST_FLOORPLAN" \
      PRE_GLOBAL_ROUTE_TCL="$HOOK_PRE_GRT" \
      POST_GLOBAL_ROUTE_TCL="$HOOK_POST_GRT" \
      DFT_DEFER_STITCH=1 \
      DFT_SCAN_ORDER_METRIC=PIN_TO_NET \
      DFT_MAX_CHAINS="$max_chains" \
      DFT_MAX_CHAIN_LENGTH="$max_length" \
      finish
  fi

  report_json="$FLOW_DIR/logs/nangate45/ibex/${variant}/6_report.json"
  if [[ ! -f "$report_json" ]]; then
    echo "ERROR: missing report json: $report_json" >&2
    exit 2
  fi

  # Extract QoR metrics (TNS/WNS/WL).
  read -r tns wns wl <<<"$(
    python3 - "$report_json" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
def get_first(keys):
  for k in keys:
    if k in data:
      return data[k]
  return ""
tns = get_first(["finish__timing__setup__tns", "finish__timing__setup__TNS"])
wns = get_first(["finish__timing__setup__ws", "finish__timing__setup__WNS", "finish__timing__setup__wns"])
wl  = get_first(["finish__route__wirelength", "finish__route__wire_length", "finish__route__wirelength__total"])
print(tns, wns, wl)
PY
  )"

  # Extract chain length statistics from the final Verilog (multi-chain aware).
  read -r chains min_len median max_len <<<"$(
    VALIDATE_PY="$VALIDATE_PY" python3 - "$final_v" <<'PY'
import json, os, statistics, subprocess, sys, tempfile
verilog = sys.argv[1]
validate_py = os.environ["VALIDATE_PY"]
with tempfile.NamedTemporaryFile(prefix="scan_validate_", suffix=".json", delete=False) as tf:
  json_path = tf.name

cmd = ["python3", validate_py, "--auto-chains", "--verilog", verilog, "--out-json", json_path]
p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
if p.returncode != 0:
  sys.stderr.write(p.stderr)
  raise SystemExit(p.returncode)
summary = json.load(open(json_path))
try:
  os.unlink(json_path)
except FileNotFoundError:
  pass
lengths = sorted([c["cells"] for c in summary.get("chains", []) if c.get("cells") is not None])
if not lengths:
  print(0, "", "", "")
  raise SystemExit(0)
chains = len(lengths)
min_len = lengths[0]
max_len = lengths[-1]
median = int(statistics.median(lengths))
print(chains, min_len, median, max_len)
PY
  )"

  printf '| %d | %d | %s | %s | %s | %s | %s | %s | %s |\n' \
    "$max_chains" "$max_length" "$tns" "$wns" "$wl" "$chains" "$min_len" "$median" "$max_len" >>"$OUT_MD"
done

echo "Wrote: $OUT_MD"
