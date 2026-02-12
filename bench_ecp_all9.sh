#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

OPENROAD_EXE="${OPENROAD_EXE:-$ROOT_DIR/tools/OpenROAD/build/bin/openroad}"
NUM_CORES="${NUM_CORES:-4}"
VALIDATE_N="${VALIDATE_N:-18}"
VALIDATE_JOBS="${VALIDATE_JOBS:-18}"
TIME_BUDGET_S="${TIME_BUDGET_S:-3000}"

SURROGATE_SAMPLES="${SURROGATE_SAMPLES:-100000000000}"
SURROGATE_TOP_N="${SURROGATE_TOP_N:-200}"
SURROGATE_GLOBAL_TOP_N="${SURROGATE_GLOBAL_TOP_N:-200}"

DATE_TAG="${DATE_TAG:-20260211}"
SEED="${SEED:-1}"

need_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 1
  fi
}

surrogate_done() {
  local summary="$1"
  python3 - <<'PY' "$summary"
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.exists():
    raise SystemExit(1)
try:
    obj = json.loads(p.read_text())
except Exception:
    raise SystemExit(1)
val = obj.get("validation") if isinstance(obj, dict) else None
enabled = bool(val.get("enabled")) if isinstance(val, dict) else False
raise SystemExit(0 if enabled else 1)
PY
}

random_done() {
  local summary="$1"
  [[ -f "$summary" ]]
}

wait_for_idle() {
  while pgrep -f "flow/scripts/surrogate_autotune.py" >/dev/null 2>&1; do
    echo "[bench] Waiting: surrogate_autotune.py already running..."
    sleep 60
  done
  while pgrep -f "flow/scripts/random_baseline.py" >/dev/null 2>&1; do
    echo "[bench] Waiting: random_baseline.py already running..."
    sleep 60
  done
}

run_surrogate_ecp() {
  local platform="$1"
  local design="$2"
  local variant="$3"
  local design_config="designs/${platform}/${design}/config.mk"
  local space_file="designs/${platform}/${design}/surrogate_space.json"
  local summary="flow/results/${platform}/${design}/${variant}/surrogate_autotune.json"

  need_file "flow/${design_config}"
  need_file "flow/${space_file}"

  if surrogate_done "$summary"; then
    echo "[bench] surrogate done: ${platform}/${design} (${variant})"
    return 0
  fi

  echo "[bench] surrogate start: ${platform}/${design} (${variant})"
  wait_for_idle
  OPENROAD_EXE="$OPENROAD_EXE" make -C flow surrogate_autotune \
    "DESIGN_CONFIG=${design_config}" \
    "FLOW_VARIANT=${variant}" \
    "NUM_CORES=${NUM_CORES}" \
    "SURROGATE_SPACE_FILE=${space_file}" \
    "SURROGATE_OBJECTIVE=effective_clock_period" \
    "SURROGATE_RESUME=1" \
    "SURROGATE_TIME_BUDGET_S=${TIME_BUDGET_S}" \
    "SURROGATE_SAMPLES=${SURROGATE_SAMPLES}" \
    "SURROGATE_TOP_N=${SURROGATE_TOP_N}" \
    "SURROGATE_GLOBAL_TOP_N=${SURROGATE_GLOBAL_TOP_N}" \
    "SURROGATE_VALIDATE=1" \
    "SURROGATE_VALIDATE_N=${VALIDATE_N}" \
    "SURROGATE_VALIDATE_JOBS=${VALIDATE_JOBS}" \
    "SURROGATE_VALIDATE_MAKE_TARGET=report" \
    "SURROGATE_ROUTE_VALIDATE_N=${VALIDATE_N}" \
    "SURROGATE_ROUTE_VALIDATE_JOBS=${VALIDATE_JOBS}" \
    "SURROGATE_ROUTE_INJECT_RANDOM_N=0"
}

run_random_ecp() {
  local platform="$1"
  local design="$2"
  local synth_from_variant="$3"
  local platform_tag="$platform"
  if [[ "$platform" == "nangate45" ]]; then
    platform_tag="ng45"
  fi
  local prefix="rand18_${platform_tag}_${design}_${DATE_TAG}"
  local summary="flow/results/${platform}/${design}/${prefix}_ecp/random_baseline.json"

  need_file "flow/designs/${platform}/${design}/config.mk"

  if random_done "$summary"; then
    echo "[bench] random done: ${platform}/${design} (${prefix}_ecp)"
    return 0
  fi

  echo "[bench] random start: ${platform}/${design} (${prefix}_ecp)"
  wait_for_idle
  OPENROAD_EXE="$OPENROAD_EXE" python3 flow/scripts/random_baseline.py \
    --platforms "$platform" \
    --designs "$design" \
    --objectives ecp \
    --validate-n "$VALIDATE_N" \
    --validate-jobs "$VALIDATE_JOBS" \
    --num-cores "$NUM_CORES" \
    --make-target report \
    --clock-strategy space \
    --clock-sweep-n 9 \
    --synth-from-variant "$synth_from_variant" \
    --compare-to-variant-ecp "$synth_from_variant" \
    --variant-prefix "$prefix" \
    --resume \
    --seed "$SEED"
}

variant_for() {
  local platform="$1"
  local design="$2"
  if [[ "$design" == "ibex" && ( "$platform" == "asap7" || "$platform" == "nangate45" ) ]]; then
    echo "ecpwall_modelv2_ibex_t3000s"
  else
    echo "ecpwall_modelv2_t3000s_ecp_${platform}_${design}"
  fi
}

main() {
  need_file "$OPENROAD_EXE"

  local platforms=(asap7 nangate45 sky130hd)
  local designs=(aes ibex jpeg)

  for platform in "${platforms[@]}"; do
    for design in "${designs[@]}"; do
      local variant
      variant="$(variant_for "$platform" "$design")"
      run_surrogate_ecp "$platform" "$design" "$variant"
      run_random_ecp "$platform" "$design" "$variant"
    done
  done

  echo "[bench] done"
}

main "$@"
