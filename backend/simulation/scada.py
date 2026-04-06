"""
scada.py — SCADA Control Center Module

In a real-world power system, the physical grid does NOT contain AI logic.
Instead, a Supervisory Control and Data Acquisition (SCADA) system collects 
telemetry from the physical grid, runs analytics, and pushes control signals 
back to the breakers and switches.

This module acts as that separated logical layer. It holds the AI models:
- FaultDetector (Monitoring Layer)
- DemandForecaster (Analytics Layer)
- DQNAgent (Control Layer)
"""

import networkx as nx  # noqa: F401 — used by FLISR cluster analysis
from simulation.grid import SmartGrid
from models.lstm_model import DemandForecaster
from models.rl_agent import DQNAgent
from models.fault_detector import FaultDetector

class ScadaControlCenter:
    def __init__(self):
        print("\n[SCADA] Initialising Control Center...")
        self.forecaster     = DemandForecaster()
        self.agent          = DQNAgent()
        self.fault_detector = FaultDetector()

        # Closed-loop state
        self.cycle_id: int          = 0
        self._pending_action: str   = "do_nothing"    # queued for next cycle (1-step delay)
        self._expected_health: float = 1.0             # predicted outcome of last action
        self.control_log: list      = []               # last 10 cycle summaries

    def warmup_ai(self, mock_grid: SmartGrid):
        """Pre-trains the DQN agent on a safe simulation."""
        print("[SCADA] Running AI warm-up against physical simulation...")
        self.agent.smart_warmup(mock_grid, scada_instance=self)

    def collect_telemetry(self, grid: SmartGrid) -> dict:
        """
        Polls sensors across the grid. Returns the structural state
        needed for AI models.
        """
        return {
            "rl_state":      grid.get_rl_state(),
            "lstm_sequence": grid.get_lstm_input("S0"),
            "nodes_data":    list(grid.nodes.values()),
            "raw_state":     grid.get_state(),
        }

    def execute_control_loop(self, grid: SmartGrid, ems=None) -> dict:
        """
        Advances the control-plane by one cycle (closed-loop).

        1. Read telemetry.
        2. Feedback check: measure divergence from expected outcome.
        3. Fault detection (ANN).
        4. Demand forecast (LSTM).
        5. DQN decision.
        6. Apply PREVIOUS cycle's queued action (1-cycle control delay).
        7. Queue this cycle's action for next tick.
        8. RL experience + train.
        9. Return envelope with timestamp and divergence.

        ems (EnergyManagementSystem): passed through to FLISR so it can call
        run_for_cluster() for local storage dispatch before load shedding.
        """
        self.cycle_id += 1
        hour      = grid.get_time_of_day()
        timestamp = f"T{grid.timestep:04d}·H{hour:02d}:00"

        telemetry = self.collect_telemetry(grid)
        raw_state = telemetry["raw_state"]

        # ── Feedback: did the last action achieve what we expected? ──────
        actual_health  = raw_state.get("system", {}).get("health_score", 1.0)
        divergence     = round(abs(actual_health - self._expected_health), 4)

        # ── 1. Fault Detection ─────────────────────────────────────────────
        fault_report = self.fault_detector.analyse(grid.nodes)

        # ── 2. Demand Forecast ───────────────────────────────────────────
        predicted_load = self.forecaster.predict(telemetry["lstm_sequence"])
        # Phase 4: Proactive overload warnings based on predicted load
        overload_warnings = self._predict_overloads(grid, predicted_load)

        # ── 3. DQN Decision ───────────────────────────────────────────────
        decision = self.agent.select_action(
            telemetry["rl_state"],
            predicted_load,
            raw_state,
        )

        # ── 4. Apply PREVIOUS cycle's queued action (simulating relay delay) ──
        raw_action_result = self._dispatch_control_signal(
            self._pending_action, raw_state, grid, ems
        )

        # Unpack FLISR dict vs plain string
        if isinstance(raw_action_result, dict):
            action_result = raw_action_result.get("message", "")
            flisr_log     = raw_action_result.get("flisr_log", [])
        else:
            action_result = raw_action_result
            flisr_log     = []

        # ── 5. Queue THIS cycle's action for next tick ────────────────────
        self._pending_action   = decision["action_name"]
        self._expected_health  = min(1.0, actual_health + 0.05)   # optimistic estimate

        # ── 6. RL experience storage + training ──────────────────────────
        new_state     = grid.get_state()
        reward        = self.agent.compute_reward(new_state, decision["action_name"])
        next_rl_state = grid.get_rl_state()
        self.agent.store_experience(
            telemetry["rl_state"],
            decision["action_id"],
            reward,
            next_rl_state,
        )

        # ── 7. Control log (keep last 10) ─────────────────────────────────
        self.control_log.append({
            "cycle":      self.cycle_id,
            "timestamp":  timestamp,
            "action":     decision["action_name"],
            "health":     round(actual_health, 4),
            "divergence": divergence,
        })
        if len(self.control_log) > 10:
            self.control_log.pop(0)

        return {
            "decision":           decision,
            "predicted_load":     round(float(predicted_load), 4),
            "overload_warnings":  overload_warnings,
            "action_result":      action_result,
            "flisr_log":          flisr_log,
            "fault_analysis":     fault_report,
            "cycle_id":           self.cycle_id,
            "timestamp":          timestamp,
            "control_divergence": divergence,
            "hour_of_day":        hour,
        }


    def _predict_overloads(self, grid: SmartGrid, predicted_load_mw: float) -> list:
        """
        Uses LSTM load prediction to proactively warn about impending overloads.
        Distributes predicted system load based on current load ratios.
        """
        warnings = []
        
        # Calculate current active system load to find ratios
        total_current_load = sum(n.load for n in grid.nodes.values() if n.node_type == "house" and not n.failed)
        if total_current_load <= 0.001:
            return warnings

        for nid, node in grid.nodes.items():
            if node.node_type in ("transformer", "substation") and not node.failed:
                # Approximate predicted load for this specific node
                ratio = node.load / total_current_load
                projected_load = node.load + (predicted_load_mw * ratio)
                
                # Check adjacent edges for capacity
                max_capacity = 0.0
                for neighbor in grid.graph.neighbors(nid):
                    cap = grid.graph[nid][neighbor].get("capacity", 0.0)
                    if cap > max_capacity:
                        max_capacity = cap
                
                if max_capacity > 0:
                    current_pct = node.load / max_capacity
                    projected_pct = projected_load / max_capacity
                    
                    if projected_pct > 1.15: # Critical impending overload
                        warnings.append({
                            "node": nid,
                            "label": node.label or nid,
                            "current_load_pct": round(current_pct * 100, 1),
                            "projected_load_pct": round(projected_pct * 100, 1),
                            "recommended_action": "pre-emptive load shift"
                        })
        return warnings

    def _dispatch_control_signal(self, action_name: str, state_before: dict, grid: SmartGrid, ems=None):
        """
        Enhanced control signal dispatch.
        Returns str for standard actions, dict for FLISR (includes flisr_log).
        ems is passed through to FLISR for cluster-level local energy dispatch.
        """
        if action_name == "do_nothing":
            return "No control signals dispatched."

        elif action_name == "increase_generation":
            target = "G0"
            if target in grid.nodes:
                grid.nodes[target].increase_generation(0.5)
            return f"SCADA Signal sent to {target} to increase output +0.5 MW."

        elif action_name == "use_battery":
            discharged_nodes = 0
            for node in grid.nodes.values():
                if node.node_type == "house" and node.battery_level > 0.2:
                    node.use_battery(0.2)
                    discharged_nodes += 1
            return f"SCADA Signal sent to {discharged_nodes} prosumer inverters: Base-load battery discharge."

        elif action_name == "use_supercapacitor":
            discharged_nodes = 0
            for node in grid.nodes.values():
                if node.node_type == "house" and node.supercap_level > 0.1:
                    node.use_supercapacitor(0.1)
                    discharged_nodes += 1
            return f"SCADA Signal sent to {discharged_nodes} prosumer inverters: Supercapacitor flash discharge."

        elif action_name == "shift_load":
            shifted_nodes = 0
            for node in grid.nodes.values():
                if node.node_type == "house":
                    node.shift_load(0.15)
                    shifted_nodes += 1
            return f"SCADA Signal sent to {shifted_nodes} smart meters: Demand response (Load shedding 15%)."

        elif action_name == "reroute_energy":
            # Returns dict: { "message": str, "flisr_log": list }
            return self._flisr_restore(grid, ems)

        return "Unknown SCADA control signal."

    # ------------------------------------------------------------------
    # FLISR — Fault Location, Isolation & Service Restoration
    # ------------------------------------------------------------------

    def _flisr_restore(self, grid, ems=None) -> dict:
        """
        Full 5-step FLISR — Cluster-Based, Multi-Source-Aware, EMS-Integrated.

        Key improvements:
          - ISOLATE: Trip only boundary switches adjacent to fault.
          - CLUSTER: Find ALL disconnected islands using component analysis.
          - EVALUATE: Score tie candidates per-cluster (not globally).
          - RESTORE: Close best tie per cluster, then recompute flow.
          - EMS FALLBACK: If no valid tie → call ems.run_for_cluster() for
            local storage dispatch by priority BEFORE shedding.
          - SHED: Only non-served P3 loads shed. P1 always protected.

        Returns: { "message": str, "flisr_log": list[dict] }
        """
        log = []
        t_detect  = 0.0
        t_isolate = 0.3
        t_cluster = 0.8
        t_eval    = 1.3
        t_rest    = 1.8
        NOMINAL_V = 1.05
        V_FLOOR   = 0.90   # Relaxed emergency voltage floor (real grid ~0.90 pu)

        def entry(step, detail, status="info", t=0.0):
            log.append({"step": step, "status": status, "detail": f"[{t:.1f}s] {detail}"})

        # CRITICAL FIX: Path validation - only allow switching at defined switch points
        def valid_switch_path(path_edges):
            """
            Validate that a path only uses switch edges.
            Real grids only allow switching at defined switch points (reclosers/sectionalizers).
            This prevents bypassing switch logic or creating illegal switching sequences.
            """
            for u, v in path_edges:
                edge_data = grid.graph.get_edge_data(u, v, default={})
                # Must have has_switch=True or be a tie switch to be valid for FLISR operations
                if not edge_data.get("has_switch", False) and not edge_data.get("is_tie_switch", False):
                    return False
            return True

        def get_path_edges(path_nodes):
            """Convert node path to edge list."""
            return list(zip(path_nodes[:-1], path_nodes[1:]))

        # ── STEP 1: LOCATE ────────────────────────────────────────────────────
        failed_nodes = {nid for nid, n in grid.nodes.items() if n.failed}
        if not failed_nodes:
            entry("LOCATE", "No active faults detected.", "ok", t_detect)
            return {"message": "SCADA FLISR: No active faults.", "flisr_log": log}
        entry("LOCATE", f"Fault detected at: {', '.join(sorted(failed_nodes))}", "warn", t_detect)

        # ── STEP 2: ISOLATE — open only boundary switches around the fault ────
        # Open all active edges touching the faulted node (sectionalizers/reclosers)
        # The _isolate_fault_segments() on grid.step() handles non-switch cables.
        isolated_edges = []
        for fnid in failed_nodes:
            for neighbor in list(grid.graph.neighbors(fnid)):
                edge = grid.graph[fnid][neighbor]
                if edge.get("active", True):
                    edge["active"] = False
                    edge["switch_status"] = "fault_locked"
                    isolated_edges.append(f"{fnid}\u2500{neighbor}")
                    grid.event_log.append(f"\U0001f534 FLISR ISOLATE: Opened breaker {fnid}\u2500{neighbor}")

        if isolated_edges:
            entry("ISOLATE", f"Opened boundary breakers: {', '.join(isolated_edges)}", "warn", t_isolate)
        else:
            entry("ISOLATE", "All adjacent breakers already open (pre-isolated by protection relay).", "ok", t_isolate)

        # ── STEP 3: CLUSTER — identify ALL disconnected islands ───────────────
        # Build active sub-graph (no failed nodes, no inactive edges)
        active_g = nx.Graph()
        for u, v, d in grid.graph.edges(data=True):
            if d.get("active", True) and not grid.nodes[u].failed and not grid.nodes[v].failed:
                active_g.add_edge(u, v, **d)
        for nid, n in grid.nodes.items():
            if not n.failed:
                active_g.add_node(nid)

        # Find nodes reachable from any generator
        generator_ids = [nid for nid, n in grid.nodes.items()
                         if n.node_type == "generator" and not n.failed]
        powered = set()
        for gid in generator_ids:
            if gid in active_g:
                powered.update(nx.node_connected_component(active_g, gid))

        # Identify disconnected CLUSTERS (islands not reachable from any generator)
        unpowered_nodes = {nid for nid, n in grid.nodes.items()
                           if not n.failed and nid not in powered}

        # Group unpowered nodes into their connected islands
        sub_g = active_g.subgraph(unpowered_nodes)
        clusters = list(nx.connected_components(sub_g))

        has_load_clusters = [
            c for c in clusters
            if any(grid.nodes[nid].node_type in ("house", "service", "transformer")
                   for nid in c)
        ]

        if not has_load_clusters:
            msg = f"SCADA FLISR: \u2705 Fault isolated at {failed_nodes}. No downstream loads lost."
            entry("CLUSTER", "No load-bearing islands — all loads still powered.", "ok", t_cluster)
            grid.event_log.append(msg)
            return {"message": msg, "flisr_log": log}

        total_orphan_load = sum(grid.nodes[nid].load for c in has_load_clusters for nid in c)
        critical_nodes = [nid for c in has_load_clusters for nid in c
                          if grid.nodes[nid].priority == 1]
        entry("CLUSTER",
              f"{len(has_load_clusters)} island(s) found. "
              f"Total load: {total_orphan_load:.2f} MW. "
              f"Critical: {[grid.nodes[n].label or n for n in critical_nodes] or 'None'}",
              "warn", t_cluster)

        # ── STEP 4 + 5: EVALUATE & RESTORE — per-cluster tie selection ────────
        restored_clusters = 0
        shed_count = 0
        shed_mw = 0.0
        all_ties_closed = []

        for cluster_idx, cluster in enumerate(has_load_clusters):
            cluster_load = sum(grid.nodes[nid].load for nid in cluster)
            cluster_critical = [nid for nid in cluster if grid.nodes[nid].priority == 1]

            entry("EVALUATE",
                  f"Cluster {cluster_idx+1}: {len(cluster)} nodes, {cluster_load:.2f} MW "
                  f"| Critical: {[grid.nodes[n].label or n for n in cluster_critical] or 'None'}",
                  "info", t_eval)

            # Find tie switches where one end is in this cluster, other is powered
            tie_candidates = []
            for u, v, data in grid.graph.edges(data=True):
                if not data.get("is_tie_switch", False):
                    continue
                if data.get("active", False):
                    continue  # Already closed
                if grid.nodes[u].failed or grid.nodes[v].failed:
                    continue

                u_in_cluster = u in cluster
                v_in_cluster = v in cluster
                u_powered = u in powered
                v_powered = v in powered

                # Only valid if exactly one end is in this cluster and other end is powered
                valid = (u_in_cluster and v_powered) or (v_in_cluster and u_powered)
                if not valid:
                    continue

                cap   = data.get("capacity", 1.0)
                flow  = abs(data.get("flow", 0.0))
                R     = data.get("resistance", 0.01)

                load_ratio  = (flow + cluster_load) / max(cap, 0.001)
                v_drop_est  = R * cluster_load / NOMINAL_V
                v_estimated = round(NOMINAL_V - v_drop_est, 4)
                headroom    = round((cap - flow - cluster_load) / cap * 100, 1)

                if load_ratio > 0.95:
                    entry("EVALUATE",
                          f"Tie {u}\u2500{v}: \u274c REJECTED \u2014 overload ({flow + cluster_load:.2f} > {cap*0.95:.2f} MW)",
                          "reject", t_eval)
                    continue

                if v_estimated < V_FLOOR:
                    entry("EVALUATE",
                          f"Tie {u}\u2500{v}: \u274c REJECTED \u2014 V={v_estimated:.4f} pu < {V_FLOOR} pu floor",
                          "reject", t_eval)
                    continue

                entry("EVALUATE",
                      f"Tie {u}\u2500{v}: \u2705 VALID \u2014 V={v_estimated} pu, headroom={headroom}%, R={R:.4f}",
                      "ok", t_eval)
                tie_candidates.append({
                    "u": u, "v": v, "score": R,
                    "v_estimated": v_estimated, "headroom": headroom,
                    "load_ratio": round(load_ratio, 3), "data": data,
                })

            if tie_candidates:
                # ── Multi-objective Optimization (NORMALIZED + PRIORITY-AWARE) ──
                # CRITICAL FIX 1: All metrics normalized to same scale for fair comparison
                max_R = max([t["score"] for t in tie_candidates]) or 1.0
                max_V_drop = max([1.0 - t["v_estimated"] for t in tie_candidates]) or 1.0

                # CRITICAL FIX 2: Count actual switch operations in the path
                # For single-tie: 1 switch, for multi-hop: count all switch edges
                def count_switches_in_path(u, v):
                    """Count switch edges between tie point and powered source."""
                    try:
                        # Find path from powered side to the tie switch
                        u_in_cluster = u in cluster
                        source_node = v if u_in_cluster else u
                        # Shortest path from source to nearest generator
                        min_switches = float('inf')
                        for gen_id in generator_ids:
                            if gen_id in active_g and nx.has_path(active_g, source_node, gen_id):
                                path = nx.shortest_path(active_g, source_node, gen_id)
                                path_edges = get_path_edges(path)

                                # CRITICAL FIX: Only count if path uses only switch edges
                                if not valid_switch_path(path_edges):
                                    continue  # Skip paths that bypass switch logic

                                switch_count = sum(1 for i in range(len(path)-1)
                                                   if active_g[path[i]][path[i+1]].get("has_switch", False))
                                min_switches = min(min_switches, max(1, switch_count))
                        return min_switches if min_switches != float('inf') else 999  # Invalid path = high penalty
                    except:
                        return 999  # Invalid path = high penalty

                # Count switches for each candidate (filter out invalid paths)
                for t in tie_candidates:
                    t["switch_count"] = count_switches_in_path(t["u"], t["v"])

                # Filter out candidates with invalid switch paths
                tie_candidates = [t for t in tie_candidates if t["switch_count"] < 999]

                if not tie_candidates:
                    entry("EVALUATE",
                          f"Cluster {cluster_idx+1}: No valid switch-only paths found. Skipping restoration.",
                          "reject", t_eval)
                    continue

                max_switches = max([t["switch_count"] for t in tie_candidates]) or 1.0

                # CRITICAL FIX 3: Priority-aware scoring
                # Higher priority = lower number (P1=Critical), so inverse weight gives bonus
                priority_bonus = sum(1.0 / max(1, grid.nodes[nid].priority) for nid in cluster)

                for t in tie_candidates:
                    R_norm = t["score"] / max_R
                    V_norm = (1.0 - t["v_estimated"]) / max_V_drop
                    S_norm = t["switch_count"] / max_switches

                    # FINAL SCORE: Lower is better (minimize resistance, voltage drop, switches)
                    # Priority bonus SUBTRACTS from score (higher priority = lower final score)
                    t["final_score"] = (
                        0.35 * R_norm +
                        0.25 * V_norm +
                        0.25 * S_norm -
                        0.15 * priority_bonus
                    )
                
                best = sorted(tie_candidates, key=lambda t: t["final_score"])[0]
                best["data"]["active"] = True
                best["data"]["switch_status"] = "closed"
                all_ties_closed.append(f"{best['u']}\u2500{best['v']}")
                restored_clusters += 1

                entry("RESTORE",
                      f"Cluster {cluster_idx+1}: Closed tie {best['u']}\u2500{best['v']} "
                      f"(R={best['score']:.5f}) \u2014 restored {len(cluster)} nodes ({cluster_load:.2f} MW). "
                      f"V={best['v_estimated']} pu | Headroom={best['headroom']}%",
                      "ok", t_rest)

                for nid in cluster:
                    grid.nodes[nid].isolated = False
                    if grid.nodes[nid].voltage == 0.0:
                        grid.nodes[nid].voltage = best["v_estimated"]

                grid.event_log.append(
                    f"\u2705 FLISR: Cluster {cluster_idx+1} restored via tie {best['u']}\u2500{best['v']}"
                )

            else:
                # No valid tie — EMS local energy dispatch first, then priority shedding
                entry("RESTORE",
                      f"Cluster {cluster_idx+1}: No valid tie. "
                      f"EMS local dispatch → storage → priority shedding.",
                      "warn", t_rest)

                # ── EMS cluster dispatch: exhaust local storage by priority ──
                local_served = 0.0
                if ems is not None:
                    ems_result  = ems.run_for_cluster(grid, list(cluster))
                    local_served = ems_result.get("served_mw", 0.0)
                    for act in ems_result.get("actions", [])[:3]:
                        entry("RESTORE", f"🛡️ EMS: {act}", "ok", t_rest)
                    if local_served > 0.001:
                        entry("RESTORE",
                              f"EMS discharge: {local_served:.2f} MW served from cluster storage.",
                              "ok", t_rest)

                # ── Shed what remains unserved (P3 first, P1 always protected) ──
                shed_candidates = sorted(
                    cluster, key=lambda nid: grid.nodes[nid].priority, reverse=True
                )
                for nid in shed_candidates:
                    n = grid.nodes[nid]
                    if n.priority == 1:
                        entry("RESTORE",
                              f"🏥 {n.label or nid} (P1 CRITICAL): PROTECTED — EMS storage active.",
                              "ok", t_rest)
                        # Fallback if EMS unavailable
                        if ems is None and n.battery_level > 0.1:
                            n.use_battery(0.15)
                        continue
                    shed_mw += n.load
                    n.shift_load(1.0)
                    shed_count += 1
                    tag = "🏠 residential" if n.priority == 3 else "🏭 commercial"
                    entry("RESTORE",
                          f"Shed {n.label or nid} [{nid}] — {tag}, P{n.priority}",
                          "warn", t_rest)
                    grid.event_log.append(f"✅ FLISR SHED: {n.label or nid} ({tag})")

        # ── METRICS ───────────────────────────────────────────────────────────
        total_islands = len(has_load_clusters)
        entry("METRICS",
              f"Clusters: {total_islands}. Restored: {restored_clusters}. "
              f"Shed: {shed_count} nodes ({shed_mw:.2f} MW). "
              f"\u2705 Critical loads protected." if not critical_nodes else
              f"Clusters: {total_islands}. Restored: {restored_clusters}. "
              f"Shed: {shed_count} nodes ({shed_mw:.2f} MW). "
              f"\u2705 HOSP Protected.",
              "ok" if restored_clusters == total_islands else "warn",
              t_rest + 0.1)

        # ── IMMEDIATE FLOW RECOMPUTE ──────────────────────────────────────────
        # After FLISR closes tie switches, run the multi-source BFS immediately
        # so restored cluster nodes get correct voltages in THIS cycle (not the next).
        # This eliminates the 1-step voltage recompute delay.
        if restored_clusters > 0:
            entry("RECOMPUTE",
                  f"Triggering immediate power-flow recompute for {restored_clusters} restored cluster(s).",
                  "info", t_rest + 0.2)
            grid._simulate_energy_flow()

        if restored_clusters == total_islands:
            msg = (f"SCADA FLISR: \u2705 All {restored_clusters} island(s) restored via tie(s) "
                   f"{', '.join(all_ties_closed)}. {total_orphan_load:.2f} MW recovered.")
        elif restored_clusters > 0:
            msg = (f"SCADA FLISR: \u26a0\ufe0f Partial restoration \u2014 {restored_clusters}/{total_islands} islands restored "
                   f"via {', '.join(all_ties_closed)}. Load shed: {shed_count} nodes ({shed_mw:.2f} MW).")
        else:
            msg = (f"SCADA FLISR: \u26a0\ufe0f No valid tie available. "
                   f"Load shedding: {shed_count} non-critical nodes ({shed_mw:.2f} MW shed). "
                   f"Critical loads protected.")

        grid.event_log.append(msg)
        return {"message": msg, "flisr_log": log}




