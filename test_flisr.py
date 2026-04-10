import sys
sys.path.insert(0, 'backend')
from simulation.grid import SmartGrid

g = SmartGrid()

print("=" * 55)
print("TEST 1: INITIAL STATE")
print("=" * 55)
iso = [nid for nid, n in g.nodes.items() if n.isolated]
failed = [nid for nid, n in g.nodes.items() if n.failed]
flows = [(u,v,d['flow']) for u,v,d in g.graph.edges(data=True) if d.get('flow',0)>0.01]
print(f"  Nodes:   {len(g.nodes)} total, {len(iso)} isolated, {len(failed)} failed")
print(f"  Flows:   {len(flows)} active edges")
gen_rcv = g.nodes['GEN_NUCLEAR'].received_power
gen_gen = g.nodes['GEN_NUCLEAR'].generation
print(f"  GEN_NUCLEAR received={gen_rcv:.2f}  generation={gen_gen:.2f}")
match = abs(gen_rcv - gen_gen) < 0.01
print(f"  No backflow: {'PASS' if match else 'FAIL -- BACKFLOW BUG'}")

print()
print("=" * 55)
print("TEST 2: FAIL P_C2 -> P_C3 + IND0 isolated")
print("=" * 55)
g.inject_failure("P_C2")
g.update_power_flow()
print(f"  P_C2 failed:   {g.nodes['P_C2'].failed}")
print(f"  P_C3 isolated: {g.nodes['P_C3'].isolated}  (expect True)")
print(f"  IND0 isolated: {g.nodes['IND0'].isolated}  (expect True)")
print(f"  P_A1 healthy:  {not g.nodes['P_A1'].isolated and not g.nodes['P_A1'].failed}")
print(f"  HOSP healthy:  {not g.nodes['HOSP'].isolated and not g.nodes['HOSP'].failed}")

print()
print("=" * 55)
print("TEST 3: FLISR - close P_B3->P_C3 tie switch")
print("=" * 55)
tie = g.graph.get_edge_data("P_B3", "P_C3")
print(f"  Tie before: active={tie.get('active')}, type={tie.get('switch_type')}")
g.graph["P_B3"]["P_C3"]["active"] = True
g.graph["P_B3"]["P_C3"]["switch_status"] = "closed"
g.update_power_flow()
print(f"  Tie after:  active={g.graph['P_B3']['P_C3']['active']}")
pc3 = g.nodes['P_C3']
ind0 = g.nodes['IND0']
print(f"  P_C3 isolated={pc3.isolated}  voltage={pc3.voltage:.3f}  (expect pow=online)")
print(f"  IND0 isolated={ind0.isolated}  voltage={ind0.voltage:.3f}")

print()
print("=" * 55)
print("TEST 4: BACKFLOW CHECK (all generators)")
print("=" * 55)
all_ok = True
for gid in ['GEN_SOLAR','GEN_WIND','GEN_NUCLEAR','GEN_COAL','GEN_GAS']:
    n = g.nodes[gid]
    backflow = abs(n.received_power - n.generation) > 0.01
    ok = "PASS" if not backflow else "FAIL-BACKFLOW"
    print(f"  {gid}: gen={n.generation:.2f}  recv={n.received_power:.2f}  [{ok}]")
    if backflow:
        all_ok = False

print()
print("=" * 55)
print("TEST 5: RESET -> 49 clean")
print("=" * 55)
g.reset()
iso = [nid for nid, n in g.nodes.items() if n.isolated]
failed2 = [nid for nid, n in g.nodes.items() if n.failed]
print(f"  Total={len(g.nodes)}, failed={len(failed2)}, isolated={len(iso)}")
reset_ok = len(failed2)==0 and len(iso)==0
print(f"  Reset: {'PASS' if reset_ok else 'FAIL'}")

print()
print("=" * 55)
print("ALL TESTS PASSED" if (match and all_ok and reset_ok) else "SOME TESTS FAILED")
print("=" * 55)
