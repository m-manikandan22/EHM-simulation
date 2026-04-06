"""
ems.py — Energy Management System (EMS) Layer

Real CPS execution order:
  1. Physics (grid.py)   → power flow creates real imbalance / voltage violations
  2. EMS     (ems.py)    → reacts to imbalance with PARTIAL control signals
  3. SCADA   (scada.py)  → fault detection + AI decisions

Design principles:
  - PARTIAL control only (absorption_ratio = 0.5)
    → 50 % of excess solar stored, 50 % left as visible reverse flow
    → Creates realistic, observable behaviors for the demo
  - Threshold-gated actions (ignore tiny fluctuations)
  - Node roles guide dispatch:
      "generation" → prosumer solar target (reverse flow source)
      "storage"    → battery dispatch target
      "support"    → supercapacitor fast-response node (voltage dip)
      "load"       → pure consumer (never discharged)
  - Generator ramping when system deficit is large (avoids total collapse)
  - Priority-based energy allocation: P1 (critical) served first, then P2, then P3
  - BAT0 (grid-scale battery) discharges directly as generation injection
  - run_for_cluster() exposes EMS to FLISR for local energy before shedding

PyPSA Integration (NEW):
  - Uses PyPSA for cost-aware optimization when available
  - Falls back to rule-based dispatch for speed/real-time needs
  - Separates generators (solar/wind/coal/nuclear) from storage (battery/supercap)

Integrated pipeline (after this fix):
  1. Calculate generation (solar SOLAR_CURVE + wind WIND_CURVE — already in grid.py)
  2. EMS balances (PyPSA optimization OR rule-based fallback)
  3. Physics flow (BFS + signed reverse-flow voltage)
  4. SCADA detects issues
  5. FLISR reroutes
  6. If fails → EMS cluster dispatch (storage) → then shed by priority
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Dict, Any
import logging

if TYPE_CHECKING:
    from simulation.grid import SmartGrid

# ── PyPSA Integration ──────────────────────────────────────────────────────────
try:
    from simulation.ems_optimizer import EMSOptimizer, optimize_energy, OptimizationResult
    PYPSA_AVAILABLE = True
except ImportError:
    PYPSA_AVAILABLE = False
    logging.warning("PyPSA optimizer not available, using rule-based EMS only")

logger = logging.getLogger(__name__)

# PyPSA optimizer integration
try:
    from simulation.ems_optimizer import EMSOptimizer, PyPSAEMSBridge, optimize_energy
    PYPSA_AVAILABLE = True
except ImportError:
    PYPSA_AVAILABLE = False


# ── Tuneable constants ─────────────────────────────────────────────────────────
ABSORPTION_RATIO      = 0.5    # Only absorb this fraction of excess solar (rest = visible reverse flow)
EXCESS_THRESHOLD_MW   = 0.30   # Minimum system excess before EMS acts (MW)
DEFICIT_THRESHOLD_MW  = 0.25   # Minimum system deficit before EMS acts (MW)
VOLTAGE_DIP_THRESHOLD = 0.97   # pu — supercap fast-response triggers below this
BATTERY_CHARGE_RATE   = 0.40   # Max battery charge per EMS tick (fraction of excess absorbed)
BATTERY_DISCHARGE_RATE= 0.30   # Max battery discharge per EMS tick (MW per node)
SUPERCAP_DISCHARGE    = 0.10   # MW flash-discharge from supercap per tick
GEN_RAMP_THRESHOLD_MW = 1.50   # System deficit above this → ramp up generators
GEN_RAMP_STEP_MW      = 0.30   # MW increase per generator per EMS tick
MAX_GENERATOR_OUTPUT  = 10.0   # MW cap per generator
EMS_NORMAL_RESERVE    = 0.40   # Reserve bottom 40% exclusively for FLISR/emergencies
PRIORITY_PROTECTION_THRESHOLD = 0.1  # Min battery SOC to dispatch for priority node protection


class EnergyManagementSystem:
    """
    Hybrid EMS dispatcher with PyPSA optimization.

    Uses PyPSA for cost-aware dispatch when available,
    falls back to rule-based dispatch for real-time/speed.

    Call `run(grid)` once per timestep immediately after `grid.step()`
    and before `scada.execute_control_loop()`.
    """

    def __init__(self, use_pypsa: bool = True):
        self.ems_log: list[str] = []   # human-readable log of this tick's actions
        self.cycle: int = 0

        # PyPSA optimizer for cost-aware dispatch
        self._pypsa_available = PYPSA_AVAILABLE and use_pypsa
        if self._pypsa_available:
            self._optimizer = EMSOptimizer(use_pypsa=True)
            logger.info("EMS initialized with PyPSA optimization")
        else:
            self._optimizer = None
            logger.info("EMS initialized with rule-based dispatch (PyPSA unavailable)")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, grid: "SmartGrid") -> dict:
        """
        Execute one EMS cycle with optional PyPSA optimization.

        Steps:
          0. PyPSA Optimization (if available) → cost-aware dispatch
          A. Compute system-wide balance from physics output
          B. Solar absorption (partial — 50 %)
          C. Battery charge/discharge based on optimization
          D. Generator dispatch (if needed)
          E. Peer-to-peer energy sharing
        """
        self.cycle += 1
        self.ems_log = []

        active_nodes = [n for n in grid.nodes.values() if not n.failed and not n.isolated]
        if not active_nodes:
            return self._report(0.0, 0.0, "No active nodes.")

        # ── 0. PyPSA Cost-Aware Optimization (every cycle if available) ─────
        pypsa_result = None
        if self._optimizer is not None and PYPSA_AVAILABLE:
            try:
                pypsa_result = self._optimizer.optimize(grid)
                self._apply_pypsa_dispatch(grid, pypsa_result)
                self.ems_log.append(f"🧠 PyPSA optimized dispatch: ${pypsa_result.total_cost:.2f}")
            except Exception as e:
                logger.warning(f"PyPSA optimization failed: {e}")
                self.ems_log.append(f"⚠️ PyPSA failed, using rule-based: {str(e)[:40]}")

        total_gen  = sum(n.generation for n in active_nodes)
        total_load = sum(n.load       for n in active_nodes)
        balance    = total_gen - total_load

        # ── A. Partial solar absorption (excess → battery, 50 % only) ──────
        if balance > EXCESS_THRESHOLD_MW:
            self._charge_storage(grid, active_nodes, balance)

        # ── B. Source Priority Dispatch (Deficit management) ─────────────
        elif balance < -DEFICIT_THRESHOLD_MW:
            # Only run rule-based if PyPSA didn't handle it
            if pypsa_result is None or pypsa_result.battery_dispatch <= 0.01:
                self._source_priority_dispatch(grid, active_nodes, abs(balance))
            self._priority_energy_allocation(grid, active_nodes, abs(balance))

        # ── C. Peer-to-peer residual sharing ─────────────────────────────
        self._peer_sharing(grid)

        action_msg = "; ".join(self.ems_log) if self.ems_log else "EMS: within normal limits — no action required."

        # Include PyPSA decisions in report if available
        if pypsa_result:
            return self._report_pypsa(total_gen, total_load, pypsa_result, action_msg)
        return self._report(total_gen, total_load, action_msg)

    # ------------------------------------------------------------------
    # Sub-routines
    # ------------------------------------------------------------------

    # _supercap_voltage_support logic moved natively to grid._simulate_energy_flow()

    def _charge_storage(self, grid: "SmartGrid", active_nodes: list, excess_mw: float):
        """
        Absorb ABSORPTION_RATIO of system excess into batteries.
        The remaining (1 - ABSORPTION_RATIO) stays as reverse flow — visible in UI.

        Only targets nodes with role "generation" or "storage" AND available battery space.
        """
        absorb_target = excess_mw * ABSORPTION_RATIO   # 50 % of surplus
        absorbed_total = 0.0
        charged_count  = 0

        # Sort: generation-role nodes first (they ARE the source of reverse flow)
        targets = sorted(
            [n for n in active_nodes if n.node_type == "house" and n.excess_energy > 0.05],
            key=lambda n: (n.role != "generation", -n.excess_energy)
        )

        for node in targets:
            if absorbed_total >= absorb_target:
                break
            headroom = (1.0 - node.battery_level) * node.battery_capacity
            if headroom < 0.001:
                continue

            # How much can we absorb here?
            can_absorb  = min(
                node.excess_energy * ABSORPTION_RATIO * BATTERY_CHARGE_RATE,
                headroom,
                absorb_target - absorbed_total
            )
            if can_absorb < 0.001:
                continue

            node.battery_level = min(1.0, node.battery_level + can_absorb / node.battery_capacity)
            node.excess_energy = max(0.0, node.excess_energy - can_absorb)
            absorbed_total += can_absorb
            charged_count  += 1

        if charged_count:
            self.ems_log.append(
                f"🔋 EMS charging: {absorbed_total:.2f} MW absorbed into {charged_count} batteries "
                f"({ABSORPTION_RATIO*100:.0f}% absorption — {(excess_mw - absorbed_total):.2f} MW "
                f"left as reverse flow)"
            )

    def _source_priority_dispatch(self, grid: "SmartGrid", active_nodes: list, deficit_mw: float):
        """
        Dispatch energy sources to cover system deficit following priority order:
        1. Solar/Wind (Non-dispatchable, already maxed by physics)
        2. Battery (Storage)
        3. Coal (Conventional, fast ramping)
        4. Nuclear (Baseload, slow/no ramping)
        """
        covered = 0.0

        # ── 1. Dispatch Batteries (Storage) ──
        # EMS only drains batteries down to EMS_NORMAL_RESERVE (40%) and reserves the rest for FLISR
        storage_targets = sorted(
            [n for n in active_nodes if (n.source_type == "battery" or n.battery_level > EMS_NORMAL_RESERVE)],
            key=lambda n: (n.role != "storage", -n.battery_level)
        )
        discharged_count = 0
        for node in storage_targets:
            if covered >= deficit_mw:
                break
            # Grid scale battery (BAT0) has higher discharge rate
            rate = BATTERY_DISCHARGE_RATE * 5 if node.source_type == "battery" else BATTERY_DISCHARGE_RATE
            want = min(rate, deficit_mw - covered)
            if node.source_type == "battery":
                # Grid-scale battery: deplete SOC AND inject as generation into the grid
                available = node.battery_capacity * node.battery_level
                delivered = min(want, available)
                node.battery_level = max(0.0, node.battery_level - delivered / node.battery_capacity)
                # ✅ KEY FIX: inject as generation so BFS flow engine sees real power
                node.generation = round(min(node.battery_capacity, node.generation + delivered), 4)
            else:
                delivered = node.use_battery(want)
            
            if delivered > 0.001:
                covered += delivered
                discharged_count += 1

        if discharged_count:
            self.ems_log.append(f"🔋 EMS dispatched {covered:.2f} MW from {discharged_count} batteries (Priority 1).")

        # ── 2. Ramp Generators (Coal then Nuclear) ──
        if deficit_mw - covered > GEN_RAMP_THRESHOLD_MW:
            ramped = []
            remaining_deficit = deficit_mw - covered
            
            gens_by_priority = sorted(
                [n for n in active_nodes if n.node_type == "generator"],
                key=lambda n: 0 if n.source_type == "coal" else 1 if n.source_type == "nuclear" else 2
            )
            
            for gen in gens_by_priority:
                if remaining_deficit <= 0:
                    break
                # Only ramp coal / nuclear
                if gen.source_type not in ("coal", "nuclear"):
                    continue
                
                # Coal ramps faster than nuclear
                ramp_step = GEN_RAMP_STEP_MW * 1.5 if gen.source_type == "coal" else GEN_RAMP_STEP_MW * 0.5
                max_out = MAX_GENERATOR_OUTPUT if gen.source_type == "coal" else MAX_GENERATOR_OUTPUT * 1.5
                
                if gen.generation < max_out:
                    headroom = max_out - gen.generation
                    ramp = min(ramp_step, headroom, remaining_deficit)
                    gen.generation = round(min(max_out, gen.generation + ramp), 4)
                    gen._base_generation = gen.generation
                    ramped.append(f"{gen.node_id}({gen.source_type})+{ramp:.2f}MW")
                    remaining_deficit -= ramp
                    covered += ramp
                    
            if ramped:
                self.ems_log.append(
                    f"🏭 EMS generator dispatch: {', '.join(ramped)} (Priority 2/3)"
                )

    def _priority_energy_allocation(self, grid: "SmartGrid", active_nodes: list, deficit_mw: float):
        """
        Priority-based energy allocation — the MISSING LINK between EMS + SCADA.

        When system is in deficit, protect nodes by dispatch priority:
          Priority 1 (hospitals/critical) → served first, from nearest storage
          Priority 2 (commercial/industrial) → served second
          Priority 3 (residential) → served only if energy remains

        This is the central coordination that transforms isolated components
        into a real EMS-controlled grid.
        """
        if deficit_mw <= DEFICIT_THRESHOLD_MW:
            return

        # Group deficit nodes by priority
        p1 = [n for n in active_nodes if n.priority == 1 and n.deficit > 0.05]
        p2 = [n for n in active_nodes if n.priority == 2 and n.deficit > 0.05]
        p3 = [n for n in active_nodes if n.priority == 3 and n.deficit > 0.05]

        served_total = 0.0
        alloc_log: list[str] = []

        for group, label in [(p1, "P1-CRITICAL"), (p2, "P2-COMMERCIAL"), (p3, "P3-RESIDENTIAL")]:
            for node in group:
                if served_total >= deficit_mw:
                    break
                need = min(node.deficit, deficit_mw - served_total, BATTERY_DISCHARGE_RATE)

                # Supercap first (instant voltage stabilisation)
                if node.supercap_level > 0.1 and node.voltage < VOLTAGE_DIP_THRESHOLD:
                    sc_delivered = node.use_supercapacitor(min(SUPERCAP_DISCHARGE, need))
                    if sc_delivered > 0.001:
                        served_total += sc_delivered
                        node.deficit  = max(0.0, node.deficit - sc_delivered)
                        alloc_log.append(
                            f"⚡ {label} {node.label or node.node_id}: supercap {sc_delivered:.2f} MW "
                            f"(V={node.voltage:.3f} pu)"
                        )
                        need -= sc_delivered

                # Battery for sustained supply
                if need > 0.01 and node.battery_level > PRIORITY_PROTECTION_THRESHOLD:
                    bat_delivered = node.use_battery(need)
                    if bat_delivered > 0.001:
                        served_total += bat_delivered
                        node.deficit  = max(0.0, node.deficit - bat_delivered)
                        alloc_log.append(
                            f"🔋 {label} {node.label or node.node_id}: battery {bat_delivered:.2f} MW "
                            f"(SOC={node.battery_level:.2f})"
                        )

        if alloc_log:
            self.ems_log.append(
                f"🎯 EMS Priority Allocation — {served_total:.2f} MW served: " + " | ".join(alloc_log[:3])
            )

    def run_for_cluster(self, grid: "SmartGrid", cluster_node_ids: list) -> dict:
        """
        EMS local energy dispatch for an isolated cluster — called by FLISR
        when no valid tie switch is available.

        Exhausts cluster-local storage by priority ORDER before shedding begins.
        Returns a summary of energy served so FLISR can decide what still needs shedding.

        Flow:
          P1 nodes → supercap + battery (protected, never shed)
          P2 nodes → battery if available
          P3 nodes → minimal support (likely shed anyway)
        """
        served_mw   = 0.0
        actions     = []

        cluster_nodes = [
            grid.nodes[nid] for nid in cluster_node_ids
            if nid in grid.nodes and not grid.nodes[nid].failed
        ]
        # Serve priority-1 first, then 2, then 3
        cluster_nodes.sort(key=lambda n: n.priority)

        for node in cluster_nodes:
            node_label = node.label or node.node_id

            # Supercap: sub-cycle voltage support (fast)
            if node.supercap_level > 0.1 and node.voltage < VOLTAGE_DIP_THRESHOLD:
                sc = node.use_supercapacitor(SUPERCAP_DISCHARGE)
                if sc > 0.001:
                    served_mw += sc
                    actions.append(
                        f"⚡ Supercap at {node_label} [P{node.priority}]: {sc:.2f} MW "
                        f"— voltage={node.voltage:.3f} pu"
                    )

            # Battery: sustained energy supply
            if node.battery_level > PRIORITY_PROTECTION_THRESHOLD:
                need      = min(node.load, BATTERY_DISCHARGE_RATE * 2)
                delivered = node.use_battery(need)
                if delivered > 0.001:
                    served_mw += delivered
                    node.deficit = max(0.0, node.deficit - delivered)
                    actions.append(
                        f"🔋 Battery at {node_label} [P{node.priority}]: "
                        f"{delivered:.2f} MW (SOC={node.battery_level:.2f})"
                    )

        self.ems_log.extend(actions[:4])  # cap log entries
        return {"served_mw": round(served_mw, 4), "actions": actions}

    def _peer_sharing(self, grid: "SmartGrid"):
        """
        Residual peer-to-peer excess sharing (light version).
        Only acts if after storage dispatch some nodes still have surplus AND others deficit.
        Uses direct neighbor lookup only (no global path search) — fast and local.
        Caps transfer at 0.15 MW to keep it partial.
        """
        import networkx as nx

        active_graph = nx.Graph()
        for u, v, data in grid.graph.edges(data=True):
            if data.get("active", True) and not grid.nodes[u].failed and not grid.nodes[v].failed:
                active_graph.add_edge(u, v)

        transfers = 0
        for nid, node in grid.nodes.items():
            if node.failed or node.isolated or node.excess_energy < 0.15:
                continue
            if nid not in active_graph:
                continue
            for nbr in active_graph.neighbors(nid):
                nbr_node = grid.nodes[nbr]
                if nbr_node.failed or nbr_node.isolated or nbr_node.deficit < 0.10:
                    continue
                transfer = min(node.excess_energy * 0.5, nbr_node.deficit, 0.15)
                if transfer < 0.01:
                    continue
                node.excess_energy  = max(0.0, node.excess_energy - transfer)
                nbr_node.deficit    = max(0.0, nbr_node.deficit - transfer * 0.92)
                nbr_node.generation = min(2.5, nbr_node.generation + transfer * 0.92)
                # Update flow on the graph edge
                if grid.graph.has_edge(nid, nbr):
                    grid.graph[nid][nbr]["flow"] = round(
                        grid.graph[nid][nbr].get("flow", 0.0) + transfer, 4
                    )
                transfers += 1

        if transfers:
            self.ems_log.append(f"↔️ Peer sharing: {transfers} local transfer(s) completed")

    # ------------------------------------------------------------------
    # PyPSA Integration Helpers
    # ------------------------------------------------------------------

    def _apply_pypsa_dispatch(self, grid: "SmartGrid", result: "OptimizationResult"):
        """Apply PyPSA optimization results to grid nodes."""
        # Update battery SOC based on PyPSA dispatch
        for decision in result.decisions:
            if decision.source_type == "battery" and decision.source in grid.nodes:
                battery = grid.nodes[decision.source]
                discharge_mw = decision.amount_mw
                # Update battery level
                soc_change = discharge_mw / battery.battery_capacity
                battery.battery_level = max(0.0, battery.battery_level - soc_change)
                # Add generation from battery discharge
                battery.generation += discharge_mw

    def _report_pypsa(self, gen: float, load: float, result: "OptimizationResult", message: str) -> dict:
        """Generate report including PyPSA optimization details."""
        # Build decision summary
        decisions_summary = []
        renewable_used = 0.0
        fossil_used = 0.0

        for d in result.decisions:
            if d.amount_mw > 0.01:
                decisions_summary.append({
                    "source": d.source,
                    "type": d.source_type,
                    "mw": round(d.amount_mw, 3),
                    "cost": d.cost_per_mwh
                })
                if d.source_type in ["solar", "wind"]:
                    renewable_used += d.amount_mw
                elif d.source_type in ["coal", "nuclear"]:
                    fossil_used += d.amount_mw

        return {
            "cycle": self.cycle,
            "total_gen": round(gen, 4),
            "total_load": round(load, 4),
            "balance": round(gen - load, 4),
            "log": self.ems_log.copy(),
            "message": message,
            "absorption_ratio": ABSORPTION_RATIO,
            "pypsa": {
                "enabled": True,
                "total_cost": round(result.total_cost, 2),
                "renewable_percentage": result._calculate_renewable_pct() if hasattr(result, '_calculate_renewable_pct') else 0.0,
                "battery_soc": round(result.battery_soc, 3),
                "battery_dispatch": round(result.battery_dispatch, 3),
                "grid_import": round(result.grid_import, 3),
                "decisions": decisions_summary[:10],  # Limit to top 10
                "renewable_used_mw": round(renewable_used, 3),
                "fossil_used_mw": round(fossil_used, 3),
            }
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _report(self, gen: float, load: float, message: str) -> dict:
        return {
            "cycle":       self.cycle,
            "total_gen":   round(gen, 4),
            "total_load":  round(load, 4),
            "balance":     round(gen - load, 4),
            "log":         self.ems_log.copy(),
            "message":     message,
            "absorption_ratio": ABSORPTION_RATIO,
        }

    def _apply_pypsa_dispatch(self, grid: "SmartGrid", result: "OptimizationResult"):
        """
        Apply PyPSA optimization results to the grid nodes.
        Updates generator setpoints and battery dispatch.
        """
        # Apply dispatch decisions to nodes
        for decision in result.decisions:
            if decision.source in grid.nodes:
                node = grid.nodes[decision.source]
                if decision.source_type in ["solar", "wind", "coal", "nuclear"]:
                    # Update generator output
                    node.generation = decision.amount_mw
                elif decision.source_type == "battery":
                    # Battery discharge
                    node.generation = decision.amount_mw
                    # Reduce battery SOC
                    if node.battery_capacity > 0:
                        node.battery_level = max(0.0, node.battery_level -
                            decision.amount_mw / node.battery_capacity * 0.5)

    def _report_pypsa(self, gen: float, load: float, result: "OptimizationResult", message: str) -> dict:
        """
        Generate report including PyPSA optimization results.
        """
        # Calculate percentages
        renewable_pct = result._calculate_renewable_pct() if hasattr(result, '_calculate_renewable_pct') else 0.0

        # Build decision summary for UI
        decisions_summary = []
        for d in result.decisions:
            if d.amount_mw > 0.01:
                emoji = {
                    "solar": "☀️", "wind": "💨", "battery": "🔋",
                    "coal": "🏭", "nuclear": "⚛️", "grid": "🔌"
                }.get(d.source_type, "⚡")
                decisions_summary.append(
                    f"{emoji} {d.source_type.capitalize()}: {d.amount_mw:.2f} MW"
                )

        report = {
            "cycle":       self.cycle,
            "total_gen":   round(gen, 4),
            "total_load":  round(load, 4),
            "balance":     round(gen - load, 4),
            "log":         self.ems_log.copy(),
            "message":     message,
            "absorption_ratio": ABSORPTION_RATIO,
            # PyPSA-specific fields
            "pypsa_optimized": True,
            "total_cost":   round(result.total_cost, 2),
            "renewable_pct": renewable_pct,
            "battery_dispatch": round(result.battery_dispatch, 4),
            "grid_import":   round(result.grid_import, 4),
            "solver_status": result.solver_status,
            "dispatch_summary": decisions_summary,
        }
        return report
