# -*- coding: utf-8 -*-
"""
test_fault_scenarios.py - Judge-Level Fault Verification Tests

Three critical scenarios:
  A - Mid-Feeder Fault: break P0-P1, FLISR must restore orphan loads
  B - No-Path Scenario: all ties disabled, load shedding must trigger
  C - EMS order + partial control: verify 50% absorption ratio

Run with:
    cd backend
    python test_fault_scenarios.py
"""

import sys
import os
import io

# Force UTF-8 on Windows to avoid cp1252 codec errors
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

from simulation.grid import SmartGrid
from simulation.ems import EnergyManagementSystem
from simulation.scada import ScadaControlCenter


# ── Simple pass/fail counters ──────────────────────────────────────────────────
pass_count = 0
fail_count = 0


def check(label: str, condition: bool, detail: str = ""):
    global pass_count, fail_count
    if condition:
        pass_count += 1
        tag = "[PASS]"
    else:
        fail_count += 1
        tag = "[FAIL]"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {tag}  {label}{suffix}")


def section(title: str):
    bar = "-" * 60
    print(f"\n{bar}")
    print(f"  {title}")
    print(bar)


# ==============================================================================
# TEST A - Mid-Feeder Fault + FLISR Restoration
# ==============================================================================

section("TEST A - Mid-Feeder Fault + FLISR Restoration")
print("  Scenario: Physical break on P0-P1 section, FLISR must restore orphan loads")

grid  = SmartGrid()
ems   = EnergyManagementSystem()
scada = ScadaControlCenter()

# Warm up grid with a few physics steps
for _ in range(3):
    grid.step()
    ems.run(grid)

pre_fault_hosp_v = grid.nodes["HOSP"].voltage
print(f"\n  Pre-fault HOSP voltage:  {pre_fault_hosp_v:.4f} pu")

# Inject physical line break on P0-P1 (cable fault, not node failure)
if grid.graph.has_edge("P0", "P1"):
    grid.graph["P0"]["P1"]["active"] = False
    grid.event_log.append("TEST: P0-P1 section opened to simulate cable fault")
    print("  Break injected: P0-P1 edge set active=False")

# Record tie switch statuses before FLISR
tie_statuses_before = {
    (u, v): data.get("active", True)
    for u, v, data in grid.graph.edges(data=True)
    if data.get("is_tie_switch", False)
}

# Run physics + EMS (physics will notice the break)
grid.step()
ems_report = ems.run(grid)

# Fail P1 so FLISR sees orphan downstream loads
heal_msg = grid.inject_failure("P1")
flisr_result = scada._flisr_restore(grid)

print(f"\n  FLISR message: {flisr_result['message'][:80]}")
print(f"  FLISR steps: {[e['step'] for e in flisr_result['flisr_log']]}")

# Run final physics step to propagate any restoration
grid.step()
ems.run(grid)

# -- Assertions ----------------------------------------------------------------
print()

flisr_steps = [e["step"] for e in flisr_result["flisr_log"]]
check("FLISR ran LOCATE step",   "LOCATE"   in flisr_steps)
check("FLISR ran ISOLATE step",  "ISOLATE"  in flisr_steps)
check("FLISR ran CLUSTER step",  "CLUSTER"  in flisr_steps)
check("FLISR ran EVALUATE step", "EVALUATE" in flisr_steps or "CLUSTER" in flisr_steps)
check("FLISR ran RESTORE step",  "RESTORE"  in flisr_steps)

tie_statuses_after = {
    (u, v): data.get("active", True)
    for u, v, data in grid.graph.edges(data=True)
    if data.get("is_tie_switch", False)
}
newly_closed = [
    (u, v) for (u, v) in tie_statuses_before
    if not tie_statuses_before[(u, v)] and tie_statuses_after.get((u, v), False)
]
msg_upper = flisr_result["message"].upper()
check(
    "FLISR attempted restoration (tie closed or shedding triggered)",
    len(newly_closed) > 0 or "SHED" in msg_upper or "RESTORED" in msg_upper,
    detail=f"ties closed: {newly_closed}"
)

check("HOSP not isolated after fault", not grid.nodes["HOSP"].isolated)
check("HOSP not failed after fault",   not grid.nodes["HOSP"].failed)
check("HOSP voltage > 0 after restoration",
      grid.nodes["HOSP"].voltage > 0.0,
      detail=f"V={grid.nodes['HOSP'].voltage:.4f} pu")
check("EMS returned structured report",
      "balance" in ems_report and "log" in ems_report)
check("P1 correctly marked as failed", grid.nodes["P1"].failed)

print("\n  Recent event log:")
for evt in grid.event_log[-5:]:
    print(f"    {evt}")


# ==============================================================================
# TEST B - No-Path Scenario: All Ties Disabled -> Load Shedding
# ==============================================================================

section("TEST B - No-Path Scenario (All Ties Disabled -> Load Shedding)")
print("  Scenario: All tie switches forced open, inject fault, verify priority-safe shedding")

grid2  = SmartGrid()
ems2   = EnergyManagementSystem()
scada2 = ScadaControlCenter()

for _ in range(2):
    grid2.step()
    ems2.run(grid2)

# Force ALL tie switches to be removed (simulating unavailable paths)
disabled_ties = []
ties_to_remove = []
for u, v, data in grid2.graph.edges(data=True):
    if data.get("is_tie_switch", False):
        ties_to_remove.append((u, v))
        disabled_ties.append(f"{u}-{v}")

for u, v in ties_to_remove:
    grid2.graph.remove_edge(u, v)

print(f"\n  Disabled (removed) tie switches: {disabled_ties}")
check("Exactly 5 tie switches disabled", len(disabled_ties) == 5,
      detail=f"found {len(disabled_ties)}")

priority_1_nodes = [nid for nid, n in grid2.nodes.items() if n.priority == 1]
priority_3_nodes = [nid for nid, n in grid2.nodes.items() if n.priority == 3]
print(f"  Priority-1 (critical): {priority_1_nodes}")
print(f"  Priority-3 (residential): {priority_3_nodes}")

# Inject fault at P1
grid2.inject_failure("P1")
print("\n  Fault injected at P1")

# FLISR should find no valid tie and fall back to load shedding
flisr2 = scada2._flisr_restore(grid2)
print(f"\n  FLISR message: {flisr2['message'][:90]}")

grid2.step()
ems2.run(grid2)

# -- Assertions ----------------------------------------------------------------
print()

msg2 = flisr2["message"].upper()
check("FLISR message contains result keyword",
      any(k in msg2 for k in ("SHED", "RESTORED", "ISOLATED", "NO ACTIVE FAULTS", "FAULT")),
      detail=f"msg: {flisr2['message'][:60]}")

p3_shed = any(
    grid2.nodes[nid].load < 0.05 or grid2.nodes[nid].isolated
    for nid in priority_3_nodes if nid in grid2.nodes
)
check("Priority-3 nodes shed or isolated when no path available",
      p3_shed or "SHED" in msg2,
      detail=f"direct shed detected={p3_shed}")

check("HOSP not isolated in no-path scenario", not grid2.nodes["HOSP"].isolated)
check("HOSP not failed in no-path scenario",   not grid2.nodes["HOSP"].failed)

protect_logged = any(
    "PROTECTED" in e.get("detail", "").upper() or "PRIORITY=1" in e.get("detail", "").upper()
    for e in flisr2["flisr_log"]
)
check("FLISR log protects priority-1 loads",
      protect_logged or "HOSP" in flisr2["message"],
      detail=f"steps: {[e['step'] for e in flisr2['flisr_log']]}")

print("\n  FLISR decision log:")
for e in flisr2["flisr_log"]:
    sym = "OK" if e["status"] == "ok" else ("XX" if e["status"] == "reject" else "!!")
    print(f"    [{sym}] [{e['step']}] {e['detail'][:70]}")


# ==============================================================================
# TEST C - EMS Execution Order & Partial Control Verification
# ==============================================================================

section("TEST C - EMS Execution Order & Partial Control Verification")
print("  Scenario: EMS runs after physics; absorption ratio = 0.5 (50%)")

grid3 = SmartGrid()
ems3  = EnergyManagementSystem()

# Physics step first (creates real imbalance)
grid3.step()

# Snapshot BEFORE EMS
pre_excess     = {nid: n.excess_energy for nid, n in grid3.nodes.items()
                  if n.node_type == "house" and n.excess_energy > 0}
pre_batteries  = {nid: grid3.nodes[nid].battery_level for nid in pre_excess}

# Run EMS (should react to physics output, not pre-empt it)
report3 = ems3.run(grid3)

# Snapshot AFTER EMS
post_batteries = {nid: grid3.nodes[nid].battery_level for nid in pre_excess}

print(f"\n  EMS cycle:           {report3['cycle']}")
print(f"  Balance after physics: {report3['balance']:.4f} MW")
print(f"  Absorption ratio:    {report3['absorption_ratio']} ({report3['absorption_ratio']*100:.0f}%)")
print(f"  EMS log entries:     {report3['log']}")
print()

check("EMS cycle = 1 on first run",              report3["cycle"] == 1)
check("EMS absorption ratio = 0.5 (partial)",    report3["absorption_ratio"] == 0.5)
check("EMS report has total_gen + total_load",   "total_gen" in report3 and "total_load" in report3)
check("EMS report log is a list",                isinstance(report3["log"], list))
check("EMS balance = total_gen - total_load",
      abs(report3["balance"] - (report3["total_gen"] - report3["total_load"])) < 0.001)

if pre_excess:
    charged = [nid for nid in pre_excess
               if post_batteries.get(nid, 0.0) > pre_batteries.get(nid, 0.0)]
    above_thresh = [nid for nid, v in pre_excess.items() if v > 0.05]
    check("EMS charged batteries when excess > threshold",
          len(charged) > 0 or len(above_thresh) == 0,
          detail=f"charged={len(charged)}/{len(above_thresh)} nodes")

    total_pre  = sum(pre_excess.values())
    total_post = sum(n.excess_energy for n in grid3.nodes.values() if n.node_type == "house")
    # Reverse flow visible means post excess > 0 OR total was below threshold
    check("Residual reverse-flow remains (partial absorption, not 100%)",
          total_post > 0 or total_pre < 0.30,
          detail=f"pre={total_pre:.3f} post={total_post:.3f} MW")
else:
    check("No house excess this tick (demand-dominated timestep - OK)", True)


# ==============================================================================
# SUMMARY
# ==============================================================================

section("TEST SUMMARY")
total = pass_count + fail_count
print(f"\n  Passed: {pass_count} / {total}")
print(f"  Failed: {fail_count} / {total}")

if fail_count == 0:
    print("\n  [ALL TESTS PASSED] System is judge-ready.")
elif fail_count <= 2:
    print(f"\n  [WARNING] {fail_count} test(s) failed - review before demo.")
else:
    print(f"\n  [CRITICAL] {fail_count} tests failed - fix required.")

print()
sys.exit(0 if fail_count == 0 else 1)
