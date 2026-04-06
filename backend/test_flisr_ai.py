import sys
import os

# Add to Python path
sys.path.insert(0, os.path.abspath('.'))

from simulation.grid import SmartGrid

def run_tests():
    print("--- Running Verification Tests ---\n")
    grid = SmartGrid()
    # Force grid to use isolated tests if needed, or we can just manipulate the existing grid.
    
    # We will build a controlled subgraph for Test 1 & 2
    grid.nodes.clear()
    grid.graph.clear()
    
    from simulation.node import GridNode
    
    def make_node(id, type, pri=2, x=0, y=0):
        n = GridNode(id, node_type=type, x=x, y=y)
        n.priority = pri
        n.load = 1.0
        n.generation = 5.0 if type == "substation" else 0.0
        grid.nodes[id] = n
        grid.graph.add_node(id)
        return n
        
    s0 = make_node("Sub0", "substation", pri=2)
    s0.voltage = 1.0
    s0.isolated = False
    
    h1 = make_node("H1", "house", pri=3)   # disconnected node
    h1.isolated = True
    h1.failed = False
    
    make_node("P_A1", "service")
    make_node("P_A2", "service")
    
    make_node("P_B1", "service")

    # Path A: Low resistance (total 0.015), but 3 switches
    # Sub0 -[sw]- P_A1 -[sw]- P_A2 -[sw]- H1 (R = 0.005 each approx)
    grid.graph.add_edge("Sub0", "P_A1", active=False, switch_type="tie", resistance=0.005, capacity=10.0, flow=0.0)
    grid.graph.add_edge("P_A1", "P_A2", active=False, switch_type="tie", resistance=0.005, capacity=10.0, flow=0.0)
    grid.graph.add_edge("P_A2", "H1", active=False, switch_type="tie", resistance=0.005, capacity=10.0, flow=0.0)

    # Path B: Slightly higher resistance (total 0.02), 1 switch
    grid.graph.add_edge("Sub0", "P_B1", active=True, switch_type=None, resistance=0.01, capacity=10.0, flow=0.0)
    grid.graph.add_edge("P_B1", "H1", active=False, switch_type="tie", resistance=0.01, capacity=10.0, flow=0.0)

    # Run FLISR
    print("Test 1: Switching Optimization (3 switches vs 1 switch)")
    grid._reroute(failed_node_id="NONE")
    
    # Check which path got activated
    a_closed = sum([1 for u,v,d in grid.graph.edges(data=True) if "A" in u or "A" in v and d["active"]])
    b_closed = sum([1 for u,v,d in grid.graph.edges(data=True) if "B" in u or "B" in v and d["active"]])
    
    print(f"Path A active edges: {a_closed}")
    print(f"Path B active edges: {b_closed}")
    if b_closed > 0 and a_closed == 0:
        print("✅ Expected: AI chose Path B (fewer switches)")
    else:
        print("❌ FAILED: AI did not choose Path B")
        
    
    print("\nTest 2: Priority Override (Hospital on longer path)")
    # Reset
    for u,v,d in grid.graph.edges(data=True):
        if d.get("switch_type"): d["active"] = False
        
    s1 = make_node("Sub1", "substation")
    h2 = make_node("H2", "house", pri=3)
    h2.isolated = True
    
    # Path C: Short path, 1 switch (normal priority nodes)
    make_node("P_C1", "service")
    grid.graph.add_edge("Sub1", "P_C1", active=True, resistance=0.01, capacity=10.0, flow=0.0)
    grid.graph.add_edge("P_C1", "H2", active=False, switch_type="tie", resistance=0.01, capacity=10.0, flow=0.0)
    
    # Path D: Same total resistance, 2 switches, but has HOSPITAL (Priority 1)
    make_node("HOSP", "house", pri=1)
    grid.graph.add_edge("Sub1", "HOSP", active=False, switch_type="tie", resistance=0.01, capacity=10.0, flow=0.0)
    grid.graph.add_edge("HOSP", "H2", active=False, switch_type="tie", resistance=0.01, capacity=10.0, flow=0.0)
    
    grid._reroute(failed_node_id="NONE")
    
    c_closed = grid.graph["P_C1"]["H2"]["active"]
    d_closed = grid.graph["Sub1"]["HOSP"]["active"]
    
    if d_closed and not c_closed:
         print("✅ Expected: AI chose Path D (Hospital priority) despite being more switches.")
    else:
         print("❌ FAILED: AI did not prioritize hospital path.")
         
    
    print("\nTest 3: Suggestion Engine")
    grid2 = SmartGrid() # Load default real layout
    
    # Remove all ties
    tie_edges = [(u,v) for u,v,d in grid2.graph.edges(data=True) if d.get("is_tie_switch")]
    for u,v in tie_edges:
        grid2.graph.remove_edge(u, v)
        
    suggs = grid2.suggest_tie_lines()
    print(f"Suggestions generated: {len(suggs)}")
    if len(suggs) > 0 and 'Reduces single-point-of-failure risk (articulation point)' in suggs[0]['reason']:
        print(f"✅ Expected: Suggestion Engine identified {len(suggs)} vulnerabilities based on articulation points.")
        for s in suggs:
            print(f"   -> {s['source']} to {s['target']}")
    else:
        print("❌ FAILED: Suggestion Engine did not behave as expected.")

if __name__ == "__main__":
    run_tests()
