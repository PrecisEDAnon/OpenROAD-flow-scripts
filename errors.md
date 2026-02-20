# DFT audit — severe issues (2026-02-18)

This file captures **high-severity** issues found while auditing the DFT/scan
work in this repo (root DFT docs + `replicator/` harness + ORFS DFT hook
scripts), with extra focus on **clocks**, **polarity**, and **group/order
constraints**.

## Status update (2026-02-20)

- **Fixed in this tree:** (1) `chains4` naming typo, (3) missing replicator assertions for group contiguity/`before`, (4) overlapping / multiply-parented groups now error, (5) `report_dft_plan*` avoids repeated optimizer runs via a per-session cache.
- **Mitigated in this tree:** (2) ORFS now treats `[ERROR DFT-*]` / infeasible planning as fatal during `execute_dft_plan` and validates that scan chains were actually created in ODB; replicator “pass” cases now fail on DFT errors in logs. (OpenROAD core still may exit 0 if DFT logs an error.)

## Severe issues

### 1) Replicator constraints typo: `chains4` skips `chain_3`

- **Where:** `replicator/constraints/chains4`
- **What:** The last line defines `chain chain_4 ...` instead of `chain chain_3 ...`.
- **Impact:**
  - `report_dft_plan` prints the 4th chain as `chain_4`, which is confusing and
    makes chain naming look non-contiguous.
  - Any tooling that assumes sequential chain names (`chain_0..chain_{K-1}`) can
    mis-map chain ordinals to chain names.

### 2) DFT “ERROR” does **not** imply a failing process exit status

- **Observed behavior (current OpenROAD build in this repo):**
  - In infeasible cases (e.g. polarity split with `K=1`, or clock split with
    `K=1`), OpenROAD logs errors like:
    - `Scan architect constraints infeasible ...`
    - `[ERROR DFT-....] ...`
  - Yet `openroad -python ...` still exits with status **0** (and the python
    script can continue to run other commands like `global_route`).
- **Impact:**
  - The `replicator/run_all.sh` “pass” cases can silently accept a DFT failure
    if OpenROAD exits 0 but logs DFT errors.
  - ORFS flow stages that rely on OpenROAD’s exit status can continue past a
    failed `execute_dft_plan`, producing **0 scan chains** or unstiched scan
    connectivity without failing the build.

### 3) Replicator lacks automated assertions for group contiguity / `before`

- **Where:** `replicator/7_groups/*`
- **What:** The harness highlights group members in `plot.png` but does **not**
  assert that:
  - each `group` is contiguous in scan order, and
  - `before A B` is satisfied in scan order.
- **Impact:** Regressions in constraints enforcement can slip through, and users
  may mis-report “non-contiguous groups” based on visual (physical) dispersion
  rather than scan-order contiguity.
- **Audit result (current tree):** For `replicator` cases `7c/7d/7e`, scan-order
  contiguity and `before group1 group2` are satisfied when checked against the
  produced `post.odb` and the generated constraints file.

### 4) Overlapping / multiply-parented groups are not rejected (can yield “non-contiguous groups” reports)

- **Where:** OpenROAD DFT constraints parsing:
  - `tools/OpenROAD/src/dft/src/config/ScanArchitectConfig.cpp` (`loadScanOrderConstraintsFile()`)
- **What:** The constraints file allows:
  - the same **instance** to be referenced directly by multiple `group` directives, and/or
  - the same **sub-group** to be referenced by multiple parent groups.
- **Impact:**
  - This effectively creates a **DAG** of group membership, which is ambiguous for users and can make it
    impossible to satisfy “contiguity per group” semantics in the way users expect.
  - Users may observe “non-contiguous groups” (or inconsistent constraint behavior) when group definitions overlap.

### 5) `report_dft_plan*` always runs full scan planning + ordering (optimizer), even when used as a “report”

- **Where:** OpenROAD DFT report commands:
  - `tools/OpenROAD/src/dft/src/Dft.cpp` (`Dft::reportDftPlan`, `Dft::reportDftPlanPins`)
- **What:** Both report commands call `scanArchitect()`, which runs:
  - scan-cell collection,
  - chain partitioning, and
  - intra-chain ordering (potentially expensive solver work).
- **Impact:**
  - ORFS hook scripts that call `report_dft_plan` multiple times can accidentally invoke the optimizer multiple times
    per flow stage, increasing runtime and risking nondeterministic plan differences when solver randomness is enabled.
  - After `execute_dft_plan` has already written scan chains into ODB, `report_dft_plan` can still recompute a *new*
    plan (unless `use_existing_scan_chains` is enabled), which can confuse debugging (“reported plan” vs “stitched plan”).

## Notes (non-severe, but frequently confused)

- In OpenROAD’s scan-order constraints file semantics, `group` enforces
  **contiguity in scan order** within a chain; it does **not** force “all group
  members must be in exactly one scan chain” when `K>1`. To force must-same-chain,
  use `assign <chain> <inst|group...>` (or explicit strict paths / fixed edges).
- The replicator intentionally pins some scan ports to `(0,0)` and may emit GRT
  errors like invalid pin placement; these are expected **harness artifacts**,
  not DFT failures.
