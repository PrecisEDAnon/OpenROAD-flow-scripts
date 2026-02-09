#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# Configure these (or export before running).
# -----------------------------------------------------------------------------

# Pre-placed JPEG flow run dir (must contain 3_5_place_dp.odb, 3_place.sdc, 6_final.def).
BASE_RUN_DIR=${BASE_RUN_DIR:-""}

# OpenROAD binary (must support: set_dft_config -polarity_mode mid|strict).
OPENROAD=${OPENROAD:-"$(pwd)/tools/OpenROAD/build/bin/openroad"}

# Liberty (Sky130HD default).
LIBERTY=${LIBERTY:-"$(pwd)/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"}

# OpenROAD ScanOpt knobs (increase for heavier runs).
SCANOPT_TIME_LIMIT=${SCANOPT_TIME_LIMIT:-15}
SCANOPT_ROUNDS=${SCANOPT_ROUNDS:-500000}
SCANOPT_SEED=${SCANOPT_SEED:-1}

# OR-Tools knobs.
ORTOOLS_TIME_20M=${ORTOOLS_TIME_20M:-1200}
ORTOOLS_TIME_6H=${ORTOOLS_TIME_6H:-21600}

TESTCASES_DIR=${TESTCASES_DIR:-"bullet-point-generator/testcases"}

if [[ -z "${BASE_RUN_DIR}" ]]; then
  echo "[bpgen] ERROR: BASE_RUN_DIR is empty."
  echo "[bpgen] Set it to a pre-placed JPEG run dir containing: 3_5_place_dp.odb, 3_place.sdc, 6_final.def"
  exit 1
fi

if [[ ! -x "${OPENROAD}" ]]; then
  echo "[bpgen] ERROR: OPENROAD binary not found/executable: ${OPENROAD}"
  exit 1
fi

# -----------------------------------------------------------------------------
# 0) Create testcases (JPEG-REAL1 + variants A..F).
# -----------------------------------------------------------------------------
python3 bullet-point-generator/make_testcases.py --base-run-dir "$BASE_RUN_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --out-dir "$TESTCASES_DIR"

# -----------------------------------------------------------------------------
# 3) Optimizers (JPEG-REAL1, K=1, begin/end LL)
# -----------------------------------------------------------------------------
python3 bullet-point-generator/run_openroad_case.py --case-id 3a --testcase JPEG-REAL1 --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 1 --begin LL --end LL --solver HEURISTIC --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 3b --testcase JPEG-REAL1 --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 1 --begin LL --end LL --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_ortools_case.py  --case-id 3c --testcase JPEG-REAL1 --testcases-dir "$TESTCASES_DIR" --time-limit-s "$ORTOOLS_TIME_20M" --begin LL --end LL
python3 bullet-point-generator/run_ortools_case.py  --case-id 3d --testcase JPEG-REAL1 --testcases-dir "$TESTCASES_DIR" --time-limit-s "$ORTOOLS_TIME_6H"  --begin LL --end LL

# -----------------------------------------------------------------------------
# 4) Ports/chains (JPEG-REAL1)
# -----------------------------------------------------------------------------
python3 bullet-point-generator/run_openroad_case.py --case-id 4a --testcase JPEG-REAL1 --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 1 --begin LL --end UR --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 4b --testcase JPEG-REAL1 --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 2 --begin LL --end LL --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 4c --testcase JPEG-REAL1 --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 2 --begin LL --end UR --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 4d --testcase JPEG-REAL1 --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 2 --begin UL --end UR --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"

# -----------------------------------------------------------------------------
# 5) Polarity (JPEG-REAL1A / JPEG-REAL1B)
# -----------------------------------------------------------------------------
# 5c: show BOTH modes.
python3 bullet-point-generator/run_openroad_case.py --case-id 5c_mid    --testcase JPEG-REAL1A --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 1 --begin LL --end LL --solver SCANOPT --polarity-mode mid    --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 5c_strict --testcase JPEG-REAL1A --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 1 --begin LL --end LL --solver SCANOPT --polarity-mode strict --expect ERROR --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"

# Google-doc entries (you can pick mid or strict; strict matches the "should error?" comment for K=1):
python3 bullet-point-generator/run_openroad_case.py --case-id 5d --testcase JPEG-REAL1A --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 2 --begin LL --end LL --solver SCANOPT --polarity-mode strict --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 5e --testcase JPEG-REAL1B --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 2 --begin LL --end LL --solver SCANOPT --polarity-mode strict --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"

# -----------------------------------------------------------------------------
# 6) Clocks (JPEG-REAL1C / JPEG-REAL1D)
# -----------------------------------------------------------------------------
python3 bullet-point-generator/run_openroad_case.py --case-id 6c --testcase JPEG-REAL1C --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 1 --begin LL --end LL --solver SCANOPT --expect ERROR --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 6d --testcase JPEG-REAL1C --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 2 --begin LL --end LL --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 6e --testcase JPEG-REAL1C --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 3 --begin LL --end LL --solver SCANOPT --expect ERROR --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 6f --testcase JPEG-REAL1C --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 4 --begin LL --end LL --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 6g --testcase JPEG-REAL1D --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 2 --begin LL --end LL --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 6h --testcase JPEG-REAL1D --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 4 --begin LL --end LL --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"

# -----------------------------------------------------------------------------
# 7) Grouping (JPEG-REAL1E / JPEG-REAL1F)
# -----------------------------------------------------------------------------
python3 bullet-point-generator/run_openroad_case.py --case-id 7c --testcase JPEG-REAL1E --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 1 --begin LL --end LL --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 7d --testcase JPEG-REAL1F --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 1 --begin LL --end LL --solver SCANOPT --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"
python3 bullet-point-generator/run_openroad_case.py --case-id 7e --testcase JPEG-REAL1E --testcases-dir "$TESTCASES_DIR" --openroad "$OPENROAD" --liberty "$LIBERTY" --k 2 --begin LL --end LL --solver SCANOPT --expect ERROR --scanopt-time-limit "$SCANOPT_TIME_LIMIT" --scanopt-rounds "$SCANOPT_ROUNDS" --scanopt-seed "$SCANOPT_SEED"

# -----------------------------------------------------------------------------
# Summary table (optional).
# -----------------------------------------------------------------------------
python3 bullet-point-generator/fill_amur_table.py --out amur_table_filled.md
