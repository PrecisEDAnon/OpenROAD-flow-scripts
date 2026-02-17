
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

setup_cmd = f'set_dft_config -chain_count 1 -max_imbalance 2 -scanopt_time_limit 300 -scan_order_constraints_file "../constraints/chains1"  -scan_in_name_pattern "scan_in_{{}}" -scan_order_metric PLACEMENT -scan_out_name_pattern "scan_out_{{}}" -scan_enable_name_pattern "scan_enable_{{}}" -clock_mixing no_mix'
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

S = chainlib.getAllChains(block)

chainlib.printChainsStats(block, S)

chainlib.graph(block, S, f"{args.output_plot}")
