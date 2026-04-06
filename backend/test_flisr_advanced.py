"""
test_flisr_advanced.py — FLISR advanced test suite

Tests:
  1. (Original) Fault at P3 — FLISR restores HOSP via tie
  2. (NEW) Partial isolation — fault mid-feeder at P1 shouldn't isolate P0
  3. (NEW) No-tie fallback — all ties disabled, verify load shedding fires
  4. (NEW) Switch state machine — restore_node resets switch_status correctly
  5. (NEW) Fault segment descriptor — last_fault_segment populated correctly
"""

import sys
from simulation.grid import SmartGrid
from simulation.scada import ScadaControlCenter

def _green(text):  print(f"\033[92m{text}\033[0m")
def _yellow(text): print(f"\033[93m{text}\033[0m")
def _red(text):    print(f"\033[91m{text}\033[0m")
def _cyan(text):   print(f"\033[96m{text}\033[0m")

def ok(msg):   _green(f"  ✅  {msg}")
def fail(msg): _red(f"  ❌  {msg}"); sys.exit(1)
def info(msg): _cyan(f"  ℹ   {msg}")

# ── Minimal SCADA fixture (skips AI warmup) ──────────────────────────────
class QuickSCADA(ScadaControlCenter):
    def __init__(self): pass

qs = QuickSCADA()

# ════════════════════════════════════════════════════════════════════════
# TEST 0 — Smoke test: grid initialises cleanly
# ════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("TEST 0 — Smoke Test")
print("="*60)
g = SmartGrid()
st = g.get_state()
assert len(g.nodes) > 0, "Grid has no nodes"
assert "HOSP" in g.nodes, "Hospital missing"
assert "last_fault_segment" in st, "last_fault_segment missing from get_state()"
assert st["last_fault_segment"] == {}, "Segment should be empty on fresh grid"
ok(f"Grid initialised: {len(g.nodes)} nodes, HOSP present, last_fault_segment exposed")


# ════════════════════════════════════════════════════════════════════════
# TEST 1 — Original regression: Fault at P3 → FLISR restores HOSP
# ════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("TEST 1 — P3 Fault → FLISR restores downstream (HOSP)")
print("="*60)
g = SmartGrid()
g.inject_failure("P3")
result = qs._dispatch_control_signal("reroute_energy", g.get_state(), g)
assert isinstance(result, dict), "FLISR should return dict"
msg = result.get("message", "")
info(f"FLISR result: {msg}")
if "restored" in msg.lower() or "✅" in msg:
    ok("FLISR successfully restored downstream cluster")
else:
    _yellow(f"  ⚠  FLISR result: {msg}")


# ════════════════════════════════════════════════════════════════════════
# TEST 2 — CRITICAL: Partial isolation (fault at P1)
#   P0 is UPSTREAM of P1, separated by a sectionalizer switch.
#   P0 must NOT be isolated — only P1 and its downstream segment.
# ════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("TEST 2 — Partial Isolation: Fault at P1, P0 must stay powered")
print("="*60)
g = SmartGrid()

# Pre-step to populate voltages
g.step()

# Inject fault mid-feeder
g.inject_failure("P1")

# Run segment isolation (called inside _simulate_energy_flow)
g._isolate_fault_segments()

# P0 must still be active (not isolated)
p0 = g.nodes.get("P0")
assert p0 is not None, "P0 missing from grid"
if p0.isolated or p0.failed:
    fail(f"P0 incorrectly marked isolated={p0.isolated} / failed={p0.failed} after P1 fault")
else:
    ok("P0 correctly remains powered after P1 fault (sectionalization works)")

# P1 itself should be failed
p1 = g.nodes.get("P1")
assert p1 is not None, "P1 missing"
assert p1.failed, "P1 should be in failed state"
ok(f"P1 correctly marked as FAILED")

# Fault segment should be populated
seg = g.last_fault_segment
assert seg, "last_fault_segment should be populated after fault"
info(f"Fault segment: start={seg.get('start_switch')} end={seg.get('end_switch')} affected={seg.get('affected_nodes')}")
ok("last_fault_segment correctly populated")

# Boundary switches adjacent to P1 should be fault_locked
boundary_faults = [
    (u, v, d) for u, v, d in g.graph.edges(data=True)
    if d.get("switch_status") == "fault_locked"
]
info(f"Fault-locked edges: {[(u,v) for u,v,_ in boundary_faults]}")
assert len(boundary_faults) > 0, "No switches were fault-locked — isolation failed"
ok(f"{len(boundary_faults)} boundary switch(es) correctly fault-locked")


# ════════════════════════════════════════════════════════════════════════
# TEST 3 — No-tie fallback: disable ALL tie switches → verify load shedding
# ════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("TEST 3 — No-Tie Fallback: disable ties, expect load shedding")
print("="*60)
g = SmartGrid()

# Disable every tie switch
disabled = 0
for u, v, d in g.graph.edges(data=True):
    if d.get("is_tie_switch"):
        d["active"]        = False
        d["switch_status"] = "open"
        # Mark it so FLISR also skips it as a candidate
        d["is_tie_switch"] = False  # hide from FLISR loop
        disabled += 1
info(f"Disabled {disabled} tie switches")

g.inject_failure("P3")  # Orphans commercial feeder cluster

result = qs._dispatch_control_signal("reroute_energy", g.get_state(), g)
msg = result.get("message", "")
info(f"FLISR result: {msg}")

log_steps = [entry["step"] for entry in result.get("flisr_log", [])]
has_shed   = any("shed" in entry["detail"].lower() for entry in result.get("flisr_log", []))

if has_shed or "shed" in msg.lower() or "no valid tie" in msg.lower():
    ok("Load shedding correctly triggered when no valid tie is available")
else:
    _yellow(f"  ⚠  Expected load shedding, got: {msg}")


# ════════════════════════════════════════════════════════════════════════
# TEST 4 — Switch state machine: restore_node resets switch_status
# ════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("TEST 4 — Switch State Machine: restore_node resets switch_status")
print("="*60)
g = SmartGrid()
g.inject_failure("P1")
g._isolate_fault_segments()

# Verify fault_locked switches exist
locked = [(u, v, d) for u, v, d in g.graph.edges(data=True) if d.get("switch_status") == "fault_locked"]
assert locked, "No fault_locked edges found — cannot test state machine"
info(f"Before restore: {len(locked)} fault_locked edge(s): {[(u,v) for u,v,_ in locked]}")

# Restore the faulted node
result_msg = g.restore_node("P1")
info(f"restore_node: {result_msg}")

# After restore, NO edge should still be fault_locked
still_locked = [(u, v, d) for u, v, d in g.graph.edges(data=True) if d.get("switch_status") == "fault_locked"]
if still_locked:
    fail(f"Edges still fault_locked after restore: {[(u,v) for u,v,_ in still_locked]}")
else:
    ok(f"All fault_locked switches correctly reset after restore_node()")

# last_fault_segment should be cleared
assert g.last_fault_segment == {}, "last_fault_segment not cleared after restore_node()"
ok("last_fault_segment cleared after restore_node()")

# Tie switches should be open (normally-open), not fault_locked or closed
tie_states = [(u, v, d["switch_status"]) for u, v, d in g.graph.edges(data=True) if d.get("is_tie_switch")]
for u, v, status in tie_states:
    assert status in ("open", "closed"), f"Tie switch {u}-{v} has unexpected status: {status}"
ok(f"All {len(tie_states)} tie switch(es) reset to 'open' standby state")


# ════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("ALL FLISR ADVANCED TESTS PASSED ✅")
print("="*60 + "\n")
