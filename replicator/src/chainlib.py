
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
            
sys.setrecursionlimit(30000)


def followChainAllInputsFromNet(inst, net):
    its = net.getITerms()
    bts = net.getBTerms()
    
    for bt in bts:
        if bt.getName().startswith("scan_in"):
            return [bt.getName()]
        
    if len(bts) != 0: # if we found bterms but none are scanin, then we have made a poor termination.
        return None
    
    for it in its:
        if it.getInst().getName() == inst.getName() or it.getIoType() != "OUTPUT":
            continue
        
        subchain = followChainITerm(it)
        if subchain is not None:
            return subchain

    return None
    
    

def followChainITerm(iterm):
    mterm = iterm.getMTerm().getName()
    inst = iterm.getInst()
    master = inst.getMaster().getName()    
    
    # Flop traversal
    if "sdf" in master or "sed" in master:
        #print(f"Found flop term  {iterm.getName()}")
        if mterm.startswith("Q"): # We are "inside" the flop, traversing the arc from Q/Q_N to SCD but not recording it. We broadly guarantee that this exists.
            #return followChainITerm(inst.findITerm("SCD"))
            return [iterm.getName()] + followChainITerm(inst.findITerm("SCD"))
            #We can do this recursively, as above, or we can do it like so to save stack depth
            #iterm = inst.findITerm("SCD")
            #mterm = iterm.getMTerm().getName()
            
        if mterm == "SCD": 
            return [iterm.getName()] + followChainAllInputsFromNet(inst, iterm.getNet())
        else:
            return None
            
    elif "buf" in master and inst.getITerm("A") is not None: # We traverse buffers but do not record them
        #print(f"Found buf term  {iterm.getName()}")
        return followChainAllInputsFromNet(inst, inst.getITerm("A").getNet())
    else: # Any other kind of cell should be ignored
        return None
    

def followChain(scan_out) -> list[str] | None:
    if "scan_out" in scan_out.getName():
        #print(f"Found potential scanchain output  {scan_out.getName()}")
        for it in scan_out.getNet().getITerms():
            ms = it.getInst().getMaster().getName()
            if  "sdf" not in ms and "sed" not in it.getInst().getMaster().getName():
                continue
            #print(f"-> Found potential instance connection to that output  {it.getName()} ({ms})")
            chain = followChainITerm(it) 
            if chain is not None:
                final_chain = [scan_out.getName()] + chain
                #print(f"-> Resolved a chain with {len(final_chain)} cities (incl. ports)")
                return list(reversed([scan_out.getName()] + chain))
        #print("-> This resulted in no chain. The chain may be empty or unused.")
    
    return None
            
def getAllChains(block) -> list[list[str]]:
    chains = []
    
    for bt in block.getBTerms():
        chain = followChain(bt) 
        if chain is not None:
            chains.append(chain)
            
    return chains


# Find a chain's city in OpenDB regardless of if it is an ITerm or BTerm
def resolveChainNode(block, node):
    iterm = block.findITerm(node)
    if iterm is not None:
        return iterm
    
    bterm = block.findBTerm(node)
    if bterm is not None:
        return bterm
    
    return None


def resolveChainNodeRenderableBox(block, node):
    iterm = block.findITerm(node)
    if iterm is not None:
        return iterm.getInst().getBBox()
    
    bterm = block.findBTerm(node)
    if bterm is not None:
        return bterm.getBBox()
    
    return None

def resolveChainNodeCostableBox(block, node):
    iterm = block.findITerm(node)
    if iterm is not None:
        return iterm.getBBox()
    
    bterm = block.findBTerm(node)
    if bterm is not None:
        return bterm.getBBox()
    
    return None
        
    
def isInternalArc(e):
    n0_split = e[0].split("/")
    n1_split = e[1].split("/")
    n0_name = n0_split[0] if len(n0_split) == 2 else ""
    n1_name = n1_split[0] if len(n1_split) == 2 else ""
    return n0_name == n1_name

def getNodeRenderableCoordinate(block, node):
    bbox = resolveChainNodeRenderableBox(block, node)
    return bbox.xMin(), bbox.yMin()

def getNodeCostableCoordinate(block, node):
    bbox = resolveChainNodeCostableBox(block, node)
    return bbox.xMin(), bbox.yMin()

def getEdgeAsRenderableCoords(block, edge):
    return getNodeRenderableCoordinate(block, edge[0]), getNodeRenderableCoordinate(block, edge[1]) 

def getEdgeAsCostableCoords(block, edge):
    return getNodeCostableCoordinate(block, edge[0]), getNodeCostableCoordinate(block, edge[1]) 

def getChainRenderableEdges(block, chain):
    return [getEdgeAsRenderableCoords(block, e) for e  in itertools.pairwise(chain) if not isInternalArc(e)]

def getChainCostableEdges(block, chain):
    return [getEdgeAsCostableCoords(block, e) for e in itertools.pairwise(chain) if not isInternalArc(e)]
    #return [getEdgeAsRenderableCoords(block, e) for e in itertools.pairwise(chain) if not isInternalArc(e)]

def countChainUniqueCities(chain):
    cities = set()
    for e in itertools.pairwise(chain):
        n0_split = e[0].split("/")
        n1_split = e[1].split("/")
        n0_name = n0_split[0] if len(n0_split) == 2 else e[0]
        n1_name = n1_split[0] if len(n1_split) == 2 else e[1]
        cities.update([n0_name, n1_name])    
    return len(list(cities))

def getChainCost(block, chain):
    edges = getChainCostableEdges(block, chain)
    cost = 0
    for edge in edges:
        cost += abs(edge[0][0] - edge[1][0])
        cost += abs(edge[0][1] - edge[1][1])
    return cost

def getChainsCost(block, chains):
    return sum([getChainCost(block, c) for c in chains])

def printChainsStats(block, chains):
    print(f"Found {len(chains)} chains:")
    for i, c in enumerate(chains):
        print(f"- Chain {i} traverses {c[0]} -> {c[-1]}  with {countChainUniqueCities(c)} cities total ({len(c)} arcs). ")
    print(f"Total cost: {getChainsCost(block, chains)}")
        
def graph(block, chains, output, highlight=None, highlight_label=None):
    llx = block.getBBox().xMin()
    lly = block.getBBox().yMin()
    urx = block.getBBox().xMax()
    ury = block.getBBox().yMax()
    
    fig, ax = plt.subplots()
    highlights_count = 0
    
    for chain, color in zip(chains, ["red", "blue", "purple", "orange", "green"] * 100):
            
        renderable = getChainRenderableEdges(block, chain)
        core = renderable[1:-1]
        ends = [renderable[0], renderable[-1]]
        ax.add_collection(LineCollection(core, colors=color, path_effects=[pe.Stroke(linewidth=2, foreground="black"), pe.Normal()]))
        ax.add_collection(LineCollection(ends, colors="black", linestyles='dashed'))
        ax.scatter(ends[0][1][0], ends[0][1][1], s=7, c=color, path_effects=[pe.Stroke(linewidth=4, foreground="black"), pe.Stroke(linewidth=3, foreground=color)], zorder=100000)
        ax.scatter(ends[1][0][0], ends[1][0][1], s=7, c=color, path_effects=[pe.Stroke(linewidth=4, foreground="black"), pe.Stroke(linewidth=3, foreground=color)], zorder=100000)
    
        for node in chain:
            if highlight is not None and highlight in node:
                highlights_count += 1
                coord = getNodeRenderableCoordinate(block, node)
                ax.scatter(coord[0], coord[1], s=1, c="green", zorder=150000)
            
        
            
    ax.set_xlim(llx, urx)
    ax.set_ylim(lly, ury)
    ax.set_aspect('equal', adjustable='box')
    fig.savefig(output, dpi=300)

    if highlight is not None:
        if highlight_label is None:
            highlight_label = ""
        print(f"Found {highlights_count} flops marked {highlight_label}")