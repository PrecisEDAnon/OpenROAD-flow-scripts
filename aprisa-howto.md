# NG45 Aprisa reproduce (Ariane + BSG)

Branches:

- ORFS: `https://github.com/PrecisEDAnon/OpenROAD-flow-scripts` branch `ng45-aprisa-orfs-93c42b`
- OpenROAD: `https://github.com/PrecisEDAnon/OpenROAD` branch `ng45-aprisa-openroad-7bc521`
  - The ORFS branch pins `tools/OpenROAD` to the OpenROAD commit used.

Clone + init submodules:

```bash
git clone https://github.com/PrecisEDAnon/OpenROAD-flow-scripts
cd OpenROAD-flow-scripts
git checkout ng45-aprisa-orfs-93c42b
git submodule update --init --recursive
```

Build OpenROAD:

```bash
./build_openroad.sh --local
```

Run:

```bash
# Ariane @ 1.3ns, util=68
make -C flow DESIGN_CONFIG=../asap7_ng45_testcases/config_ariane_ng45.mk \
  FLOW_VARIANT=u68_p1p3_pdnw0p9_strict EQUIVALENCE_CHECK=0 finish

# BSG @ 1.3ns, util=68
make -C flow DESIGN_CONFIG=../asap7_ng45_testcases/config_bsg_chip_ng45.mk \
  FLOW_VARIANT=u68_p1p3_orng45work EQUIVALENCE_CHECK=0 finish
```

Outputs:

- `flow/results/nangate45/ariane/u68_p1p3_pdnw0p9_strict/6_final.gds`
- `flow/results/nangate45/bsg_chip/u68_p1p3_orng45work/6_final.gds`
