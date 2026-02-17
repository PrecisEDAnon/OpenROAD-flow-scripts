from ortools.constraint_solver import routing_enums_pb2 as rte
import ortools.constraint_solver.pywrapcp as cp 
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.collections import LineCollection

df = pd.read_csv("tsp_problem.csv").to_numpy()

mgr = cp.RoutingIndexManager(len(df)+1, 1, 0)

def dist_callback(ifrom, ito):
    ifrom = mgr.IndexToNode(ifrom)
    ito = mgr.IndexToNode(ito)
    cfrom = (0,0)
    if ifrom != 0:
        cfrom = tuple(df[ifrom - 1, 2:4])
        
    cto = (0,0)
    if ito != 0:
        cto = tuple(df[ito - 1, 0:2])
    
    return abs(cto[0] - cfrom[0]) + abs(cto[1] - cfrom[1])
    
print(len(df) + 1)
    

routing = cp.RoutingModel(mgr)

tcidx = routing.RegisterTransitCallback(dist_callback)
routing.SetArcCostEvaluatorOfAllVehicles(tcidx)

search_params = cp.DefaultRoutingSearchParameters()
search_params.first_solution_strategy  = rte.FirstSolutionStrategy.PATH_CHEAPEST_ARC
search_params.local_search_metaheuristic = (
    rte.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
search_params.time_limit.seconds = 21600
search_params.log_search = True

def get_edge(idxs):
    cfrom = (0,0)
    if idxs[0] != 0:
        cfrom = tuple(df[idxs[0] - 1, 2:4])
        
    cto = (0,0)
    if idxs[1] != 0:
        cto = tuple(df[idxs[1] - 1, 2:4])
        
    return cfrom, cto


def print_solution(manager, routing, solution):
    edges = [] 
    print(f"Cost objective : {solution.ObjectiveValue()} miles")
    index = routing.Start(0)
    plan_output = ""
    #plan_output = "Route for vehicle 0:\n"
    route_distance = 0
    while not routing.IsEnd(index):
        #plan_output += f" {manager.IndexToNode(index)} ->"
        previous_index = index
        index = solution.Value(routing.NextVar(index))
        edges.append(get_edge((manager.IndexToNode(previous_index), manager.IndexToNode(index))))
        route_distance += routing.GetArcCostForVehicle(previous_index, index, 0)
    plan_output += f"Cost: {route_distance}\n"
    print(plan_output)
    print(len(edges))
    
    fig, ax = plt.subplots()
    ax.add_collection(LineCollection(edges, colors="red", path_effects=[pe.Stroke(linewidth=2, foreground="black"), pe.Normal()]))
            
    ax.set_xlim(-400, 900445)
    ax.set_ylim(-245, 897840)
    fig.savefig("ort.png", dpi=300)
    
solution = routing.SolveWithParameters(search_params)
if solution:
    print_solution(mgr, routing, solution)
print(routing.status())


