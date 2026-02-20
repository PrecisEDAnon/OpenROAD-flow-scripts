#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/../"

DESIGN_NAME=${1:-gcd}
PLATFORM=${2:-nangate45}
DESIGN_CONFIG=./designs/$PLATFORM/$DESIGN_NAME/config.mk

OPENROAD_EXE_DEFAULT="$(cd .. && pwd)/tools/OpenROAD/build/bin/openroad"
if [ -z "${OPENROAD_EXE:-}" ]; then
  if [ -x "$OPENROAD_EXE_DEFAULT" ]; then
    export OPENROAD_EXE="$OPENROAD_EXE_DEFAULT"
  else
    echo "ERROR: OPENROAD_EXE not set and default build not found/executable: $OPENROAD_EXE_DEFAULT" >&2
    exit 1
  fi
fi

run_variant() {
  local variant="$1"
  shift
  local -a make_args=()
  local -a validate_args=()

  # Keep the regression fast and robust; we only need GRT for stitching and the
  # final scan wirelength report (which falls back to global-route numbers).
  make_args+=("SKIP_DETAILED_ROUTE=1")
  make_args+=("OPENROAD_EXE=$OPENROAD_EXE")
  make_args+=("DFT_UCLA_MAJOR_LOOPS=10")
  make_args+=("DFT_SCANOPT_ROUNDS=200")
  make_args+=("DFT_SCANOPT_TIME_LIMIT=0")
  make_args+=("DFT_WRITE_SCANDEF=1")

  while [ $# -gt 0 ]; do
    if [ "$1" = "--" ]; then
      shift
      validate_args=("$@")
      break
    fi
    make_args+=("$1")
    shift
  done

  echo "=== DFT regression: $PLATFORM/$DESIGN_NAME ($variant) ==="

  make DESIGN_CONFIG="$DESIGN_CONFIG" FLOW_VARIANT="$variant" clean_all
  make DESIGN_CONFIG="$DESIGN_CONFIG" FLOW_VARIANT="$variant" "${make_args[@]}" finish

  local final_v="results/$PLATFORM/$DESIGN_NAME/$variant/6_final.v"
  python3 util/scan_chain_validate.py --verilog "$final_v" "${validate_args[@]}"

  local wl_rpt="reports/$PLATFORM/$DESIGN_NAME/$variant/dft_scan_wirelength_finish.rpt"
  if [ ! -f "$wl_rpt" ]; then
    echo "ERROR: missing scan wirelength report: $wl_rpt" >&2
    exit 1
  fi

  local scandef="results/$PLATFORM/$DESIGN_NAME/$variant/6_final.scandef"
  if [ ! -f "$scandef" ]; then
    echo "ERROR: missing scandef export: $scandef" >&2
    exit 1
  fi
}

# 1) Bundled ScanOpt-next ordering (placement-based).
run_variant "test_dft_scanopt_next" DFT_ENABLE=1 DFT_SCAN_SOLVER=scanopt_next

# 2) Routing-aware ordering (trial GRT guides + PIN_TO_NET).
run_variant "test_dft_route_aware" DFT_ENABLE=1 DFT_ROUTE_AWARE=1

# 3) Custom scan port naming (reuse existing ports via patterns).
run_variant "test_dft_custom_ports" DFT_ENABLE=1 \
  DFT_SCAN_ENABLE_NAME_PATTERN=se DFT_SCAN_IN_NAME_PATTERN=si_{} DFT_SCAN_OUT_NAME_PATTERN=so_{} \
  -- --scan-in si_0 --scan-out so_0 --scan-enable se
