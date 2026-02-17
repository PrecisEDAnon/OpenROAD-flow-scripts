
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


parser = argparse.ArgumentParser(description="Run with clocks")


parser.add_argument(
    '--clock-mode',
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

scan_flops, n_flops = find_scan_flops()

clk2_net = odb.dbNet_create(block, "clk2_net")

clk2_port = odb.dbBTerm_create(clk2_net, "clk2")
clk2_pin = odb.dbBPin_create(clk2_port)
odb.dbBox_create(clk2_pin, tech.getTech().getLayers()[10], 0, 0, 1200, 1200)
clk2_pin.setPlacementStatus("PLACED")

design.evalTclString("create_clock -period 1 clk2")

random.seed(100)

flops_to_reclock = []
if args.clock_mode == "split":
    flops_to_reclock = sorted(scan_flops, key=lambda i: i.getBBox().xMin())[:n_flops // 2]
elif args.clock_mode == "even":
    flops_to_reclock = random.sample(scan_flops, n_flops // 2)
else:
    raise Exception("Unknown Clock Mode")


for flop in flops_to_reclock:
    m = flop.getMaster().getName()
    n = flop.getName()
    
    it = flop.findITerm("CLK")
    it.disconnect()
    it.connect(clk2_net)
    flop.rename(flop.getName() + "__RECLK")
    
scan_flops, n_flops = find_scan_flops()



design.evalTclString("detailed_placement")
setup_cmd = f'set_dft_config -chain_count {args.k} -scanopt_time_limit 300 -max_imbalance 2 -scan_order_constraints_file "../constraints/chains{args.k}" -scan_order_metric PLACEMENT -scan_enable_name_pattern "scan_enable_{{}}" -clock_mixing no_mix'
design.evalTclString(setup_cmd)
design.evalTclString("report_dft_plan")
design.evalTclString("report_dft_config")
time_start = time.process_time()
design.evalTclString("execute_dft_plan")
print(f"DFT execute took {time.process_time() - time_start}s (CPU)")
design.evalTclString("global_route -verbose -allow_congestion -congestion_iterations 0")
design.evalTclString(f"write_db {args.output}/post.odb")
    
    
scan_flops, n_flops = find_scan_flops()

S = chainlib.getAllChains(block)
chainlib.printChainsStats(block, S)
chainlib.graph(block, S, f"{args.output}/plot.png", "__RECLK", "reclocked")