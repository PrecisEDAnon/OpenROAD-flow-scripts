# Project Status Notes - November 19, 2025

## 1. Clock Scaling (0.85x) for Nangate45
- Created `_085` variants for specific Nangate45 designs to test tighter timing constraints (0.85x of original clock period).
- **Affected Designs:**
    - `ariane133`, `ariane136`
    - `black_parrot`, `bp_be_top`, `bp_fe_top`, `bp_multi_top`, `bp_quad`
    - `swerv_wrapper`, `tinyRocket`
- **Changes:**
    - Created `*_085.sdc` files with scaled clock periods.
    - Created `config_085.mk` files pointing to the new SDC files.

## 2. Execution Scripts
- Created `run_subset_parallel.sh`: Runs baselines + specific Nangate45 designs (1x & 0.85x) in parallel.
- Created `run_all_parallel.sh`: Runs baselines + ALL Nangate45 designs (1x & 0.85x) in parallel.
    - **Features:**
        - Backs up existing `flow/` directories (`results`, `logs`, etc.) to `flow/backup_<timestamp>`.
        - Nukes the flow directory before starting.
        - Runs all jobs in background (`&`) for true parallel execution.
        - Uses `FLOW_VARIANT` ("base" vs "085") to separate output streams.

## 3. Metrics Collection
- Created `collect_metrics.py`:
    - Scans `flow/logs` and `flow/backup_*/logs`.
    - Extracts key metrics: Instance Count, Area, Power, Routed Wirelength, Effective Clock, Target Clock, Slack.
    - **Calculates Effective Clock** as `Target Clock - Worst Slack`.
    - Generates a Markdown table in `metrics.md`.
    - Includes a "Source" column to track which backup/run folder the data came from.
    - Includes logic to handle unfinished runs gracefully (reporting "N/A").

## 4. Current Status
- A full parallel run (`run_all_parallel.sh`) was executed.
- Metrics have been successfully collected and verified for both baseline and 0.85x variants.
- `metrics.md` contains the latest results.
- All OpenROAD jobs have completed.
