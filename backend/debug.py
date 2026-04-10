import sys
sys.path.append('c:/Users/ELCOT/Music/TNWISE/simulation/backend')
from simulation.grid import SmartGrid
from simulation.ems import EnergyManagementSystem
from simulation.scada import ScadaControlCenter

grid = SmartGrid()
ems = EnergyManagementSystem()
scada = ScadaControlCenter()

print("Initial cycle...")
grid.step()
ems.run(grid)

print("\nFailing LA1_2...")
# Use inject_failure() which automatically cuts edges + marks downstream isolated
if "LA1_2" in grid.nodes:
    grid.inject_failure("LA1_2")
else:
    print("Node LA1_2 not found - trying a random pole...")
    grid.random_failure()

grid.step()
ems.run(grid)

# scada flisr
scada_report = scada.execute_control_loop(grid, ems)
flisr_log = scada_report.get("flisr_log", [])
if flisr_log:
    print("SCADA FLISR log:")
    for entry in flisr_log:
        print(f"  [{entry['step']}] {entry['detail']}")

print("\nIsolated nodes:")
for nid, n in grid.nodes.items():
    if n.isolated:
        print(f"{nid} (failed: {n.failed})")
