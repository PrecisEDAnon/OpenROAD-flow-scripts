#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

FLOW_DIR="flow"
TIMESTAMP=$(date "+%Y%m%d_%H%M%S")
BACKUP_DIR="${FLOW_DIR}/backup_${TIMESTAMP}"

echo "=== Setup ==="
echo "Backup Directory: ${BACKUP_DIR}"
mkdir -p "${BACKUP_DIR}"

# Backup existing output directories
for dir in results logs reports objects; do
    if [ -d "${FLOW_DIR}/${dir}" ]; then
        echo "Backing up ${dir}..."
        mv "${FLOW_DIR}/${dir}" "${BACKUP_DIR}/"
    fi
done

# Clean the flow directory
echo "Cleaning flow directory (make nuke)..."
make -C "${FLOW_DIR}" nuke

# Create logs directory
mkdir -p "${FLOW_DIR}/logs"

# Function to run a job
# $1: Platform
# $2: Design
# $3: Config File (optional, defaults to config.mk)
# $4: Variant Name (optional, defaults to base)
run_job() {
    local platform=$1
    local design=$2
    local config=${3:-config.mk}
    local variant=${4:-base}
    
    # Construct log file name based on design and variant
    local log_file="${FLOW_DIR}/logs/run_${platform}_${design}_${variant}.log"
    local config_path="./designs/${platform}/${design}/${config}"
    
    if [ ! -f "${FLOW_DIR}/${config_path}" ]; then
        # Silent return if config doesn't exist is acceptable for the 0.85x check loop
        # or we can print a message.
        if [[ "${variant}" != "085" ]]; then
             echo "Warning: Config file ${config_path} not found. Skipping."
        fi
        return
    fi

    echo "Launching ${platform}/${design} [${variant}]..."
    # Run make in background
    nohup make -C "${FLOW_DIR}" \
        DESIGN_CONFIG="${config_path}" \
        FLOW_VARIANT="${variant}" \
        > "${log_file}" 2>&1 &
}

echo "=== Starting Wirelength Jobs ==="
run_job asap7 aes
run_job asap7 ibex
run_job asap7 jpeg
run_job sky130hd aes
run_job sky130hd ibex
run_job sky130hd jpeg

echo "=== Starting All Nangate45 Jobs (1x and 0.85x) ==="
# Get all designs in nangate45 directory
# Exclude bp_quad
designs=$(ls "${FLOW_DIR}/designs/nangate45")

for design in $designs; do
    if [ "${design}" == "bp_quad" ]; then
        continue
    fi
    
    # Run 1x (Standard) - assumes config.mk exists for all listed designs
    run_job nangate45 "${design}" "config.mk" "base"
    
    # Run 0.85x (Tight) - check if exists inside run_job, but logic here helps clarity
    # We try to run it, run_job handles missing file gracefully
    run_job nangate45 "${design}" "config_085.mk" "085"
done

echo "=== All Jobs Launched in Background ==="
echo "Check ${FLOW_DIR}/logs/ for progress."
