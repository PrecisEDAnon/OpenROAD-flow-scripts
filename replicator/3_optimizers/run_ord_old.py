
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
    '--output-plot',
    type=str,
    required=True,
    help="output plot image",
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



core_llx = block.getBBox().xMin()
core_lly = block.getBBox().yMin()
core_urx = block.getBBox().xMax()
core_ury = block.getBBox().yMax()

scan_flops, n_flops = find_scan_flops()

def resolve_q(inst):
    if inst.findITerm("Q") is not None and inst.findITerm("Q").getNet() is not None:
        return inst.findITerm("Q").getName()
    if inst.findITerm("Q_N") is not None and inst.findITerm("Q_N").getNet() is not None:
        return inst.findITerm("Q_N").getName()

            
design.evalTclString("detailed_placement")

setup_cmd = f'set_dft_config -chain_count 1 -max_imbalance 2 -scan_order_constraints_file "../constraints/chains1"  -scan_in_name_pattern "scan_in_{{}}" -scan_order_metric PLACEMENT -scan_out_name_pattern "scan_out_{{}}" -scan_enable_name_pattern "scan_enable_{{}}" -clock_mixing no_mix'
design.evalTclString(setup_cmd)
design.evalTclString("report_dft_plan")
design.evalTclString("report_dft_config")
time_start = time.process_time()
design.evalTclString("execute_dft_plan")
print(f"DFT execute took {time.process_time() - time_start}s (CPU)")
design.evalTclString("global_route -verbose -allow_congestion -congestion_iterations 0")
    
scan_flops, n_flops = find_scan_flops()

with open("tsp_problem.csv", "w") as file:
    file.write("dx,dy,qx,qy\n")

    for flop in scan_flops:
        scd = flop.findITerm("SCD").getBBox()
        q = block.findITerm(resolve_q(flop)).getBBox()
        #scd = flop.getBBox()
        #q = flop.getBBox()
        file.write(f"{scd.xMin()},{scd.yMin()},{q.xMin()},{q.yMin()}\n")


def followChain(inst, processed=set()):
    if inst.getName() in processed:
        return set()
    chain = set()
    ms = inst.getMaster().getName()    
    processed.add(inst.getName())
    if "sdf" not in ms and "sed" not in ms and "buf" not in ms:
        return set()
    
    if "sdf" in ms or "sed" in ms:
        box = inst.getBBox()
        chain = set([(box.xMin(), box.yMin(), inst.findITerm("SCD").getName())])
    
    input = inst.findITerm("SCD") or inst.findITerm("A")
    its = input.getNet().getITerms()
    bts = input.getNet().getBTerms()
    if len(bts) != 0:
        box = bts[0].getBBox()
        chain.add((box.xMin(), box.yMin(), bts[0].getName()))
    
    for it in its:
        if it.getInst().getName() == inst.getName() or it.getIoType() != "OUTPUT":
            continue
        
        if ("sdf" in it.getInst().getMaster().getName() or "sed" in it.getInst().getMaster().getName()):
            box = it.getInst().getBBox()
            chain.add((box.xMin(), box.yMin(), it.getName()))
            
        elif "sdf" not in it.getInst().getMaster().getName() and "sed" not in it.getInst().getMaster().getName():
            chain |= followChain(it.getInst(), processed)
    
    return chain

    
chains = [list(followChain(i)) for i in scan_flops]
for b in block.getBTerms():
    if "scan_out" in b.getName():
        for it in b.getNet().getITerms():
            ms = it.getInst().getMaster().getName()
            if  "sdf" not in ms and "sed" not in it.getInst().getMaster().getName():
                continue
            inst = it.getInst()
            ibox = inst.getBBox()
            bbox =  b.getBBox()
            chain = followChain(inst)
            chain = [(bbox.xMin(), bbox.yMin(), b.getName()), (ibox.xMin(), ibox.yMin(), resolve_q(inst))]
            chains.append(chain)
            

G = nx.Graph()

for c in chains:
    if len(c) != 2:
        print(len(c))
        continue
    G.add_node(c[0][0:2], name=c[0][2])
    G.add_node(c[1][0:2], name=c[1][2])
    G.add_edge(c[0][0:2], c[1][0:2])


S = [G.subgraph(c).copy() for c in nx.connected_components(G)]
print(f"Found {len(S)} chains from {len([n for n in G if G.degree[n] > 1])} flops and {len([n for n in G if G.degree[n] == 1])} ports.")
print(f"The largest has {len(max(S, key=len))} pins. The smallest has {len(min(S, key=len))} pins.")


def is_chain_end(chain, u, v):
    if chain.degree[u] == 1 or chain.degree[v] == 1:
        return True
    if u == (0, 0) or v == (0, 0):
        return True
    return False


fig, ax = plt.subplots()
for chain, color in zip(S, ["red", "blue", "purple", "orange", "green"] * 100):
    core = [(u,v) for u,v in chain.edges if not is_chain_end(chain, u, v)]
    ends = [(u,v) for u,v in chain.edges if is_chain_end(chain, u, v)]
    ax.add_collection(LineCollection(core, colors=color, path_effects=[pe.Stroke(linewidth=2, foreground="black"), pe.Normal()]))
    ax.add_collection(LineCollection(ends, colors="black", linestyles='dashed'))
    for node in chain.edge_subgraph(ends):
        if chain.degree[node] != 1:
            data = G.nodes(data=True)[node]
            ax.scatter(node[0], node[1], s=7, c=color, path_effects=[pe.Stroke(linewidth=4, foreground="black"), pe.Stroke(linewidth=3, foreground=color)], zorder=50000)

        
            
ax.set_xlim(core_llx, core_urx)
ax.set_ylim(core_lly, core_ury)
fig.savefig(args.output_plot, dpi=300)

    
for chain in S:
    print(f"Have a chain with {len(chain)} flops/pins")


costs = 0

edges = []

for c in chains:
    if len(c) != 2:
        continue
    
    t_1 = c[0][2]
    t_2 = c[1][2]
    box_1 = (0,0)
    box_2 = (0,0)

    if block.findITerm(t_1) is not None:
        box_1 = (block.findITerm(t_1)).getBBox()
        #box_1 = block.findITerm(t_1).getInst().getBBox()
        box_1 = (box_1.xMin(), box_1.yMin())     
        
    if block.findITerm(t_2) is not None:
        box_2 = (block.findITerm(t_2)).getBBox()
        #box_2 = block.findITerm(t_2).getInst().getBBox()
        box_2 = (box_2.xMin(), box_2.yMin())

    # This construct should only print pairs containing the scan IO ports.
    if (t_1.endswith("Q") or t_1.endswith("Q_N")) and t_2.endswith("SCD"):
        pass
    elif (t_2.endswith("Q") or t_2.endswith("Q_N")) and t_1.endswith("SCD"):
        pass
    else:
        print(t_1, t_2)
    
    x = abs(box_1[0] - box_2[0])
    y = abs(box_1[1] - box_2[1])
    
    edges.append((box_1, box_2))
    
    costs += x + y

print(f"Cost from DRT: {costs}")


fig, ax = plt.subplots()
ax.add_collection(LineCollection(edges, colors="red", path_effects=[pe.Stroke(linewidth=2, foreground="black"), pe.Normal()]))
        
            
ax.set_xlim(core_llx, core_urx)
ax.set_ylim(core_lly, core_ury)
fig.savefig("2_" + args.output_plot, dpi=300)

#print(core_llx, core_lly, core_urx, core_ury)