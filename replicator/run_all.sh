#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORFS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

OPENROAD_EXE="${OPENROAD_EXE:-${ORFS_ROOT}/tools/OpenROAD/build/bin/openroad}"

if [[ ! -x "${OPENROAD_EXE}" ]]; then
  echo "error: OPENROAD_EXE not found/executable: ${OPENROAD_EXE}" >&2
  echo "hint: build OpenROAD under: ${ORFS_ROOT}/tools/OpenROAD" >&2
  exit 2
fi

run_case()
{
  local id="$1"
  local test_dir="$2"
  local expected="$3" # pass|fail
  shift 3

  local dir_path="${SCRIPT_DIR}/${test_dir}"
  local log_dir="${dir_path}/logs"
  local run_dir="${dir_path}/runs"

  mkdir -p "${log_dir}" "${run_dir}"
  mkdir -p "${run_dir}/${id}"

  local log_path="${log_dir}/${id}.log"

  echo "=== ${id} (${expected}) ==="
  pushd "${dir_path}" >/dev/null
  set +e
  "${OPENROAD_EXE}" -exit -python run_ord.py "$@" 2>&1 | tee "${log_path}"
  local status="${PIPESTATUS[0]}"
  set -e
  popd >/dev/null

  # OpenROAD DFT may log [ERROR DFT-*] while still exiting 0 (especially via
  # `-python`). Treat these as failures for "pass" cases.
  local dft_err=0
  if grep -qF "[ERROR DFT-" "${log_path}" \
    || grep -qF "Scan architect constraints infeasible" "${log_path}"; then
    dft_err=1
  fi

  if [[ "${expected}" == "pass" ]]; then
    if [[ "${status}" -ne 0 || "${dft_err}" -ne 0 ]]; then
      echo "error: ${id} failed (exit ${status}, dft_err=${dft_err}): ${log_path}" >&2
      exit 1
    fi
  elif [[ "${expected}" == "fail" ]]; then
    if [[ "${status}" -ne 0 || "${dft_err}" -ne 0 ]]; then
      return 0
    fi
    echo "error: ${id} unexpectedly passed: ${log_path}" >&2
    exit 1
  else
    echo "error: unknown expected status '${expected}' for ${id}" >&2
    exit 2
  fi
}

# 3. Optimizers (OpenROAD only)
run_case 3a 3_optimizers pass --output-plot runs/3a/openroad.png

# 4. Ports/chains
run_case 4a 4_locations pass --begin-mode lower_left --end-mode upper_right --k 1 --output runs/4a
run_case 4b 4_locations pass --begin-mode lower_left --end-mode lower_left --k 2 --output runs/4b
run_case 4c 4_locations pass --begin-mode lower_left --end-mode upper_right --k 2 --output runs/4c
run_case 4d 4_locations pass --begin-mode upper_left --end-mode upper_right --k 2 --output runs/4d

# 5. Polarities
run_case 5c 5_polarities fail --polarity-mode split --k 1 --output runs/5c
run_case 5d 5_polarities pass --polarity-mode split --k 2 --output runs/5d
run_case 5e 5_polarities pass --polarity-mode even --k 2 --output runs/5e

# 6. Clocks
run_case 6c 6_clocks fail --clock-mode split --k 1 --output runs/6c
run_case 6d 6_clocks pass --clock-mode split --k 2 --output runs/6d
run_case 6e 6_clocks fail --clock-mode split --k 3 --output runs/6e
run_case 6f 6_clocks pass --clock-mode split --k 4 --output runs/6f
run_case 6g 6_clocks pass --clock-mode even --k 2 --output runs/6g
run_case 6h 6_clocks pass --clock-mode even --k 4 --output runs/6h

# 7. Groups
run_case 7c 7_groups pass --group-mode split --k 1 --output runs/7c
run_case 7d 7_groups pass --group-mode even --k 1 --output runs/7d
run_case 7e 7_groups pass --group-mode split --k 2 --output runs/7e
run_case 7f 7_groups fail --group-mode overlap --k 1 --output runs/7f

echo "OK: all required cases passed (expected failures: 5c, 6c, 6e, 7f)."
