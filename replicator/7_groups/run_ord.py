
from openroad import Design, Tech
import openroad
from odb import *
import odb 
import os
import argparse
from pathlib import Path
from glob import glob
import random, math
import runpy
import json
import time
import sys
import itertools
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.collections import LineCollection
import numpy as np
import networkx as nx
from collections import Counter
sys.path.append("..")
import  src.chainlib as chainlib


parser = argparse.ArgumentParser(description="Run with locations")


parser.add_argument(
    '--group-mode',
    type=str,
    required=True
)

parser.add_argument(
    '--k',
    type=int,
    required=True
)

parser.add_argument(
    '--output',
    type=str,
    required=True
)
args = parser.parse_args()

tech = Tech()

os.makedirs(args.output, exist_ok=True)

design = Design(tech)

design.readDb("../db/jpeg_sky130hd_postcts.odb")
tech.readLiberty("../db/sky130hd_tt.lib")
design.evalTclString("read_sdc ../db/jpeg_sky130hd_postcts.sdc")
library = design.getDb().getLibs()[1]
block = design.getBlock()
site = design.getDb().getLibs()[0].getSites()[0].getWidth()

random.seed(100)

def find_scan_flops():
    scan_flops = []
    for i in block.getInsts():
        if i.findITerm("SCD") is not None and (i.findITerm("Q") is not None or i.findITerm("Q_N") is not None):
            scan_flops.append(i)
    
    print(f"ScanFlops found with Length {len(scan_flops)}")
    print(Counter([s.getMaster().getName() for s in scan_flops]))
    return scan_flops, len(scan_flops)

def resolve_q(inst):
    if inst.findITerm("Q") is not None and inst.findITerm("Q").getNet() is not None:
        return inst.findITerm("Q").getName()
    if inst.findITerm("Q_N") is not None and inst.findITerm("Q_N").getNet() is not None:
        return inst.findITerm("Q_N").getName()


core_llx = block.getBBox().xMin()
core_lly = block.getBBox().yMin()
core_urx = block.getBBox().xMax()
core_ury = block.getBBox().yMax()

ins = [port for port in block.getBTerms() if port.getName().startswith("scan_in")][:args.k]
outs = [port for port in block.getBTerms() if port.getName().startswith("scan_out")][:args.k]



def updatePin(bterm, x, y):
    pin = bterm.getBPins()[0]
    box = pin.getBoxes()[0]
        
    odb.dbBox_create(pin, box.getTechLayer(), x, y, x + (box.xMax() - box.xMin()), y + (box.yMax() - box.yMin()))
    odb.dbBox_destroy(box)
    
for port in ins:
        updatePin(port, 0, 0)

for port in outs:
    updatePin(port, 0, 0)


scan_flops, n_flops = find_scan_flops()

flops_to_regroup = []
if args.group_mode == "split":
    flops_to_regroup = sorted(scan_flops, key=lambda i: i.getBBox().xMin())[:n_flops // 5]
elif args.group_mode == "even":
    flops_to_regroup = random.sample(scan_flops, n_flops // 5)
elif args.group_mode == "overlap":
    flops_to_regroup = random.sample(scan_flops, n_flops // 5)
else:
    raise Exception("Unknown Group Mode")


with open(f"{args.output}/constraints", "w") as file:  
    for idx, (i, o) in enumerate(zip(ins, outs)):
        file.write(f"chain c{idx} begin {i.getName()} end {o.getName()}\n")
        
    group1 = set()
    for flop in flops_to_regroup:
        flop.rename(flop.getName() + "__GROUP1")
        group1.add(flop.getName())
    file.write(f"group group1 {' '.join(list(group1))}\n")
    
    group2 = []
    for flop in scan_flops:
        if args.group_mode == "overlap" or flop.getName() not in group1:
            group2.append(flop.getName())
    file.write(f"group group2 {' '.join(group2)}\n")
            
    
        
    file.write("before group1 group2\n")




setup_cmd = f'set_dft_config -chain_count {args.k} -max_imbalance 200 -scanopt_time_limit 300 -scan_order_constraints_file "{args.output}/constraints" -scan_order_metric PLACEMENT -scan_enable_name_pattern "scan_enable_{{}}" -clock_mixing no_mix'
design.evalTclString(setup_cmd)
design.evalTclString("report_dft_plan")
design.evalTclString("report_dft_config")
time_start = time.process_time()
design.evalTclString("execute_dft_plan")
print(f"DFT execute took {time.process_time() - time_start}s (CPU)")
design.evalTclString("global_route -verbose -allow_congestion -congestion_iterations 0")
design.evalTclString(f"write_db {args.output}/post.odb")
    

S = chainlib.getAllChains(block)
chainlib.printChainsStats(block, S)

def _parse_constraints_groups(path):
    groups = {}
    before = []
    with open(path, "r") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            toks = line.split()
            if not toks:
                continue
            kw = toks[0].lower()
            if kw == "group":
                if len(toks) < 3:
                    continue
                name = toks[1]
                start = 2
                # Optional integer priority.
                if len(toks) >= 4:
                    try:
                        int(toks[2])
                        start = 3
                    except Exception:
                        start = 2
                groups[name] = toks[start:]
            elif kw == "before" and len(toks) >= 3:
                before.append((toks[1], toks[2]))
    return groups, before

def _chain_flop_order(chain):
    insts = []
    for node in chain:
        if "/SCD" in node:
            insts.append(node.split("/", 1)[0])
    return insts

def _contiguous_span(indices):
    if not indices:
        return True, None
    mn = min(indices)
    mx = max(indices)
    return (mx - mn + 1 == len(indices)), (mn, mx, len(indices))

# Assert group contiguity / before constraints (to catch regressions and avoid
# visual misinterpretation of physical dispersion as scan-order non-contiguity).
groups, befores = _parse_constraints_groups(f"{args.output}/constraints")
if groups:
    gsets = {k: set(v) for k, v in groups.items()}
    if set(gsets.keys()) == {"group1", "group2"}:
        if gsets["group1"] & gsets["group2"]:
            raise RuntimeError("constraints invalid: group1 and group2 overlap")
        all_grouped = gsets["group1"] | gsets["group2"]
    else:
        all_grouped = None

    for ci, chain in enumerate(S):
        insts = _chain_flop_order(chain)
        pos = {name: i for i, name in enumerate(insts)}

        if all_grouped is not None:
            missing = [name for name in insts if name not in all_grouped]
            if missing:
                raise RuntimeError(
                    f"constraints invalid: chain {ci} has {len(missing)} ungrouped flops"
                )

        for gname, members in gsets.items():
            idxs = [pos[m] for m in members if m in pos]
            idxs.sort()
            ok, span = _contiguous_span(idxs)
            if not ok:
                raise RuntimeError(
                    f"group not contiguous: {gname} in chain {ci} (span={span})"
                )

        for a, b in befores:
            if a not in gsets or b not in gsets:
                continue
            a_idxs = [pos[m] for m in gsets[a] if m in pos]
            b_idxs = [pos[m] for m in gsets[b] if m in pos]
            if not a_idxs or not b_idxs:
                continue
            if max(a_idxs) >= min(b_idxs):
                raise RuntimeError(
                    f"before violated: {a} before {b} in chain {ci} "
                    f"(max({a})={max(a_idxs)} min({b})={min(b_idxs)})"
                )

chainlib.graph(block, S, f"{args.output}/plot.png", "__GROUP1", "in Group 1")
