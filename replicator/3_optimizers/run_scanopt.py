
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


parser = argparse.ArgumentParser(description="TSP run utility")


parser.add_argument(
    '--output',
    type=str,
    required=True,
)
args = parser.parse_args()

tech = Tech()

design = Design(tech)

design.readDb("../db/jpeg_sky130hd_postcts.odb")
tech.readLiberty("../db/sky130hd_tt.lib")
design.evalTclString("read_sdc ../db/jpeg_sky130hd_postcts.sdc")
library = design.getDb().getLibs()[1]
block = design.getBlock()
site = design.getDb().getLibs()[0].getSites()[0].getWidth()


def find_scan_flops():
    scan_flops = []
    for i in block.getInsts():
        if i.findITerm("SCD") is not None and (i.findITerm("Q") is not None or i.findITerm("Q_N") is not None):
            scan_flops.append(i)
    
    print(f"ScanFlops found with Length {len(scan_flops)}")
    print(Counter([s.getMaster().getName() for s in scan_flops]))
    return scan_flops, len(scan_flops)


def resolve_q(inst):
    if inst.findITerm("Q") is not None and inst.findITerm("Q").getNet() is not None and inst.findITerm("Q_N") is not None and inst.findITerm("Q_N").getNet() is not None:
        print("Both in use...")
    if inst.findITerm("Q") is not None and inst.findITerm("Q").getNet() is not None:
        return inst.findITerm("Q")
    if inst.findITerm("Q_N") is not None and inst.findITerm("Q_N").getNet() is not None:
        return inst.findITerm("Q_N")

flops, n_flops = find_scan_flops()

lines = []

lines.append('GROUP "Chain"')
lines.append('LOOSECELLs:')

for i in range(2):
    lines.append(f'\tCELL "ScanIO_{i}"')
    lines.append(f'\t\tINs:')
    lines.append(f'\t\t\tIN 0 0')
    lines.append(f'\t\tENDINs')
    lines.append(f'\t\tOUTs:')
    lines.append(f'\t\t\tOUT 0 0')
    lines.append(f'\t\tENDOUTs')
    lines.append(f'\tENDCELL\n')
    
    

ins = [(0,0),(0,0)]
outs = [(0,0),(0,0)]


for flop in flops:
    scd = flop.findITerm("SCD").getBBox()
    q = resolve_q(flop).getBBox()
    ins.append((scd.xMin(), scd.yMin()))
    outs.append((q.xMin(), q.yMin()))
    
    lines.append(f'\tCELL "{flop.getName()}"')
    lines.append(f'\t\tINs:')
    lines.append(f'\t\t\tIN {scd.xMin()} {scd.yMin()}')
    lines.append(f'\t\tENDINs')
    lines.append(f'\t\tOUTs:')
    lines.append(f'\t\t\tOUT {q.xMin()} {q.yMin()}')
    lines.append(f'\t\tENDOUTs')
    lines.append(f'\tENDCELL\n')
    pass
    
    
lines.append("""
ENDLOOSECELLs

LEGALINs:
    LEGALIN 0
ENDLEGALINs
LEGALOUTs:
    LEGALOUT 1
ENDLEGALOUTs

ENDGROUP
         """)
     

with open(f"{args.output}.in", "w") as file:
    file.writelines([l + "\n" for l in lines])
    
os.system(f"time ./ScanOptTest0.exe -rfile {args.output}.in -sfile {args.output}.stats -tfile {args.output}.tour -xfile {args.output}.x")

tour = []
with open(f"{args.output}.tour") as file:
    tour = [int(line.split()[0]) for line in file.readlines() if line.startswith(tuple(list("0123456789")))]

cost = 0
edges = []


def is_chain_end(u, v):
    if u == (0, 0) or v == (0, 0):
        return True
    return False


for a, b in itertools.pairwise(tour):
    first = outs[a] 
    second = ins[b]
    cost += abs(second[0] - first[0]) + abs(second[1] - first[1])
    edges.append((outs[a], outs[b]))
    
fig, ax = plt.subplots()
ax.add_collection(LineCollection([(u, v) for u, v in edges if not is_chain_end(u,v)], colors="red", path_effects=[pe.Stroke(linewidth=2, foreground="black"), pe.Normal()]))
ax.add_collection(LineCollection([(u, v) for u, v in edges if is_chain_end(u,v)], colors="black", linestyles='dashed'))
        
ax.set_xlim(-400, 900445)
ax.set_ylim(-245, 897840)
fig.savefig("scanopt.png", dpi=300)
    
print(f"Final cost: {cost}")