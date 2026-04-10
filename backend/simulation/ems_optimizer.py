"""
ems_optimizer.py — PyPSA-based Energy Management System Optimizer

Replaces rule-based EMS with mathematical optimization for cost-aware dispatch.
Separates energy generators (renewable/conventional) from storage systems.

Key Features:
- Minimizes total operating cost
- Prioritizes renewable energy (solar/wind at zero marginal cost)
- Optimizes battery charge/discharge timing
- Handles grid import/export decisions
- Provides detailed dispatch decisions for UI display

Integration Flow:
1. Grid Physics (NetworkX) → 2. PyPSA EMS Optimization → 3. Power Flow → 4. SCADA/FLISR
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Dict, List, Tuple, Optional
from dataclasses import dataclass

# PyPSA for optimization
logger = logging.getLogger(__name__)

try:
    import pypsa  # type: ignore
    import numpy as np
    import pandas as pd
    PYPSA_AVAILABLE = True
    
    # Suppress verbose pypsa and linopy logs globally to avoid console spam
    logging.getLogger("pypsa").setLevel(logging.ERROR)
    logging.getLogger("linopy").setLevel(logging.ERROR)
except ImportError:
    PYPSA_AVAILABLE = False
    logger.warning("PyPSA not installed. Running in fallback mode.")

if TYPE_CHECKING:
    from simulation.grid import SmartGrid
    from simulation.node import GridNode


@dataclass
class DispatchDecision:
    """Represents a single energy dispatch decision."""
    source: str           # Node ID
    source_type: str      # solar, wind, battery, coal, nuclear, grid
    amount_mw: float      # Power dispatched
    cost_per_mwh: float   # Marginal cost
    total_cost: float     # amount_mw * cost_per_mwh


@dataclass
class OptimizationResult:
    """Complete result from PyPSA optimization."""
    total_cost: float
    total_generation: float
    total_load: float
    battery_soc: float           # State of charge after optimization
    battery_dispatch: float    # Net battery power (positive=discharge, negative=charge)
    grid_import: float           # Power imported from main grid
    grid_export: float           # Power exported to main grid
    decisions: List[DispatchDecision]
    solver_status: str

    def to_dict(self) -> dict:
        """Convert to dictionary for API serialization."""
        return {
            "total_cost": round(self.total_cost, 2),
            "total_generation": round(self.total_generation, 4),
            "total_load": round(self.total_load, 4),
            "battery_soc": round(self.battery_soc, 4),
            "battery_dispatch": round(self.battery_dispatch, 4),
            "grid_import": round(self.grid_import, 4),
            "grid_export": round(self.grid_export, 4),
            "solver_status": self.solver_status,
            "decisions": [
                {
                    "source": d.source,
                    "type": d.source_type,
                    "amount_mw": round(d.amount_mw, 4),
                    "cost_per_mwh": d.cost_per_mwh,
                    "total_cost": round(d.total_cost, 2)
                }
                for d in self.decisions
            ],
            "renewable_percentage": self._calculate_renewable_pct(),
        }

    def _calculate_renewable_pct(self) -> float:
        """Calculate percentage of load served by renewables."""
        renewable_gen = sum(d.amount_mw for d in self.decisions
                          if d.source_type in ["solar", "wind"])
        if self.total_load > 0:
            return round((renewable_gen / self.total_load) * 100, 1)
        return 0.0


class EMSOptimizer:
    """
    PyPSA-based Energy Management System Optimizer.

    Separates energy generators from storage systems:
    - Generators: solar, wind, coal, nuclear (produce energy)
    - Storage: battery, supercapacitor (store energy)

    Uses linear programming to minimize cost while meeting demand.
    """

    # Default marginal costs in $/MWh
    DEFAULT_COSTS = {
        "solar": 0.0,      # Zero marginal cost
        "wind": 0.0,       # Zero marginal cost
        "battery": 5.0,    # Small degradation cost
        "supercap": 10.0,  # Higher degradation for fast response
        "coal": 50.0,      # Fuel + emissions cost
        "nuclear": 30.0,   # Low fuel cost
        "grid": 100.0,     # Import cost
    }

    # Storage parameters
    BATTERY_EFFICIENCY = 0.95      # Round-trip efficiency
    SUPERCAP_EFFICIENCY = 0.98     # Higher efficiency, lower capacity
    BATTERY_MAX_HOURS = 4          # Hours of storage at max power

    def __init__(self, use_pypsa: bool = True):
        """
        Initialize the EMS Optimizer.

        Args:
            use_pypsa: If True, use PyPSA optimization. If False, use rule-based fallback.
        """
        self.use_pypsa = use_pypsa and PYPSA_AVAILABLE
        self.cycle: int = 0
        self.ems_log: List[str] = []

        if not PYPSA_AVAILABLE and use_pypsa:
            logger.warning("PyPSA not available, using rule-based fallback")
            self.use_pypsa = False

    def optimize(self, grid: "SmartGrid") -> OptimizationResult:
        """
        Run optimization for the current grid state.

        Args:
            grid: The SmartGrid instance to optimize

        Returns:
            OptimizationResult with dispatch decisions
        """
        self.cycle += 1
        self.ems_log = []

        if self.use_pypsa:
            try:
                result = self._pypsa_optimize(grid)
                self.ems_log.append(f"✅ PyPSA optimization successful (cycle {self.cycle})")
                return result
            except Exception as e:
                logger.error(f"PyPSA optimization failed: {e}")
                self.ems_log.append(f"⚠️ PyPSA failed, using fallback: {str(e)[:50]}")
                return self._fallback_optimize(grid)
        else:
            return self._fallback_optimize(grid)

    def _pypsa_optimize(self, grid: "SmartGrid") -> OptimizationResult:
        """
        PyPSA-based optimization.

        Creates a PyPSA network with:
        - Bus representing the grid
        - Generators for each energy source
        - Storage units for batteries/supercaps
        - Load representing total demand
        """
        # Create PyPSA network
        network = pypsa.Network()
        network.set_snapshots([0])  # Single timestep optimization

        # Add main bus with carrier defined
        network.add("Bus", "grid_bus", carrier="AC")

        # Track nodes by type
        generators: List[Tuple["GridNode", float]] = []  # (node, max_power)
        storage_units: List["GridNode"] = []
        total_load = 0.0

        # Categorize nodes
        for node in grid.nodes.values():
            if node.failed or node.isolated:
                continue

            # Calculate marginal cost
            cost = self._get_marginal_cost(node)

            if node.node_type in ["solar", "wind"]:
                # Renewable generators - variable output
                max_power = node.generation
                if max_power > 0.01:
                    network.add(
                        "Generator",
                        node.node_id,
                        bus="grid_bus",
                        p_nom=max_power,
                        p_max_pu=1.0,
                        p_min_pu=0.0,  # Can curtail
                        marginal_cost=cost
                    )
                    generators.append((node, max_power))

            elif node.node_type == "generator":
                # Conventional generators (coal, nuclear)
                max_power = getattr(node, "max_generation", 10.0)
                current_gen = node.generation
                network.add(
                    "Generator",
                    node.node_id,
                    bus="grid_bus",
                    p_nom=max_power,
                    p_max_pu=1.0,
                    p_min_pu=0.0,  # Can reduce output
                    marginal_cost=cost
                )
                generators.append((node, max_power))

            elif node.node_type == "battery":
                # Grid-scale battery
                storage_units.append(node)
                capacity = node.battery_capacity
                current_soc = node.battery_level

                network.add(
                    "StorageUnit",
                    node.node_id,
                    bus="grid_bus",
                    p_nom=capacity * 0.25,  # Max charge/discharge rate
                    max_hours=self.BATTERY_MAX_HOURS,
                    efficiency_store=self.BATTERY_EFFICIENCY,
                    efficiency_dispatch=self.BATTERY_EFFICIENCY,
                    standing_loss=0.01,  # 1% per hour self-discharge
                    inflow=0,  # No natural inflow
                    state_of_charge_initial=current_soc * capacity,
                    cyclic_state_of_charge=False,  # Track actual SOC
                    marginal_cost=cost
                )

            elif node.node_type == "house":
                # Houses contribute to load
                if node.load > 0:
                    total_load += node.load

            elif node.node_type == "substation":
                # Substations also have load
                if node.load > 0:
                    total_load += node.load

        # Add grid import as expensive generator (last resort)
        network.add(
            "Generator",
            "grid_import",
            bus="grid_bus",
            p_nom=100.0,  # Large capacity
            marginal_cost=self.DEFAULT_COSTS["grid"]
        )

        # Add load
        network.add(
            "Load",
            "total_demand",
            bus="grid_bus",
            p_set=total_load
        )

        # Solve optimization (try different solvers in order of preference)
        solver_status = "unknown"
        
        import warnings
        import os
        from contextlib import redirect_stdout, redirect_stderr
        
        with warnings.catch_warnings():
            # Suppress FutureWarnings and PyPSA consistency warnings
            warnings.simplefilter("ignore")
            # Temporarily redirect python stdout to avoid solver print spam
            with open(os.devnull, 'w') as fnull:
                with redirect_stdout(fnull), redirect_stderr(fnull):
                    # Prioritize highs since it's most robust and solved successfully in user's env
                    for solver in ["highs", "glpk", "cbc", "scipy"]:
                        try:
                            network.optimize(solver_name=solver)
                            solver_status = f"solved_with_{solver}"
                            break
                        except Exception as e:
                            logger.debug(f"Solver {solver} failed: {e}")
                            continue
                    else:
                        # If all solvers fail, raise error to trigger fallback
                        raise RuntimeError("No LP solver available (tried: highs, glpk, cbc, scipy)")

        # Extract results
        return self._extract_results(network, grid, generators, storage_units, total_load)

    def _extract_results(self, network, grid: "SmartGrid",
                        generators: List[Tuple["GridNode", float]],
                        storage_units: List["GridNode"],
                        total_load: float) -> OptimizationResult:
        """Extract optimization results from PyPSA network."""

        decisions: List[DispatchDecision] = []
        total_cost = 0.0
        total_gen = 0.0
        battery_dispatch = 0.0
        battery_soc = 0.0
        grid_import = 0.0

        # Extract generator dispatch
        for gen in network.generators.index:
            if gen == "grid_import":
                power = network.generators_t.p[gen].iloc[0]
                if power > 0.01:
                    grid_import = power
                    total_cost += power * self.DEFAULT_COSTS["grid"]
                    decisions.append(DispatchDecision(
                        source="Grid Import",
                        source_type="grid",
                        amount_mw=power,
                        cost_per_mwh=self.DEFAULT_COSTS["grid"],
                        total_cost=power * self.DEFAULT_COSTS["grid"]
                    ))
            else:
                power = network.generators_t.p[gen].iloc[0]
                if power > 0.01 and gen in grid.nodes:
                    node = grid.nodes[gen]
                    cost = self._get_marginal_cost(node)
                    total_gen += power
                    total_cost += power * cost
                    decisions.append(DispatchDecision(
                        source=gen,
                        source_type=node.source_type or node.node_type,
                        amount_mw=power,
                        cost_per_mwh=cost,
                        total_cost=power * cost
                    ))
                    # Update node's actual generation
                    node.generation = power

        # Extract storage dispatch
        for store in network.storage_units.index:
            power = network.storage_units_t.p[store].iloc[0]
            soc = network.storage_units_t.state_of_charge[store].iloc[0] if hasattr(
                network.storage_units_t, 'state_of_charge') else 0

            if abs(power) > 0.01 and store in grid.nodes:
                node = grid.nodes[store]
                battery_dispatch = power  # Positive = discharge, Negative = charge
                battery_soc = soc / node.battery_capacity if node.battery_capacity > 0 else 0

                if power > 0:  # Discharging
                    total_gen += power
                    decisions.append(DispatchDecision(
                        source=store,
                        source_type="battery",
                        amount_mw=power,
                        cost_per_mwh=self.DEFAULT_COSTS["battery"],
                        total_cost=power * self.DEFAULT_COSTS["battery"]
                    ))
                else:  # Charging
                    decisions.append(DispatchDecision(
                        source=store,
                        source_type="battery_charge",
                        amount_mw=abs(power),
                        cost_per_mwh=-self.DEFAULT_COSTS["battery"],  # Negative cost = saving
                        total_cost=power * self.DEFAULT_COSTS["battery"]  # power is negative
                    ))

                # Update node's battery level
                node.battery_level = min(1.0, max(0.0, battery_soc))

        return OptimizationResult(
            total_cost=total_cost,
            total_generation=total_gen,
            total_load=total_load,
            battery_soc=battery_soc,
            battery_dispatch=battery_dispatch,
            grid_import=grid_import,
            grid_export=0.0,  # Not tracking exports yet
            decisions=sorted(decisions, key=lambda d: d.cost_per_mwh),
            solver_status="optimal"
        )

    def _fallback_optimize(self, grid: "SmartGrid") -> OptimizationResult:
        """
        Rule-based fallback optimization when PyPSA is unavailable.
        Uses priority-based dispatch: solar/wind -> battery -> coal -> nuclear -> grid
        """
        decisions: List[DispatchDecision] = []

        active_nodes = [n for n in grid.nodes.values()
                       if not n.failed and not n.isolated]

        # Calculate totals
        total_load = sum(n.load for n in active_nodes)

        # Categorize generation
        solar_gen = sum(n.generation for n in active_nodes if n.node_type == "solar")
        wind_gen = sum(n.generation for n in active_nodes if n.node_type == "wind")

        # Categorize conventional generation
        coal_nodes = [n for n in active_nodes
                     if n.node_type == "generator" and n.source_type == "coal"]
        nuclear_nodes = [n for n in active_nodes
                        if n.node_type == "generator" and n.source_type == "nuclear"]
        battery_nodes = [n for n in active_nodes if n.node_type == "battery"]

        # Track dispatch
        dispatched = 0.0
        total_cost = 0.0
        battery_dispatch = 0.0

        # Priority 1: Solar and Wind (free)
        if solar_gen > 0.01:
            used = min(solar_gen, total_load)
            decisions.append(DispatchDecision(
                source="Solar Farm",
                source_type="solar",
                amount_mw=used,
                cost_per_mwh=0.0,
                total_cost=0.0
            ))
            dispatched += used

        if wind_gen > 0.01:
            used = min(wind_gen, total_load - dispatched)
            decisions.append(DispatchDecision(
                source="Wind Farm",
                source_type="wind",
                amount_mw=used,
                cost_per_mwh=0.0,
                total_cost=0.0
            ))
            dispatched += used

        # Priority 2: Battery discharge (if deficit)
        remaining = total_load - dispatched
        if remaining > 0.25 and battery_nodes:
            for battery in battery_nodes:
                available = battery.battery_level * battery.battery_capacity
                discharge = min(available * 0.3, remaining, 10.0)  # Max 10MW

                if discharge > 0.1:
                    battery.battery_level -= discharge / battery.battery_capacity
                    battery_dispatch += discharge
                    decisions.append(DispatchDecision(
                        source=battery.node_id,
                        source_type="battery",
                        amount_mw=discharge,
                        cost_per_mwh=self.DEFAULT_COSTS["battery"],
                        total_cost=discharge * self.DEFAULT_COSTS["battery"]
                    ))
                    dispatched += discharge
                    remaining -= discharge

        # Priority 3: Coal (ramp up)
        remaining = total_load - dispatched
        if remaining > 0.25 and coal_nodes:
            for coal in coal_nodes:
                headroom = getattr(coal, "max_generation", 10.0) - coal.generation
                ramp = min(headroom, remaining, 5.0)

                if ramp > 0.1:
                    coal.generation += ramp
                    decisions.append(DispatchDecision(
                        source=coal.node_id,
                        source_type="coal",
                        amount_mw=ramp,
                        cost_per_mwh=self.DEFAULT_COSTS["coal"],
                        total_cost=ramp * self.DEFAULT_COSTS["coal"]
                    ))
                    dispatched += ramp
                    remaining -= ramp

        # Priority 4: Nuclear (slow ramp)
        remaining = total_load - dispatched
        if remaining > 0.5 and nuclear_nodes:
            for nuc in nuclear_nodes:
                headroom = getattr(nuc, "max_generation", 15.0) - nuc.generation
                ramp = min(headroom * 0.1, remaining)  # Slow ramp

                if ramp > 0.1:
                    nuc.generation += ramp
                    decisions.append(DispatchDecision(
                        source=nuc.node_id,
                        source_type="nuclear",
                        amount_mw=ramp,
                        cost_per_mwh=self.DEFAULT_COSTS["nuclear"],
                        total_cost=ramp * self.DEFAULT_COSTS["nuclear"]
                    ))
                    dispatched += ramp
                    remaining -= ramp

        # Priority 5: Grid import (expensive)
        remaining = total_load - dispatched
        grid_import = max(0.0, remaining)
        if grid_import > 0.01:
            decisions.append(DispatchDecision(
                source="Grid Import",
                source_type="grid",
                amount_mw=grid_import,
                cost_per_mwh=self.DEFAULT_COSTS["grid"],
                total_cost=grid_import * self.DEFAULT_COSTS["grid"]
            ))

        # Calculate total cost
        total_cost = sum(d.total_cost for d in decisions)

        # Calculate average battery SOC
        avg_battery_soc = sum(n.battery_level for n in battery_nodes) / len(battery_nodes) if battery_nodes else 0.0

        return OptimizationResult(
            total_cost=total_cost,
            total_generation=dispatched,
            total_load=total_load,
            battery_soc=avg_battery_soc,
            battery_dispatch=battery_dispatch,
            grid_import=grid_import,
            grid_export=0.0,
            decisions=sorted(decisions, key=lambda d: d.cost_per_mwh),
            solver_status="fallback_rule_based"
        )

    def _get_marginal_cost(self, node: "GridNode") -> float:
        """Get marginal cost for a node based on its type and source."""
        # Use node's custom cost if set, otherwise use defaults
        if hasattr(node, "marginal_cost") and node.marginal_cost > 0:
            return node.marginal_cost

        if node.node_type in ["solar", "wind"]:
            return self.DEFAULT_COSTS[node.node_type]
        elif node.source_type in self.DEFAULT_COSTS:
            return self.DEFAULT_COSTS[node.source_type]
        elif node.node_type == "battery":
            return self.DEFAULT_COSTS["battery"]
        else:
            return 50.0  # Default cost


def optimize_energy(grid: "SmartGrid", use_pypsa: bool = True) -> OptimizationResult:
    """
    Convenience function to run EMS optimization.

    Args:
        grid: The SmartGrid to optimize
        use_pypsa: Whether to use PyPSA (if available) or rule-based fallback

    Returns:
        OptimizationResult with dispatch decisions
    """
    optimizer = EMSOptimizer(use_pypsa=use_pypsa)
    return optimizer.optimize(grid)


# For backward compatibility with existing EMS
class PyPSAEMSBridge:
    """
    Bridge class to integrate PyPSA optimizer with existing EMS.
    Maintains the same interface as the original EMS for easy drop-in replacement.
    """

    def __init__(self, use_pypsa: bool = True):
        self.optimizer = EMSOptimizer(use_pypsa=use_pypsa)
        self.cycle: int = 0
        self.ems_log: List[str] = []
        self.last_result: Optional[OptimizationResult] = None

    def run(self, grid: "SmartGrid") -> dict:
        """
        Run one EMS cycle with PyPSA optimization.
        Compatible with the original EMS.run() interface.
        """
        self.cycle += 1
        self.ems_log = []

        result = self.optimizer.optimize(grid)
        self.last_result = result

        # Generate human-readable log
        decisions_by_type: Dict[str, float] = {}
        for d in result.decisions:
            decisions_by_type[d.source_type] = decisions_by_type.get(d.source_type, 0) + d.amount_mw

        if "solar" in decisions_by_type:
            self.ems_log.append(f"☀️ Solar: {decisions_by_type['solar']:.2f} MW (free)")
        if "wind" in decisions_by_type:
            self.ems_log.append(f"💨 Wind: {decisions_by_type['wind']:.2f} MW (free)")
        if "battery" in decisions_by_type:
            self.ems_log.append(f"🔋 Battery: {decisions_by_type['battery']:.2f} MW (${self.optimizer.DEFAULT_COSTS['battery']}/MWh)")
        if "coal" in decisions_by_type:
            self.ems_log.append(f"🏭 Coal: {decisions_by_type['coal']:.2f} MW (${self.optimizer.DEFAULT_COSTS['coal']}/MWh)")
        if "nuclear" in decisions_by_type:
            self.ems_log.append(f"⚛️ Nuclear: {decisions_by_type['nuclear']:.2f} MW (${self.optimizer.DEFAULT_COSTS['nuclear']}/MWh)")
        if "grid" in decisions_by_type:
            self.ems_log.append(f"🔌 Grid Import: {decisions_by_type['grid']:.2f} MW (${self.optimizer.DEFAULT_COSTS['grid']}/MWh)")

        self.ems_log.append(f"💰 Total Cost: ${result.total_cost:.2f}")

        return result.to_dict()

    def run_for_cluster(self, grid: "SmartGrid", cluster_node_ids: list) -> dict:
        """
        Local optimization for isolated cluster (FLISR integration).
        Falls back to rule-based for cluster-level decisions.
        """
        # For clusters, use priority-based local dispatch
        served_mw = 0.0
        actions = []

        cluster_nodes = [
            grid.nodes[nid] for nid in cluster_node_ids
            if nid in grid.nodes and not grid.nodes[nid].failed
        ]
        cluster_nodes.sort(key=lambda n: n.priority)

        for node in cluster_nodes:
            # Supercap first
            if node.supercap_level > 0.1:
                sc = node.use_supercapacitor(0.5)
                if sc > 0.001:
                    served_mw += sc
                    actions.append(f"⚡ Supercap at {node.node_id}: {sc:.2f} MW")

            # Battery second
            if node.battery_level > 0.2:
                need = min(node.load, 2.0)
                delivered = node.use_battery(need)
                if delivered > 0.001:
                    served_mw += delivered
                    actions.append(f"🔋 Battery at {node.node_id}: {delivered:.2f} MW")

        self.ems_log.extend(actions)
        return {"served_mw": round(served_mw, 4), "actions": actions}
