#!/usr/bin/env bash
set -euo pipefail

log() {
  printf "[%s] %s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "ERROR: missing required command: $1"
    exit 127
  fi
}

require_cmd bash
require_cmd make

WORK_HOME="${WORK_HOME:-/work}"
FLOW_VARIANT="${FLOW_VARIANT:-base}"
PLATFORM="${PLATFORM:-nangate45}"
DESIGN="${DESIGN:-gcd}"
NPROC="${NPROC:-$(nproc)}"
MAKE_TARGET="${MAKE_TARGET:-}"

mkdir -p "${WORK_HOME}"

cd /OpenROAD-flow-scripts
source ./env.sh

require_cmd openroad
require_cmd yosys

cd "${FLOW_HOME}"

if [[ -z "${DESIGN_CONFIG:-}" ]]; then
  DESIGN_CONFIG="./designs/${PLATFORM}/${DESIGN}/config.mk"
fi

log "FLOW_HOME=${FLOW_HOME}"
log "WORK_HOME=${WORK_HOME}"
log "DESIGN_CONFIG=${DESIGN_CONFIG}"
log "FLOW_VARIANT=${FLOW_VARIANT}"
log "NPROC=${NPROC}"
log "MAKE_TARGET=${MAKE_TARGET:-<default>}"

make_args=(
  "DESIGN_CONFIG=${DESIGN_CONFIG}"
  "WORK_HOME=${WORK_HOME}"
  "FLOW_VARIANT=${FLOW_VARIANT}"
  "-j"
  "${NPROC}"
)

if [[ -n "${MAKE_TARGET}" ]]; then
  make_args+=("${MAKE_TARGET}")
fi

make \
  --file "${FLOW_HOME}/Makefile" \
  --file /usr/local/share/orfs-extra.mk \
  "${make_args[@]}"

final_dir="${WORK_HOME}/results/${PLATFORM}/${DESIGN}/${FLOW_VARIANT}"
if [[ -d "${final_dir}" ]]; then
  log "Results: ${final_dir}"
  ls -lah "${final_dir}" | head -n 200
fi
