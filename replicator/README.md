# DFT Replicator (JPEG-REAL1)

This folder is a self-contained reproduction harness for OpenROAD DFT scan
planning/stitching on a fixed Sky130HD JPEG database (`db/jpeg_sky130hd_postcts.odb`).

## Run

From the ORFS repo root:

```bash
./replicator/run_all.sh
```

To use a different OpenROAD build:

```bash
OPENROAD_EXE=/path/to/openroad ./replicator/run_all.sh
```

Default: `tools/OpenROAD/build/bin/openroad`.

## Outputs

- Logs: `replicator/<section>/logs/<test>.log`
- Artifacts: `replicator/<section>/runs/<test>/plot.png` and `post.odb`

## Expected behavior

Only these tests are expected to be infeasible and report `Scan architect constraints infeasible`:
- `5c` (polarity split, `K=1`)
- `6c` (clock split, `K=1`)
- `6e` (clock split, `K=3`, `max_imbalance=2%`)

All other tests should produce a stitched solution and a `plot.png`.

## Notes

Some runs intentionally pin scan ports to `(0,0)` and may emit global-route errors like
`GRT-0080 Invalid pin placement`; these are non-fatal for this harness and do not
indicate a DFT planning/stitching failure.
