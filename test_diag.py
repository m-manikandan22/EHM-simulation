import sys
sys.path.insert(0, 'backend')
from simulation.grid import SmartGrid

g = SmartGrid()

print("=" * 60)
print("SOLAR GEN CHECK")
print("=" * 60)
solar = g.nodes['GEN_SOLAR']
print(f"  GEN_SOLAR: type={solar.node_type}, gen={solar.generation:.3f}, base={solar._base_generation:.3f}")
print(f"  timestep={g.timestep}, hour={g.get_time_of_day()}")

# Check what SOLAR_CURVE says at hour 0
import sys; sys.path.insert(0,'backend')
from simulation.grid import SOLAR_CURVE, WIND_CURVE
print(f"  SOLAR_CURVE[0] = {SOLAR_CURVE[0]}")
print(f"  SOLAR_CURVE[12] = {SOLAR_CURVE[12]} (noon)")
print(f"  WIND_CURVE[0]  = {WIND_CURVE[0]}")

print()
print("=" * 60)
print("STORAGE EDGES CHECK")
print("=" * 60)
for u, v, d in g.graph.edges(data=True):
    if 'STORAGE' in u or 'STORAGE' in v:
        print(f"  {u} --> {v}  active={d.get('active')} flow={d.get('flow',0):.2f} capacity={d.get('capacity')}")

print()
print("=" * 60)
print("FAILED/ISOLATED NODES")
print("=" * 60)
failed = [nid for nid,n in g.nodes.items() if n.failed]
isolated = [nid for nid,n in g.nodes.items() if n.isolated]
print(f"  Failed ({len(failed)}): {failed}")
print(f"  Isolated ({len(isolated)}): {isolated}")

print()
print("=" * 60)
print("POLES STATUS")
print("=" * 60)
for nid, n in g.nodes.items():
    if n.node_type == 'pole':
        print(f"  {nid:12s} failed={n.failed} isolated={n.isolated} voltage={n.voltage:.3f} recv={n.received_power:.2f}")
