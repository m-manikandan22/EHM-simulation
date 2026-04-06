import sys
sys.path.append('c:/Users/ELCOT/Music/TNWISE/simulation/backend')
from simulation.grid import SmartGrid
from simulation.ems import EnergyManagementSystem
from simulation.scada import ScadaControlCenter
from simulation.ai_models import simulate_ai_fault_detection

grid = SmartGrid()
ems = EnergyManagementSystem()
scada = ScadaControlCenter()

print("Initial cycle...")
grid.step()
ems.run(grid)

print("\nFailing LA1_2...")
grid.nodes["LA1_2"].failed = True
grid.nodes["LA1_2"].isolated = True

grid.step()
ems.run(grid)

# scada flisr
fault_analysis = simulate_ai_fault_detection(grid)
if fault_analysis["fault_detected"]:
    msgs = scada.run_scada_cycle(grid)
    for m in msgs:
        print("SCADA:", m)

print("\nIsolated nodes:")
for nid, n in grid.nodes.items():
    if n.isolated:
        print(f"{nid} (failed: {n.failed})")
