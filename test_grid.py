import sys
sys.path.insert(0, 'backend')
from simulation.grid import SmartGrid

g = SmartGrid()
total = len(g.nodes)
failed = [nid for nid, n in g.nodes.items() if n.failed]
isolated = [nid for nid, n in g.nodes.items() if n.isolated]
online = [nid for nid, n in g.nodes.items() if not n.failed and not n.isolated]
edges_with_flow = [(u, v, d['flow']) for u, v, d in g.graph.edges(data=True) if d.get('flow', 0) > 0]

print(f"Total nodes  : {total}")
print(f"Online       : {len(online)}")
print(f"Failed       : {len(failed)}  -> {failed[:5]}")
print(f"Isolated     : {len(isolated)} -> {isolated[:5]}")
print(f"Edges w/flow : {len(edges_with_flow)}")
print()
print("--- Sample voltages ---")
for nid, n in list(g.nodes.items())[:10]:
    print(f"  {nid:20s}  type={n.node_type:20s}  V={n.voltage:.3f}  isolated={n.isolated}")
