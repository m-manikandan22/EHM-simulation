"""
grid.py — SmartGrid class built on a NetworkX graph.

Represents the entire power grid:
  - 3 Generators   (G0-G2)   — high-generation sources
  - 6 Substations  (S0-S5)   — medium-voltage ring
  - 12 Transformers (T0-T11) — feeder head nodes
  - 60-80 Houses   (H0-H…)   — radial feeder end-consumers

Topology (matches real-world distribution wiring):
  Generator → Substation ring → Transformer → FEEDER BACKBONE (Poles) → Houses as parallel taps
  Each feeder: T ─── P0 ─── P1 ─── P2 ─── P3
                    │    │    │    │
                    H0   H1   H2   H3  (parallel service drops)

Key capabilities:
  - Energy flow simulation across edges
  - Node failure injection + BFS-based rerouting (self-healing)
  - Storm event (spikes load on all nodes)
  - Multi-agent coordination (excess energy sharing)
  - Full serialisable state for the API
"""

import random
import math
from collections import deque
from typing import Optional

import networkx as nx  # type: ignore

from simulation.node import GridNode  # type: ignore


# ── 24-Hour Time-of-Day Profiles ─────────────────────────────────────────────────
# Solar irradiance factor 0.15–1.0 by hour (0.15 minimum = inverter standby / diffuse light)
SOLAR_CURVE = [
    0.15, 0.15, 0.15, 0.15, 0.15, 0.20,
    0.35, 0.55, 0.78, 0.92, 1.00, 1.00,
    0.95, 0.87, 0.72, 0.52, 0.30, 0.18,
    0.15, 0.15, 0.15, 0.15, 0.15, 0.15,
]
# Wind generation factor 0–1 by hour (stronger at night / early morning)
WIND_CURVE = [
    0.85, 0.90, 0.92, 0.90, 0.85, 0.75,
    0.65, 0.55, 0.45, 0.40, 0.38, 0.40,
    0.42, 0.48, 0.55, 0.60, 0.68, 0.75,
    0.80, 0.85, 0.88, 0.90, 0.88, 0.86,
]
# Residential demand multiplier by hour (morning & evening peaks)
LOAD_CURVE = [
    0.35, 0.30, 0.28, 0.27, 0.30, 0.45,
    0.65, 0.85, 1.00, 0.95, 0.88, 0.90,
    1.00, 0.95, 0.85, 0.90, 1.10, 1.20,
    1.10, 0.90, 0.78, 0.65, 0.50, 0.40,
]


class SmartGrid:
    """
    Manages the smart grid graph and orchestrates per-timestep simulation.
    """

    def __init__(self):
        # FIX: Use DiGraph for directional power flow (source → downstream)
        self.graph = nx.DiGraph()
        self.nodes: dict[str, GridNode] = {}
        self.timestep: int = 0
        self.storm_active: bool = False
        self.total_energy_loss: float = 0.0
        self.avg_frequency = 50.0  # 🔥 Ensure this is explicitly 50.0 at start
        self.event_log: list[str] = []   # recent events for API consumers
        self.reclose_queue: dict[tuple, tuple] = {}
        # ── FLISR: last identified fault segment (for UI overlay + logging) ──
        self.last_fault_segment: dict = {}  # {start_switch, end_switch, affected_nodes}
        
        self._build_grid()

        # ── INITIAL STATE RESET (MANDATORY) ──
        for node in self.nodes.values():
            node.failed = False
            node.isolated = False
            node.voltage = 1.0
            node.received_power = 0.0

        # 🔥 CRITICAL FIX: Run full physics once at startup
        # This guarantees all nodes excited and flow computed so React frontend renders flowing immediately
        for _ in range(3):
            self.update_generation()
            self.update_power_flow()

    # ------------------------------------------------------------------
    # Grid Construction
    # ------------------------------------------------------------------

    def _build_grid(self):
        """
        Phase 4: MAIN SUBSTATION Architecture (1500×920 canvas)

        Clear visual hierarchy with single entry point:
        ┌─────────────────────────────────────────────────────────────┐
        │ LEFT (x=0-150)     │ CENTER (x=200-350)   │ RIGHT (x=400+) │
        │                    │                      │                │
        │  GENERATION ZONE   │  MAIN SUBSTATION     │  DISTRIBUTION  │
        │  ☀️ Solar Farm     │      ⚡ S_MAIN        │    GRID        │
        │  🌬️ Wind Farm      │         │            │   ├─Feeder A   │
        │  ⚛️ Nuclear Plant  │    ┌────┴────┐       │   ├─Feeder B   │
        │  ⛏️ Coal Plant     │    │ Storage │       │   └─Feeder C   │
        │                    │    🔋   ⚡    │       │                │
        └────────────────────┴────────┬───────────┴────────────────┘
                                        │
                              All power flows THROUGH here
        """
        def make_node(nid, ntype, x, y, gen=0.0, load=0.0,
                      source_type="none", role="load", street=""):
            n = GridNode(nid, node_type=ntype, x=x, y=y)
            n.street         = street
            n.generation     = gen
            n.load           = load
            n._base_generation = gen
            n._base_load     = load
            n.source_type    = source_type
            n.role           = role
            self.nodes[nid]  = n
            self.graph.add_node(nid)
            return n

        # ═══════════════════════════════════════════════════════════════
        # ZONE 1: GENERATION (Left Side - x=50-120)
        # ═══════════════════════════════════════════════════════════════

        # Solar Farm (Top-left)
        solar = make_node("GEN_SOLAR", "generator_solar", 80, 150,
                         random.uniform(6, 9), 0.0, "solar", "generation", "Gen Zone")
        solar.label = "Solar Farm"
        solar.marginal_cost = 0.0

        # Wind Farm (Upper-mid left)
        wind = make_node("GEN_WIND", "generator_wind", 80, 300,
                        random.uniform(7, 11), 0.0, "wind", "generation", "Gen Zone")
        wind.label = "Wind Farm"
        wind.marginal_cost = 0.0

        # Nuclear Plant (Lower-mid left) - Baseload
        nuclear = make_node("GEN_NUCLEAR", "generator_nuclear", 80, 450,
                           random.uniform(12, 16), 0.2, "nuclear", "generation", "Gen Zone")
        nuclear.label = "Nuclear Plant"
        nuclear.marginal_cost = 30.0

        # Coal Plant (Bottom-left) - Backup
        coal = make_node("GEN_COAL", "generator_coal", 80, 600,
                        random.uniform(6, 8), 0.1, "coal", "generation", "Gen Zone")
        coal.label = "Coal Plant"
        coal.marginal_cost = 50.0

        # Gas Peaker (Backup for fast ramping)
        gas = make_node("GEN_GAS", "generator_gas", 80, 750,
                       random.uniform(5, 7), 0.1, "gas", "generation", "Gen Zone")
        gas.label = "Gas Peaker"
        gas.marginal_cost = 60.0

        # ═══════════════════════════════════════════════════════════════
        # ZONE 2: MAIN SUBSTATION + STORAGE (Center - x=250-350)
        # ═══════════════════════════════════════════════════════════════

        # MAIN SUBSTATION - Single aggregation point for ALL generation
        s_main = make_node("S_MAIN", "substation", 280, 400,
                          load=random.uniform(2, 4), street="Main Bus")
        s_main.label = "Main Substation"
        s_main.priority = 1  # Critical infrastructure

        # Grid-Scale Battery - Connected directly to main substation
        battery = make_node("STORAGE_BAT", "battery", 350, 320,
                           0.0, 0.0, "battery", "storage", "Storage Zone")
        battery.label = "Grid Battery"
        battery.battery_capacity = 150.0
        battery.battery_level = 0.75
        battery.discharge_rate = 8.0  # MW discharge when needed
        battery.priority = 2
        battery.marginal_cost = 5.0

        # Supercapacitor - Connected to main substation for fast response
        supercap = make_node("STORAGE_SC", "supercap", 350, 480,
                            0.0, 0.0, "supercap", "support", "Storage Zone")
        supercap.label = "Supercapacitor"
        supercap.supercap_capacity = 15.0
        supercap.supercap_level = 1.0
        supercap.discharge_rate = 5.0
        supercap.priority = 1
        supercap.marginal_cost = 10.0

        # ═══════════════════════════════════════════════════════════════
        # CONNECTIONS: Generation → Main Substation → Storage
        # ═══════════════════════════════════════════════════════════════

        # All generators connect TO the main substation
        self._add_edge("GEN_SOLAR", "S_MAIN", capacity=30.0, resistance=0.001)
        self._add_edge("GEN_WIND", "S_MAIN", capacity=35.0, resistance=0.001)
        self._add_edge("GEN_NUCLEAR", "S_MAIN", capacity=50.0, resistance=0.001)
        self._add_edge("GEN_COAL", "S_MAIN", capacity=25.0, resistance=0.001)
        self._add_edge("GEN_GAS", "S_MAIN", capacity=20.0, resistance=0.001)

        # ─── STORAGE: Bidirectional connections ───────────────────────────
        # CHARGE path: S_MAIN → STORAGE  (grid charges battery when surplus)
        self._add_edge("S_MAIN", "STORAGE_BAT", capacity=15.0, resistance=0.002)
        self._add_edge("S_MAIN", "STORAGE_SC",  capacity=10.0, resistance=0.002)

        # ═══════════════════════════════════════════════════════════════
        # ZONE 3: DISTRIBUTION GRID (Right Side - x=450+)
        # Three feeders emanating from Main Substation
        # ═══════════════════════════════════════════════════════════════

        # ── FEEDER A — Residential (North feeder, y=200) ──────────────
        make_node("T_A", "transformer", 400, 220, load=random.uniform(1.0, 2.0), street="Feeder A")
        make_node("P_A1", "pole", 550, 220, street="Oak St")
        make_node("P_A2", "pole", 700, 220, street="Pine St")
        make_node("P_A3", "pole", 850, 220, street="Maple St")

        self._add_edge("S_MAIN", "T_A", capacity=40.0, resistance=0.002)
        self._add_edge("T_A", "P_A1", capacity=15.0, resistance=0.003, switch_type="recloser", has_switch=True)
        self._add_edge("P_A1", "P_A2", capacity=12.0, resistance=0.004, switch_type="sectionalizer", has_switch=True)
        self._add_edge("P_A2", "P_A3", capacity=12.0, resistance=0.004, switch_type="sectionalizer", has_switch=True)

        # ── FEEDER B — Mixed / Hospital (Central feeder, y=400) ───────────
        make_node("T_B", "transformer", 400, 400, load=random.uniform(1.5, 2.5), street="Feeder B")
        make_node("P_B1", "pole", 550, 400, street="Oak St")
        make_node("P_B2", "pole", 700, 400, street="Pine St")
        make_node("P_B3", "pole", 850, 400, street="Maple St")

        self._add_edge("S_MAIN", "T_B", capacity=45.0, resistance=0.002)
        self._add_edge("T_B", "P_B1", capacity=18.0, resistance=0.003, switch_type="recloser", has_switch=True)
        self._add_edge("P_B1", "P_B2", capacity=15.0, resistance=0.004, switch_type="sectionalizer", has_switch=True)
        self._add_edge("P_B2", "P_B3", capacity=15.0, resistance=0.004, switch_type="sectionalizer", has_switch=True)

        # Hospital - Critical load at end of Feeder B
        hosp = make_node("HOSP", "hospital", 1000, 400,
                        0.0, random.uniform(0.8, 1.2), "none", "critical", "Feeder B")
        hosp.priority = 1
        hosp.label = "General Hospital"
        self._add_edge("P_B3", "HOSP", capacity=8.0, resistance=0.004)

        # ── FEEDER C — Industrial (South feeder, y=580) ───────────────
        make_node("T_C", "transformer", 400, 580, load=random.uniform(2.0, 3.0), street="Feeder C")
        make_node("P_C1", "pole", 550, 580, street="Oak St")
        make_node("P_C2", "pole", 700, 580, street="Pine St")
        make_node("P_C3", "pole", 850, 580, street="Maple St")

        self._add_edge("S_MAIN", "T_C", capacity=50.0, resistance=0.002)
        self._add_edge("T_C", "P_C1", capacity=20.0, resistance=0.003, switch_type="recloser", has_switch=True)
        self._add_edge("P_C1", "P_C2", capacity=18.0, resistance=0.004, switch_type="sectionalizer", has_switch=True)
        self._add_edge("P_C2", "P_C3", capacity=18.0, resistance=0.004, switch_type="sectionalizer", has_switch=True)

        # Industrial Zone - Heavy load at end of Feeder C
        ind0 = make_node("IND0", "industry", 1000, 580,
                        0.05, random.uniform(3.0, 5.0), "none", "industrial", "Feeder C")
        ind0.priority = 2
        ind0.label = "Industrial Zone"
        self._add_edge("P_C3", "IND0", capacity=12.0, resistance=0.004)

        # ═══════════════════════════════════════════════════════════════
        # HOUSES & LATERALS: Distribution grid taps off feeder poles
        # ═══════════════════════════════════════════════════════════════
        hid = 0

        def build_lateral(parent, branch_id, st_name, root_x, root_y, dy_step, house_count):
            nonlocal hid
            curr = parent
            for i in range(1, house_count + 1):
                ly = root_y + dy_step * i
                # Lateral poles are distribution nodes
                ln = make_node(f"{branch_id}_{i}", "pole", root_x, ly, street=st_name)
                ln.priority = 2
                
                # Add a lateral fuse (switch) to the first connection point
                if i == 1:
                    self._add_edge(curr, ln.node_id, capacity=5.0, resistance=0.006, switch_type="sectionalizer", has_switch=True)
                else:
                    self._add_edge(curr, ln.node_id, capacity=5.0, resistance=0.006)

                # Tap a house off this lateral pole
                hx = root_x + (40 if i % 2 == 0 else -40)
                nid = f"H{hid}"
                h = make_node(nid, "house", x=hx, y=ly,
                              gen=random.uniform(0.1, 0.35),
                              load=random.uniform(0.2, 0.6), street=st_name)
                h.label = f"House {hid}"
                self._add_edge(ln.node_id, nid, capacity=2.0, resistance=0.008)
                curr = ln.node_id
                hid += 1
            return curr # Returns the last node on the lateral

        # FEEDER A laterals (Oak St at x=550, Pine St at x=700, Maple St at x=850)
        la0_end = build_lateral("P_A1", "LA0", "Oak St A", 550, 220, 40, 2)    # y=260, 300
        la1_end = build_lateral("P_A2", "LA1", "Pine St A", 700, 220, 40, 2)  # y=260, 300
        la2_end = build_lateral("P_A3", "LA2", "Maple St A", 850, 220, 40, 2) # y=260, 300

        # FEEDER B laterals (Oak St at x=550, Pine St at x=700)
        lb0_up  = build_lateral("P_B1", "LB0_UP", "Oak St B", 550, 400, -40, 1)  # y=360
        lb0_dn  = build_lateral("P_B1", "LB0_DN", "Oak St B Deep", 550, 400, 40, 1)  # y=440
        lb1_up  = build_lateral("P_B2", "LB1_UP", "Pine St B", 700, 400, -40, 1) # y=360
        lb1_dn  = make_node("LB1_DN", "pole", 700, 480, street="Pine St B Deep")
        self._add_edge("P_B2", "LB1_DN", capacity=5.0, resistance=0.006, switch_type="sectionalizer", has_switch=True)
        lb2_end = build_lateral("P_B3", "LB2", "Maple St B", 850, 400, -40, 2) # y=360, 320

        # FEEDER C laterals (Industrial - fewer houses)
        lc0_up  = build_lateral("P_C1", "LC0_UP", "Oak St C", 550, 580, -40, 1)  # y=540
        lc1_up  = build_lateral("P_C2", "LC1_UP", "Pine St C", 700, 580, -40, 1) # y=540

        # ═══════════════════════════════════════════════════════════════
        # TIE SWITCHES (Normally Open) - For FLISR self-healing
        # ═══════════════════════════════════════════════════════════════

        # Feeder B ↔ C ties (End)
        self._add_edge("P_B3", "P_C3", capacity=6.0, resistance=0.008, active=False, switch_type="tie", is_tie_switch=True)
        # HOSPITAL CRITICAL BACKUP
        self._add_edge("P_A3", "HOSP", capacity=8.0, resistance=0.012, active=False, switch_type="tie", is_tie_switch=True)
        # OPTIONAL GRID BALANCE
        self._add_edge("P_A2", "P_B2", capacity=5.0, resistance=0.010, active=False, switch_type="tie", is_tie_switch=True)

        # ── NODE PRIORITIES ──────────────────────────────────────────────
        for nid in self.nodes:
            if "G" in nid or "S" in nid or "T" in nid or "P" in nid or "L" in nid:
                self.nodes[nid].priority = 2
        for hid_idx in range(30):  # More houses now
            if f"H{hid_idx}" in self.nodes:
                self.nodes[f"H{hid_idx}"].priority = 3
        if "HOSP" in self.nodes: self.nodes["HOSP"].priority = 1
        if "IND0" in self.nodes: self.nodes["IND0"].priority = 2

        # ── NODE ROLES (for EMS dispatch) ──────────────────────────────
        for hid_idx in range(15):
            if f"H{hid_idx}" in self.nodes:
                h = self.nodes[f"H{hid_idx}"]
                if h._base_generation >= 0.25:
                    h.role = "generation"
                elif h._base_generation >= 0.15:
                    h.role = "storage"
                else:
                    h.role = "load"

        # Recloser metadata on feeder-head edges
        for u, v, data in self.graph.edges(data=True):
            if data.get("switch_type") == "recloser":
                data["reclose_attempts"] = 0
                data["reclose_max"]      = 3
                data["reclose_delay"]    = 2   # timesteps before auto-reclose
                data["locked_out"]       = False






    def add_house(self, target_id: str):
        """Dynamically add a new house to an existing pole or lateral."""
        if target_id not in self.nodes:
            return "Target node not found"
            
        target = self.nodes[target_id]
        
        # Determine next ID
        h_nodes = [nid for nid in self.nodes.keys() if nid.startswith('H') and nid != "HOSP"]
        h_idx = max([int(nid.replace('H', '')) for nid in h_nodes]) + 1 if h_nodes else 0
        new_id = f"H{h_idx}"
        
        # Visual spacing offset
        import random
        offset_y = 50 if target.y < 500 else -50
        offset_x = random.randint(-20, 20)
        
        h = GridNode(
            new_id, 
            node_type="house", 
            x=target.x + offset_x, 
            y=target.y + offset_y
        )
        h.load = random.uniform(0.2, 0.6)
        h.generation = random.uniform(0.1, 0.35)
        h._base_load = h.load
        h._base_generation = h.generation
        
        if h._base_generation >= 0.25:
            h.role = "generation"
        elif h._base_generation >= 0.15:
            h.role = "storage"
        else:
            h.role = "load"
            
        h.label = f"House {h_idx}"
        h.priority = 3
        
        self.nodes[new_id] = h
        self.graph.add_node(new_id)
        self._add_edge(target_id, new_id, capacity=3.0, resistance=0.01)
        
        return f"Added {new_id} to {target_id}"

    def _add_edge(self, u: str, v: str, capacity: float = None, resistance: float = None,
                  active: bool = True, switch_type: str = None, is_tie_switch: bool = False,
                  has_switch: bool = False):
        """
        Add a physical line between two nodes.
        switch_type: "sectionalizer" | "tie" | None
        switch_status is derived from `active`: open=False → 'open', True → 'closed'
        has_switch kept for backward compat (True if switch_type is set).
        """
        
        # Interactive constraints logic
        n_u = self.nodes.get(u)
        n_v = self.nodes.get(v)
        
        if not n_u or not n_v:
            raise ValueError(f"Nodes {u} and {v} must exist to connect.")
            
        types = {n_u.node_type, n_v.node_type}
        
        # Hard constraint rules preventing random spaghetti logic
        if "house" in types and "house" in types and len(types) == 1:
            raise ValueError("Houses cannot connect directly to houses.")
        if "generator" in types and "house" in types:
            raise ValueError("Generators cannot feed houses directly (requires transformers).")
            
        # Euclidean Distance calculation
        dx = n_u.x - n_v.x
        dy = n_u.y - n_v.y
        dist = math.hypot(dx, dy)
        
        # Dynamic physical resistance based natively on drawn line length
        if resistance is None:
            resistance = max(0.001, dist * 0.0001)
            
        # Dynamic generic capacity based on distance and type (simplified)
        if capacity is None:
            if "generator" in types: capacity = 30.0
            elif "step_up" in types: capacity = 15.0
            elif "substation" in types: capacity = 15.0
            elif "transformer" in types: capacity = 10.0
            elif "service" in types or "pole" in types: capacity = 5.0
            else: capacity = 2.0
            
        if switch_type is not None:
            has_switch = True   # auto-set for backward compat
        switch_status = "open" if not active else "closed"

        edge_attrs = {
            'capacity': capacity, 'resistance': resistance, 'flow': 0.0,
            'active': active, 'has_switch': has_switch, 'is_tie_switch': is_tie_switch,
            'switch_type': switch_type or ("tie" if is_tie_switch else ("sectionalizer" if has_switch else None)),
            'switch_status': switch_status,
            'switch': 'closed'
        }

        self.graph.add_edge(u, v, **edge_attrs)

        # ✅ MAKE TIE SWITCH BIDIRECTIONAL
        if is_tie_switch:
            self.graph.add_edge(v, u, **edge_attrs)

    def get_downstream_nodes(self, start):
        visited = set()
        stack = [start]

        while stack:
            node = stack.pop()

            for _, child, data in self.graph.out_edges(node, data=True):
                if not data.get("active", True):
                    continue

                if child not in visited:
                    visited.add(child)
                    stack.append(child)

        return visited

    def move_node(self, node_id: str, new_x: float, new_y: float):
        """Updates spatial coordinates and dynamically recalcs edge physics based on stretching."""
        if node_id not in self.nodes:
            raise ValueError(f"Unknown node {node_id}")
            
        node = self.nodes[node_id]
        node.x = new_x
        node.y = new_y
        
        # Update all physical connected line resistances 
        for neighbor in self.graph.neighbors(node_id):
            n_neighbor = self.nodes[neighbor]
            dx = node.x - n_neighbor.x
            dy = node.y - n_neighbor.y
            dist = math.hypot(dx, dy)
            # Apply dynamic resistance (0.0001 Ohms per pixel as base metric)
            self.graph[node_id][neighbor]["resistance"] = max(0.001, dist * 0.0001)

    # ------------------------------------------------------------------
    # Per-Timestep Simulation
    # ------------------------------------------------------------------

    def get_time_of_day(self) -> int:
        """Maps simulation timestep → hour of day (0-23) on a 24-step cycle."""
        return self.timestep % 24

    def step(self) -> dict:
        """Backward compatibility for existing tests."""
        self.update_generation()
        return self.update_power_flow()

    def update_generation(self):
        """Step 1: Calculate generation (solar + wind) and loads."""
        self.timestep += 1

        self._handle_reclosers()

        self._apply_time_curves()

        for node in self.nodes.values():
            node.weather = 0.8 if self.storm_active else 0.0
            if not node.failed and not node.isolated:
                node.step(dt=1.0, timestep=self.timestep)

        # 🔥 SIMPLE INERTIA MODEL (FREQUENCY RESPONSE)
        total_gen = sum(n.generation for n in self.nodes.values())
        total_load = sum(n.load for n in self.nodes.values())
        imbalance = total_gen - total_load

        self.avg_frequency = max(
            47.0,
            min(52.0, 50.0 + (imbalance * 0.1))
        )

        for node in self.nodes.values():
            node.frequency = self.avg_frequency

    def update_power_flow(self) -> dict:
        """Main physics update"""

        # ✅ RESET EVERYTHING FIRST (THIS IS YOUR MAIN BUG)
        for node_id, node in self.nodes.items():
            node.isolated = False
            node.received_power = 0.0

            if node.failed:
                node.voltage = 0.0
                # 🔥 FIX 7 — STOP “VICE VERSA FAILURE” (NO SWITCH ISOLATION BUG)
                for u, v in self.graph.edges():
                    if u == node_id or v == node_id:
                        self.graph[u][v]["active"] = False
            else:
                node.voltage = 1.0

        # ✅ RESET ALL EDGE FLOWS
        for u, v in self.graph.edges():
            self.graph[u][v]["flow"] = 0.0

        # Run actual flow
        self._simulate_energy_flow()

        return self.get_state()

        # 🔥 THERMAL TRIP MODEL (OVERLOAD PROTECTION)
        for u, v, d in self.graph.edges(data=True):
            if not d.get("active", True):
                continue

            flow = abs(d.get("flow", 0))
            cap = d.get("capacity", 1)

            # REAL WORLD BEHAVIOR: Line trips if flow > 120% of capacity
            if flow > cap * 1.2:
                d["active"] = False
                d["switch_status"] = "fault_locked"
                self.event_log.append(f"🔥 Thermal Trip: {u} → {v} overloaded ({flow:.2f}MW)")

        self._update_stress()

        return self.get_state()

    def _handle_reclosers(self):
        """
        Auto-reclose logic for feeder-head distribution reclosers.
        If a recloser tripped (active=False), but it hasn't reached lockout,
        wait `reclose_delay` and then attempt to close it again.
        """
        to_remove = []
        for (u, v), (target_time, attempt_count) in list(self.reclose_queue.items()):
            if self.timestep >= target_time:
                edge = self.graph[u][v]
                if attempt_count < edge.get("reclose_max", 3):
                    edge["active"] = True
                    edge["switch_status"] = "closed"
                    edge["reclose_attempts"] = attempt_count + 1
                    self.event_log.append(f"🔄 Recloser {u}─{v} auto-reclosing (attempt {attempt_count+1})...")
                else:
                    edge["locked_out"] = True
                    self.event_log.append(f"❌ Recloser {u}─{v} locked out permanently.")
                to_remove.append((u, v))
                
        for k in to_remove:
            self.reclose_queue.pop(k, None)

    def _apply_time_curves(self):
        """
        Scale generation according to 24h curves.
        - Houses:       solar curve (SOLAR_CURVE)
        - Wind farms:   wind curve  (WIND_CURVE)
        - Nuclear:      flat baseline (no curve — always available)
        - Solar farms:  SOLAR_CURVE at grid scale
        - Coal:         demand-following, no time curve applied here
        """
        hour         = self.get_time_of_day()
        solar_factor = SOLAR_CURVE[hour]
        wind_factor  = WIND_CURVE[hour]
        load_factor  = LOAD_CURVE[hour]
        storm_boost  = 0.25 if self.storm_active else 0.0

        for node in self.nodes.values():
            if node.failed or node.isolated:
                continue

            noise = random.uniform(0.95, 1.05)

            # House prosumer solar
            if node.node_type == "house":
                node.load       = round(node._base_load * (load_factor + storm_boost) * noise, 4)
                node.generation = round(node._base_generation * solar_factor * noise, 4)

            # Wind generator — follows WIND_CURVE
            elif node.node_type == "generator" and node.source_type == "wind":
                wind_noise = random.uniform(0.88, 1.12)   # winds are more variable
                storm_penalty = 0.7 if self.storm_active else 1.0
                node.generation = round(node._base_generation * wind_factor * wind_noise * storm_penalty, 4)

            # Nuclear generator — flat baseload, never curves down
            elif node.node_type == "generator" and node.source_type == "nuclear":
                node.generation = round(node._base_generation * random.uniform(0.98, 1.01), 4)

            # Solar farm — same curve as house solar at grid scale
            elif node.source_type == "solar":
                storm_cut = 0.5 if self.storm_active else 1.0
                node.generation = round(node._base_generation * solar_factor * noise * storm_cut, 4)

            # Coal/conventional generators drift slightly
            elif node.node_type == "generator" and node.source_type == "coal":
                drift = random.gauss(0, 0.015) - (0.03 if self.storm_active else 0)
                node.generation = round(max(0.0, min(12.0, node.generation + drift)), 4)


    def _isolate_fault_segments(self):
        """
        Segment-based isolation: if any node or edge fails within a segment,
        the entire segment is isolated by opening its boundary switches.

        A segment is explicitly defined as the set of nodes BETWEEN two switches
        on the network graph (not just the faulted node's immediate neighbors).
        This prevents wrong sections from being isolated.

        The identified fault segment is stored in self.last_fault_segment for:
          - Frontend overlay rendering
          - FLISR log transparency
          - Test assertion

        IMPORTANT: This method ONLY acts when there is an actual active fault.
        On a healthy grid it returns immediately to prevent stale isolation cascades.
        """
        # ── Early exit if nothing is failed or tripped ────────────────────
        has_failed_nodes  = any(n.failed for n in self.nodes.values())
        has_tripped_cable = any(
            not d.get("active") and not d.get("switch_type")
            for _, _, d in self.graph.edges(data=True)
        )
        if not has_failed_nodes and not has_tripped_cable:
            return   # healthy grid — nothing to isolate

        # FIX: Use directed graph for proper fault propagation (downstream only)
        # Build directed segment graph - maintain direction from source to downstream
        segment_graph = nx.DiGraph()

        # ── Segment graph: ONLY active non-switch edges in ONE direction ─────────
        # This carves the network into segments bounded by switches.
        # Use ONLY active edges to avoid including broken cables in propagation calc
        SWITCH_TYPES = ("sectionalizer", "recloser", "tie")
        for u, v, data in self.graph.edges(data=True):
            # CRITICAL: Only use ACTIVE edges - don't include broken cables
            if not data.get("active", True):
                continue
            if data.get("switch_type") not in SWITCH_TYPES and not data.get("is_tie_switch"):
                # Add edge in ONE direction only (parent → child)
                # descendants(u) will find all nodes that receive power FROM u
                segment_graph.add_edge(u, v)

        for nid in self.nodes:
            segment_graph.add_node(nid)

        faulted_segments: set[frozenset] = set()

        # ── Detect faults from failed nodes ───────────────────────────────────
        # FIX: Use descendants() to get only DOWNSTREAM nodes (direction of flow)
        # This ensures when LA0_2 fails, only LA0_2 + downstream are affected,
        # NOT LA0_1 (which is upstream)
        for nid, node in self.nodes.items():
            if node.failed:
                # Get only downstream nodes using get_downstream_nodes (strict direction)
                downstream = self.get_downstream_nodes(nid)
                # Include the failed node itself
                downstream.add(nid)
                comp = frozenset(downstream)
                faulted_segments.add(comp)

        # ── Detect faults from tripped (non-switch) cables ────────────────────
        for u, v, data in self.graph.edges(data=True):
            if not data.get("active") and not data.get("switch_type"):
                if u in segment_graph:
                    # Tripped cable affects downstream from the break point using directional flow
                    downstream = self.get_downstream_nodes(u)
                    downstream.add(u)
                    comp = frozenset(downstream)
                    faulted_segments.add(comp)

        # ── Open boundary switches for all faulted segments ───────────────────
        # Also build the explicit fault segment descriptor.
        all_start_switches: list[str] = []
        all_end_switches:   list[str] = []
        all_affected_nodes: list[str] = []

        for comp in faulted_segments:
            boundary_switches: list[tuple[str, str]] = []

            for u in comp:
                for nbr in self.graph.successors(u):
                    if nbr not in comp:
                        edge_data = self.graph[u][nbr]
                        sw_type   = edge_data.get("switch_type")
                        if sw_type in SWITCH_TYPES:
                            if edge_data.get("active", True):
                                edge_data["active"]        = False
                                edge_data["switch_status"] = "fault_locked"
                                boundary_switches.append((u, nbr))
                                self.event_log.append(
                                    f"🔒 {sw_type.capitalize()} {u}─{nbr} opened to isolate fault segment."
                                )

            # Record the explicit segment descriptor
            if boundary_switches:
                sw_nodes = [s for pair in boundary_switches for s in pair]
                all_start_switches.append(boundary_switches[0][0])
                all_end_switches.append(boundary_switches[-1][1])
                all_affected_nodes.extend([n for n in comp if n not in sw_nodes])

        # ── Persist last fault segment (used by get_state + frontend overlay) ─
        if faulted_segments:
            self.last_fault_segment = {
                "start_switch":   all_start_switches[0] if all_start_switches else None,
                "end_switch":     all_end_switches[-1]  if all_end_switches   else None,
                "affected_nodes": list(set(all_affected_nodes)),
            }
        else:
            # No new faults — keep existing segment until explicitly cleared
            pass

    def _simulate_energy_flow(self):
        from collections import deque

        # ✅ STEP 1 — HARD RESET (MANDATORY)
        for u, v in self.graph.edges():
            self.graph[u][v]["flow"] = 0.0

        for node in self.nodes.values():
            node.received_power = 0.0
            node.isolated = False
            if not node.failed:
                node.voltage = 1.0

        # ✅ STEP 2 — TRUE SOURCES (ONLY GENERATORS)
        sources = [
            nid for nid, n in self.nodes.items()
            if not n.failed and n.node_type.startswith("generator")
        ]

        queue = deque(sources)
        visited = set(sources)

        for s in sources:
            self.nodes[s].received_power = self.nodes[s].generation

        # ✅ STEP 3 — BFS FLOW (STRICT DOWNSTREAM)
        while queue:
            u = queue.popleft()
            u_node = self.nodes[u]

            children = []
            for v in self.graph.successors(u):
                edge = self.graph[u][v]
                if edge.get("active", True) and not self.nodes[v].failed:
                    children.append(v)

            if not children:
                continue

            power = u_node.received_power

            if power <= 0:
                continue

            split = power / len(children)

            for v in children:
                edge = self.graph[u][v]

                # 🚫 PREVENT BACKFLOW INTO GENERATORS
                if self.nodes[v].node_type.startswith("generator"):
                    continue

                edge["flow"] = round(split, 4)

                self.nodes[v].received_power += split
                self.nodes[v].voltage = max(0.95, u_node.voltage - 0.01)

                if v not in visited:
                    visited.add(v)
                    queue.append(v)

        # ✅ STEP 4 — MARK TRUE ISOLATION
        for nid, node in self.nodes.items():
            if nid not in visited and not node.failed:
                node.isolated = True
                node.voltage = 0.0

        # ✅ FIX 4 — GENERATORS NEVER ISOLATED
        for node in self.nodes.values():
            if node.node_type.startswith("generator"):
                node.isolated = False
                node.voltage = 1.0




    def _multi_agent_coordination(self):
        """
        Enhanced multi-agent coordination with bidirectional flow support.
        Nodes with excess offer energy to deficient neighbours with priority routing.
        """
        # First pass: identify surplus and deficit nodes
        surplus_nodes = []
        deficit_nodes = []
        
        for node_id, node in self.nodes.items():
            if node.failed or node.isolated:
                continue
            if node.excess_energy > 0.1:
                surplus_nodes.append((node_id, node.excess_energy))
            elif node.deficit > 0.1:
                deficit_nodes.append((node_id, node.deficit))

        # Second pass: match surplus to deficit with path optimization
        for surplus_id, surplus_amount in surplus_nodes:
            if surplus_amount <= 0:
                continue
                
            surplus_node = self.nodes[surplus_id]
            
            # Find best deficit neighbor using shortest path
            best_match = None
            best_distance = float('inf')
            
            for deficit_id, deficit_amount in deficit_nodes:
                if deficit_amount <= 0:
                    continue
                    
                try:
                    path = nx.shortest_path(self.graph, surplus_id, deficit_id, weight='resistance')
                    distance = len(path) - 1  # hop count
                    
                    if distance < best_distance:
                        best_distance = distance
                        best_match = (deficit_id, deficit_amount)
                except nx.NetworkXNoPath:
                    continue
            
            if best_match:
                deficit_id, deficit_amount = best_match
                deficit_node = self.nodes[deficit_id]
                
                # Transfer energy with efficiency loss
                transfer_amount = min(surplus_amount, deficit_amount, 0.2)
                efficiency = max(0.85, 1.0 - 0.02 * best_distance)  # distance-based efficiency
                received_amount = transfer_amount * efficiency
                
                surplus_node.excess_energy -= transfer_amount
                deficit_node.deficit = max(0.0, deficit_node.deficit - received_amount)
                deficit_node.generation = min(2.5, deficit_node.generation + received_amount * 0.95)
                
                # Update flow on the path
                path = nx.shortest_path(self.graph, surplus_id, deficit_id, weight='resistance')
                for i in range(len(path) - 1):
                    u, v = path[i], path[i + 1]
                    if self.graph.has_edge(u, v):
                        self.graph[u][v]['flow'] = self.graph[u][v].get('flow', 0) + transfer_amount

    def _update_stress(self):
        """Enhanced stress computation with voltage regulation and reactive power awareness."""
        for node in self.nodes.values():
            if node.failed or node.isolated:
                continue
            
            # Voltage deviation stress (only penalize drops beyond healthy 5% margin)
            v_dev = max(0.0, abs(node.voltage - 1.0) - 0.05) / 0.15
            
            # Frequency deviation stress (allow small 0.2 Hz drift)
            f_dev = max(0.0, abs(node.frequency - 50.0) - 0.2) / 1.3
            
            # Reactive power stress (inferred from voltage-frequency coupling)
            reactive_stress = max(0.0, (v_dev + f_dev) - 0.2) / 0.8
            
            # Load factor stress (high utilization) - only for generators
            # Substations and transformers do not generate, so comparing load/generation is invalid.
            if node.node_type == "generator":
                load_factor = node.load / max(node.generation + 0.1, 1.0)
                utilization_stress = max(0.0, load_factor - 0.8) / 0.2
            else:
                utilization_stress = 0.0  # Houses/Transformers/Poles
            
            # Combine stresses with weights
            node.stress_level = float(min(1.0, 
                0.40 * v_dev + 
                0.30 * f_dev + 
                0.15 * reactive_stress + 
                0.15 * utilization_stress
            ))
            
            # Add voltage regulation logic
            if node.node_type in ("transformer", "substation"):
                # Transformers can provide voltage support
                if v_dev > 0.1:
                    # Boost voltage by adjusting tap (simplified)
                    node.voltage = min(1.08, node.voltage + 0.02)
                elif v_dev < 0.05 and node.voltage > 0.98:
                    # Reduce voltage to save energy
                    node.voltage = max(0.95, node.voltage - 0.01)

    # ------------------------------------------------------------------
    # User Control Interactions
    # ------------------------------------------------------------------
    
    def add_user_node(self, node_type: str, x: float = 0.0, y: float = 0.0) -> dict:
        """User API to add a node at an explicit CAD coordinate."""
        if len(self.nodes) >= 500:
            raise ValueError("Max nodes (500) reached.")
            
        if node_type == "generator":
            gens = sum(1 for n in self.nodes.values() if n.node_type == "generator")
            if gens >= 20:
                raise ValueError("Max generators (20) reached.")
                
        prefix = node_type[0].upper()
        existing = [int(nid[1:]) for nid in self.nodes.keys() if nid.startswith(prefix) and nid[len(prefix):].isdigit()]
        next_id = f"{prefix}{max(existing) + 1 if existing else 0}"
        
        node = GridNode(next_id, node_type=node_type, x=x, y=y)
        if node_type == "generator":
            node.generation = random.uniform(5.0, 8.0)
            node.load = 0.1
        elif node_type == "substation":
            node.generation = 0.0
            node.load = random.uniform(1.0, 2.0)
        elif node_type == "transformer":
            node.generation = 0.0
            node.load = random.uniform(0.5, 1.5)
        elif node_type == "solar":
            node.generation = random.uniform(1.0, 3.0)
            node.load = 0.0
            node.role = "generation"
            node.source_type = "solar"
        elif node_type == "wind":
            node.generation = random.uniform(1.5, 4.0)
            node.load = 0.0
            node.role = "generation"
            node.source_type = "wind"
        elif node_type == "battery":
            node.generation = 0.0
            node.load = 0.0
            node.role = "storage"
            node.source_type = "battery"
            node.battery_capacity = random.uniform(2.0, 6.0)
            node.battery_level = 0.9
        elif node_type == "supercap":
            node.generation = 0.0
            node.load = 0.0
            node.role = "support"
            node.supercap_capacity = 2.0
            node.supercap_level = 1.0
        else:
            node.generation = random.uniform(0.0, 0.2)
            node.load = random.uniform(0.2, 0.8)
            
        node._base_generation = node.generation
        node._base_load = node.load
        
        if node.node_type == "house":
            if node._base_generation >= 0.25:
                node.role = "generation"
            elif node._base_generation >= 0.15:
                node.role = "storage"
            else:
                node.role = "load"
            
        self.nodes[next_id] = node
        self.graph.add_node(next_id)
        return {"id": next_id, "node_type": node_type, "x": x, "y": y}
        
    def delete_node(self, node_id: str) -> str:
        """User API to delete a node and all its incident edges."""
        if node_id not in self.nodes:
            raise ValueError(f"Node {node_id} not found.")
        self.graph.remove_node(node_id)
        del self.nodes[node_id]
        return f"Deleted node {node_id}"
        
    def add_user_edge(self, u: str, v: str) -> str:
        """User API to add an edge with realistic physics bounds."""
        if u == v:
            raise ValueError("Cannot connect node to itself.")
        if u not in self.nodes or v not in self.nodes:
            raise ValueError("Node not found.")
        if self.graph.has_edge(u, v):
            raise ValueError("Edge already exists.")
            
        # Defer physics computations (distance and dynamic resistance) 
        # and constraint checks directly to the core `_add_edge` builder engine.
        self._add_edge(u, v)
        return f"Connected {u}-{v}"
        
    def cut_user_edge(self, u: str, v: str) -> str:
        """User API to cut a wire entirely."""
        if not self.graph.has_edge(u, v):
            raise ValueError("Edge not found.")
        
        self.graph[u][v]["active"] = False
        msg = f"✂️ Line {u}-{v} was physically cut."
        self.event_log.append(msg)
        
        # Validation metric: Warn on disconnect
        try:
            active_edges = [(nu, nv) for nu, nv, data in self.graph.edges(data=True) if data.get("active", True)]
            test_g = self.graph.edge_subgraph(active_edges)
            if len(test_g.nodes) < len(self.nodes) or not nx.is_connected(test_g):
                self.event_log.append("⚠️ Grid Collapse Risk: Network partitioning detected!")
        except Exception:
            pass # Failsafe
            
        return msg

    # ------------------------------------------------------------------
    # Failure Injection & Self-Healing
    # ------------------------------------------------------------------

    def _balance_transformers(self) -> None:
        """
        Activates normally-open tie-switches to mechanically balance load 
        across the distribution layer. Called by SCADA Control Center.
        """
        balanced = 0
        for u, v, data in self.graph.edges(data=True):
            nu, nv = self.nodes[u], self.nodes[v]
            if nu.node_type == "switch" or nv.node_type == "switch":
                if not data.get("active", True) and not nu.failed and not nv.failed:
                    data["active"] = True
                    balanced += 1
        
        if balanced > 0:
            self.event_log.append(f"🔄 Closed {balanced} tie-switches for load balancing.")

    def inject_failure(self, node_id: str) -> str:
        if node_id not in self.nodes:
            return f"Unknown node: {node_id}"

        self.nodes[node_id].failed = True

        # disable ALL connected edges
        for nbr in list(self.graph.successors(node_id)):
            self.graph[node_id][nbr]["active"] = False
            self.graph[node_id][nbr]["switch_status"] = "fault_locked"

        for nbr in list(self.graph.predecessors(node_id)):
            self.graph[nbr][node_id]["active"] = False
            self.graph[nbr][node_id]["switch_status"] = "fault_locked"

        # Mark downstream as isolated
        downstream = nx.descendants(self.graph, node_id)
        for n in downstream:
            self.nodes[n].isolated = True

        msg = f"⚠️ Node {node_id} FAILED — segment isolation + FLISR triggered."
        self.event_log.append(msg)
        self.event_log = self.event_log[-20:]  # type: ignore
        return msg

    def random_failure(self) -> str:
        """Inject a random failure on a pole node for demo purposes."""
        poles = [nid for nid, n in self.nodes.items() if n.node_type == "pole" and not n.failed]
        if not poles:
            return "No healthy poles available for random failure."
        target = random.choice(poles)
        return self.inject_failure(target)

    def _reroute(self, failed_node_id: str) -> str:
        """
        Multi-Path Deterministic FLISR Rerouting via AI Switch Optimization.

        Algorithm (3 steps):
          Step 1 - Enumerate ALL candidate paths from each live isolated node to any substation.
                   The search graph only allows closed active cables OR valid open switches.
          Step 2 - Filter paths by: (a) physical validity, (b) capacity, (c) voltage drop.
          Step 3 - Score paths comparing normalized metrics:
                   score = (0.35 * R_norm) + (0.25 * V_norm) + (0.25 * S_norm) - (0.15 * priority_bonus)
                   Minimum score wins.
        """
        # ── Build active sub-graph including open switches but NOT fault-locked ones ────
        search_graph = nx.Graph()
        for u, v, data in self.graph.edges(data=True):
            nu, nv = self.nodes[u], self.nodes[v]
            if nu.failed or nv.failed:
                continue
            
            # Do NOT bypass isolating fault-locked switches
            if data.get("switch_status") == "fault_locked":
                continue
                
            cap  = data.get("capacity", 1.0)
            flow = abs(data.get("flow", 0.0))
            res  = data.get("resistance", 0.05)
            
            if data.get("active", True):
                search_graph.add_edge(u, v, capacity=cap, flow=flow, resistance=res, is_switch=False)
            elif data.get("switch_type"):
                search_graph.add_edge(u, v, capacity=cap, flow=0.0, resistance=res, is_switch=True)

        substations = [
            nid for nid, n in self.nodes.items()
            if n.node_type == "substation" and not n.failed and nid in search_graph
        ]
        if not substations:
            return "❌ All substations failed — no rerouting possible."

        def capacity_ok(path: list) -> bool:
            for i in range(len(path) - 1):
                u, v = path[i], path[i + 1]
                if not search_graph.has_edge(u, v): return False
                d = search_graph[u][v]
                if d["flow"] >= 0.9 * d["capacity"]: return False
            return True

        def voltage_ok(path: list) -> bool:
            v = self.nodes[path[0]].voltage if path else 1.05
            for i in range(len(path) - 1):
                u, vn = path[i], path[i + 1]
                d = search_graph[u][vn]
                v = v - (d.get("flow", 0.0) * d.get("resistance", 0.05)) / max(v, 0.01)
            return v >= 0.90
            
        def valid_path(path: list) -> bool:
            """Restrict search: ONLY allow un-active traversing if it is explicitly a switch edge."""
            for i in range(len(path) - 1):
                u, v = path[i], path[i + 1]
                edge_data = self.graph[u][v]
                if not edge_data.get("active", True) and not edge_data.get("switch_type"):
                    return False
            return True

        def total_resistance(path: list) -> float:
            return sum(search_graph[path[i]][path[i+1]].get("resistance", 0.05) for i in range(len(path)-1))
            
        def switch_count_fn(path: list) -> int:
            return sum(1 for i in range(len(path)-1) if search_graph[path[i]][path[i+1]].get("is_switch", False))
            
        def voltage_drop(path: list) -> float:
            return sum(search_graph[path[i]][path[i+1]].get("resistance", 0.05) * 1.0 for i in range(len(path)-1))
            
        def priority_bonus_fn(path: list) -> float:
            return sum(1.0 / max(1, self.nodes[n].priority) for n in path)

        disconnected: list[str] = []
        rerouted_paths: list[list[str]] = []
        switches_flipped_this_cycle = set()

        for nid, node in self.nodes.items():
            if nid == failed_node_id or node.failed:
                continue
            if nid not in search_graph:
                disconnected.append(nid)
                continue
            
            # Optimization: only attempt to route nodes that are currently isolated/disconnected
            if not node.isolated and node.voltage > 0.01:
                # Node seems to have power via existing active graph, skip rerouting
                continue

            all_valid_candidates = []
            for sub in substations:
                if nid == sub:
                    all_valid_candidates.append([nid])
                    continue
                try:
                    candidates = list(nx.all_simple_paths(search_graph, nid, sub, cutoff=6))
                except (nx.NetworkXError, nx.NodeNotFound):
                    continue

                valid = [p for p in candidates if capacity_ok(p) and voltage_ok(p) and valid_path(p)]
                all_valid_candidates.extend(valid)

            if not all_valid_candidates:
                disconnected.append(nid)
                continue

            # ── Normalised Grid Metrics & Scoring ──
            max_R = max((total_resistance(p) for p in all_valid_candidates), default=0.001) or 0.001
            max_V = max((voltage_drop(p) for p in all_valid_candidates), default=0.001) or 0.001
            max_S = max((switch_count_fn(p) for p in all_valid_candidates), default=1) or 1
            
            best_path = None
            best_score = float("inf")

            for p in all_valid_candidates:
                if len(p) == 1:
                    score = -100.0
                else:
                    r_norm = total_resistance(p) / max_R
                    v_norm = voltage_drop(p) / max_V
                    s_norm = switch_count_fn(p) / max_S
                    priority_b = priority_bonus_fn(p)
                    
                    score = (0.35 * r_norm) + (0.25 * v_norm) + (0.25 * s_norm) - (0.15 * priority_b)

                if score < best_score:
                    best_score = score
                    best_path = p

            if best_path and len(best_path) > 1:
                # Perform the physical auto-switching sequence!
                switched_any = False
                for i in range(len(best_path) - 1):
                    u, v = best_path[i], best_path[i + 1]
                    edge = self.graph[u][v]
                    if not edge.get("active", True) and (u, v) not in switches_flipped_this_cycle:
                        edge["active"] = True
                        edge["switch_status"] = "closed"
                        switches_flipped_this_cycle.add((u, v))
                        switches_flipped_this_cycle.add((v, u))
                        switched_any = True
                        self.event_log.append(f"🔄 FLISR AI closed {edge.get('switch_type', 'switch')} {u}─{v} (Priority Score: {best_score:.3f}).")
                
                if switched_any:
                    rerouted_paths.append(best_path)
            
            # The node is technically connected now
            node.isolated = False

        if disconnected:
            for nid in disconnected:
                self.nodes[nid].isolated = True

        self._last_reroute_paths = rerouted_paths[:5]

        if not disconnected:
            return f"✅ FLISR AI Optimization Complete: Evaluated via normalized paths bounds."
        return f"⚡ FLISR: {len(disconnected)} node(s) remain isolated."

    def get_optimal_path(self, source: str, target: str) -> dict:
        """
        Public API: find the optimal (minimum-loss, capacity-constrained)
        path between any two live nodes using Dijkstra.

        Returns path list, total weight, and hop count.
        """
        if source not in self.nodes or target not in self.nodes:
            return {"error": "Node not found", "path": []}

        G = nx.Graph()
        for u, v, data in self.graph.edges(data=True):
            if not data.get("active", True):
                continue
            nu, nv = self.nodes[u], self.nodes[v]
            if nu.failed or nv.failed or nu.isolated or nv.isolated:
                continue
            cap    = data.get("capacity", 1.0)
            flow   = abs(data.get("flow", 0.0))
            load_pct = flow / max(cap, 0.01)
            # Penalise congested lines: weight increases with utilisation
            weight = (1.0 / max(cap, 0.01)) * (1.0 + load_pct)
            G.add_edge(u, v, weight=weight, capacity=cap, load_pct=round(load_pct, 3))

        try:
            path = nx.dijkstra_path(G, source, target, weight="weight")
            cost = nx.dijkstra_path_length(G, source, target, weight="weight")
            edges = []
            for i in range(len(path) - 1):
                e = G[path[i]][path[i+1]]
                edges.append({
                    "from": path[i], "to": path[i+1],
                    "capacity": e["capacity"], "load_pct": e["load_pct"],
                })
            return {
                "path":       path,
                "hops":       len(path) - 1,
                "total_cost": round(cost, 4),
                "edges":      edges,
                "algorithm":  "Constrained Dijkstra (capacity-weighted)",
            }
        except (nx.NetworkXNoPath, nx.NodeNotFound) as e:
            return {"error": str(e), "path": []}

    def restore_node(self, node_id: str) -> str:
        """
        Recover a failed/isolated node AND its downstream nodes.

        Switch State Machine:
          fault_locked -> closed   (sectionalizer / recloser that isolated the fault)
          fault_locked -> open     (tie switch - stays normally-open after repair)
          open / closed -> unchanged (switches not involved in this fault stay put)
        """
        if node_id not in self.nodes:
            return f"Unknown node: {node_id}"

        self.nodes[node_id].recover()
        self.nodes[node_id].isolated = False

        # Restore all downstream nodes (from the failed segment)
        for nid in self.nodes:
            if self.nodes[nid].isolated:
                self.nodes[nid].isolated = False

        # Reactivate ALL switches EXCEPT tie switches
        for u, v, data in self.graph.edges(data=True):
            if data.get("is_tie_switch"):
                data["active"] = False
            else:
                data["active"] = True

        # Check if grid has any remaining faults
        if not any(n.failed for n in self.nodes.values()):
            # If healthy, proactively reset all protection relays / boundary switches
            self.reset_all_switches()
            msg = f"✅ Node {node_id} restored to service. Grid healthy, protection relays reset."
        else:
            msg = f"🔧 Node {node_id} restored to service. Other active faults remain."

        self.event_log.append(msg)
        return msg

    def reset_all_switches(self) -> str:
        """
        Reset ALL fault-locked switches to their normal idle state.
        Callable after a multi-node repair scenario.
        """
        count = 0
        for u, v, data in self.graph.edges(data=True):
            if data.get("switch_status") == "fault_locked":
                sw_type = data.get("switch_type")
                if sw_type == "tie":
                    data["active"]        = False
                    data["switch_status"] = "open"
                else:
                    data["active"]        = True
                    data["switch_status"] = "closed"
                count += 1
        self.last_fault_segment = {}
        msg = f"🔧 {count} fault-locked switch(es) reset to normal idle state."
        self.event_log.append(msg)
        return msg

    # ------------------------------------------------------------------
    # Weather / Storm Event
    # ------------------------------------------------------------------

    def random_failure(self):
        """Inject a random failure on a random healthy pole node."""
        import random
        # Only target healthy poles
        pole_nodes = [nid for nid, n in self.nodes.items() if n.node_type == "pole" and not n.failed]
        if not pole_nodes:
            return "❌ No healthy poles available for random fault."
        
        target = random.choice(pole_nodes)
        return self.inject_failure(target)

    def trigger_storm(self) -> str:
        """Triggers a storm and injects a few random poles."""
        self.storm_active = True
        self.event_log.append("🌩️ STORM ACTIVE - Loads increased, solar dropped, random pole failures expected.")
        import random
        for _ in range(random.randint(1, 3)):
            self.random_failure()
        return "Storm triggered, 1-3 poles isolated."

    def clear_storm(self):
        """Deactivate storm."""
        self.storm_active = False
        msg = "☀️ Storm cleared."
        self.event_log.append(msg)
        return msg

    # ------------------------------------------------------------------
    # Generation Control
    # ------------------------------------------------------------------

    def increase_generation(self, amount: float = 0.3):
        """Boost generation on generators, and store excess."""
        generators = [n for n in self.nodes.values() if n.node_type in ["generator", "solar", "wind", "substation"] and not n.failed]
        for gen in generators:
            gen.generation *= 1.5
            
        total_gen = sum(n.generation for n in self.nodes.values() if not n.failed and not n.isolated)
        total_load = sum(n.load for n in self.nodes.values() if not n.failed and not n.isolated)
        
        if total_gen > total_load:
            excess = total_gen - total_load
            batteries = [n for n in self.nodes.values() if n.node_type == "battery"]
            for bat in batteries:
                store = min(excess, 1.5)
                bat.battery_level = min(1.0, bat.battery_level + (store / bat.battery_capacity))
                excess -= store

        return f"⚡ Generation increased by x1.5 MW on {len(generators)} generation nodes."

    def increase_demand(self, amount: float = 0.2):
        """Simulate demand surge across house, hospital, and industry nodes."""
        count = 0
        for nid, node in self.nodes.items():
            if node.node_type in ["house", "hospital", "industry"] and not node.failed:
                node.load = min(4.0, node.load * 1.3)
                count += 1
        return f"📈 Demand increased (x1.3) across {count} load nodes."

    def heal_all(self):
        """Restore all failed/isolated nodes to healthy state."""
        healed = 0
        for node in self.nodes.values():
            if node.failed or node.isolated:
                node.failed = False
                node.isolated = False
                healed += 1
        return f"🔧 Healed {healed} nodes."

    def reset(self):
        """Reset entire grid to initial state."""
        self.graph.clear()
        self.nodes.clear()
        self.timestep = 0
        self.storm_active = False
        self.total_energy_loss = 0.0
        self.event_log = []

        # Re-initialize topology with clean defaults (tie-switches open, loads nominal)
        self._build_grid()

        for _ in range(3):
            self.update_generation()
            self.update_power_flow()

        return "Grid reset successfully"

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def get_active_paths(self) -> list:
        """
        Get all active edges with flow for continuous path animation.
        Returns simplified edge list with from/to/flow for frontend path building.
        """
        paths = []
        for u, v, data in self.graph.edges(data=True):
            if data.get("active", True) and abs(data.get("flow", 0)) > 0.001:
                paths.append({
                    "from": u,
                    "to": v,
                    "flow": data["flow"],
                    "source_type": self.nodes[u].source_type if u in self.nodes else "none",
                    "active": data.get("active", True)
                })
        return paths

    def get_state(self) -> dict:
        """
        Return a full serialisable snapshot of the grid for the frontend.
        Includes last_fault_segment for the UI overlay rendering.
        """
        edges = []
        for u, v, data in self.graph.edges(data=True):
            edges.append({
                "source":        u,
                "target":        v,
                "capacity":      data.get("capacity", 1.0),
                "flow":          data.get("flow", 0.0),
                "charging":      data.get("charging", False),   # ← storage charge animation
                "source_type":   data.get("source_type", None),
                "active":        data.get("active", True),
                "is_tie_switch": data.get("is_tie_switch", False),
                "has_switch":    data.get("has_switch", False),
                "switch_type":   data.get("switch_type", None),
                "switch_status": data.get("switch_status",
                                          "closed" if data.get("active", True) else "open"),
                "resistance":    data.get("resistance", 0.0),
            })

        # System-level aggregates
        active_nodes  = [n for n in self.nodes.values() if not n.failed and not n.isolated]
        total_gen     = sum(n.generation for n in active_nodes)
        total_load    = sum(n.load for n in active_nodes)
        avg_voltage   = sum(n.voltage  for n in active_nodes) / max(len(active_nodes), 1)
        avg_freq      = sum(n.frequency for n in active_nodes) / max(len(active_nodes), 1)
        health_score  = max(0.0, 1.0 - sum(n.stress_level for n in self.nodes.values()) / len(self.nodes))

        # Count failed vs isolated (not both)
        failed_count = sum(1 for n in self.nodes.values() if n.failed)
        isolated_count = sum(1 for n in self.nodes.values() if n.isolated and not n.failed)

        # Edge-based flow for animation (already edge-level, not route-based)
        active_paths = self.get_active_paths()

        return {
            "timestep":           self.timestep,
            "storm_active":       self.storm_active,
            "nodes":              {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges":              edges,
            "active_paths":       active_paths,  # Edge-based flow for continuous particle animation
            "last_fault_segment": self.last_fault_segment,   # UI overlay data
            "system": {
                "total_generation": round(float(total_gen),   4),   # type: ignore
                "total_load":       round(float(total_load),  4),   # type: ignore
                "balance":          round(float(total_gen - total_load), 4),  # type: ignore
                "avg_voltage":      round(float(avg_voltage), 4),   # type: ignore
                "avg_frequency":    round(float(avg_freq),    4),   # type: ignore
                "health_score":     round(float(health_score),4),   # type: ignore
                "total_energy_loss":round(float(self.total_energy_loss), 4),  # type: ignore
                "failed_count":     failed_count,
                "isolated_count":   isolated_count,
            },
            "recent_events": self.event_log[-5:],  # type: ignore
        }

    def get_lstm_input(self, node_id: str) -> list:
        """Return the last 10 timesteps of [load, generation, weather] for LSTM."""
        node = self.nodes.get(node_id)
        if not node:
            return [[0.5, 0.5, 0.0]] * 10
        result = []
        for l, g in zip(node.load_history, node.gen_history):
            result.append([l, g, node.weather])
        return result

    def get_rl_state(self) -> list:
        """
        Flatten core primary nodes (G0-G2, S0-S5) into a fixed vector for the DQN agent,
        plus global context features for better situational awareness.
        This completely decouples the AI dimension scale from the dynamic house graph!
        Format per node: [voltage, frequency, load, generation, stress]
        Globals: [total_load, total_generation, balance, avg_voltage, avg_freq, failed_count, isolated_count]
        """
        state_vec = []
        target_nodes = ["G0", "G1", "G2", "S0", "S1", "S2", "S3", "S4", "S5"]
        for nid in target_nodes:
            if nid in self.nodes:
                n = self.nodes[nid]
                state_vec.extend([
                    n.voltage,
                    n.frequency / 50.0,   # normalise around 1.0
                    n.load,
                    n.generation,
                    n.stress_level,
                ])
            else:
                state_vec.extend([0.0] * 5)
                
        # Global features
        active_nodes = [n for n in self.nodes.values() if not n.failed and not n.isolated]
        total_gen = sum(n.generation for n in active_nodes)
        total_load = sum(n.load for n in active_nodes)
        balance = total_gen - total_load
        avg_voltage = sum(n.voltage for n in active_nodes) / max(len(active_nodes), 1)
        avg_freq = sum(n.frequency for n in active_nodes) / max(len(active_nodes), 1)
        failed_count = sum(1 for n in self.nodes.values() if n.failed)
        isolated_count = sum(1 for n in self.nodes.values() if n.isolated)
        
        state_vec.extend([
            total_load,
            total_gen,
            balance,
            avg_voltage,
            avg_freq / 50.0,
            failed_count,
            isolated_count
        ])
        
        return state_vec
        
    def predictive_islanding(self, failed_nodes: list) -> dict:
        """
        Advanced resilience: Form microgrids around generators with sufficient local generation.
        Returns island configuration for major outage scenarios.
        """
        # Find healthy generators
        healthy_generators = [
            nid for nid, node in self.nodes.items() 
            if node.node_type == "generator" and not node.failed
        ]
        
        islands = []
        
        for gen_id in healthy_generators:
            # Find all nodes that can be powered by this generator
            reachable = set()
            queue = deque([gen_id])
            visited = set([gen_id])
            
            while queue:
                current = queue.popleft()
                for neighbor in self.graph.successors(current):
                    if neighbor not in visited:
                        edge_data = self.graph[current][neighbor]
                        if edge_data.get("active", True):
                            neighbor_node = self.nodes[neighbor]
                            if not neighbor_node.failed and neighbor not in failed_nodes:
                                visited.add(neighbor)
                                queue.append(neighbor)
                                

            
            # Calculate island capacity
            island_nodes = [nid for nid in visited if nid != gen_id]
            total_load = sum(self.nodes[nid].load for nid in island_nodes)
            total_gen = self.nodes[gen_id].generation + sum(
                self.nodes[nid].generation for nid in island_nodes
            )
            
            # Check if island is viable (generation >= 80% of load)
            if total_gen >= 0.8 * total_load and len(island_nodes) > 0:
                islands.append({
                    "generator": gen_id,
                    "nodes": island_nodes,
                    "total_load": round(total_load, 2),
                    "total_generation": round(total_gen, 2),
                    "balance_ratio": round(total_gen / max(total_load, 0.1), 2),
                    "size": len(island_nodes)
                })
        
        return {
            "islands": islands,
            "total_islands": len(islands),
            "coverage": sum(len(i["nodes"]) for i in islands),
            "recommendation": "Form islands" if islands else "Total blackout - no viable islands"
        }

    def suggest_tie_lines(self) -> list:
        """
        AI Suggestion Engine (Graph Theory).
        Detects structural vulnerabilities (articulation points) and proposes new minimal-length ties.

        IMPROVED: Uses articulation points for LOCAL vulnerability detection (exact single points of failure)
        and suggests connections to nearest alternate feeders for redundancy.
        """
        import networkx as nx
        import random
        suggestions = []
        try:
            # Active core topology (exclude disconnected graphs and minor branches for macro analysis)
            core_nodes = [n for n, node in self.nodes.items() if not node.failed and node.node_type not in ["house", "service"]]
            sub_g = self.graph.subgraph(core_nodes)

            # Find generators and their feeder assignments for identifying alternate sources
            generators = [n for n, node in self.nodes.items() if node.node_type == "generator" and not node.failed]

            # Exact single points of failure according to graph connectivity
            weak_nodes = list(nx.articulation_points(sub_g))

            sampled_weak = random.sample(weak_nodes, min(4, len(weak_nodes)))

            for ap in sampled_weak:
                node_ap = self.nodes[ap]
                candidates = []

                # Find which feeder this articulation point currently belongs to
                ap_feeder = None
                for gen in generators:
                    if nx.has_path(sub_g, ap, gen):
                        ap_feeder = gen
                        break

                for n, node in self.nodes.items():
                    if n == ap or node.node_type in ["house", "service"] or node.failed:
                        continue
                    if not self.graph.has_edge(ap, n):
                        # Ensure we recommend connecting to a DIFFERENT feeder or far branch
                        dist = ((node_ap.x - node.x)**2 + (node_ap.y - node.y)**2)**0.5
                        if dist > 50: # Arbitrary minimum distance so it's not the same pole group
                            # Check if this candidate connects to a DIFFERENT generator (alternate feeder)
                            alt_feeder = False
                            for gen in generators:
                                if gen != ap_feeder and nx.has_path(sub_g, n, gen):
                                    alt_feeder = True
                                    break

                            # Bonus score for alternate feeders
                            score = dist - (100 if alt_feeder else 0)
                            candidates.append((n, score, dist, alt_feeder))

                if candidates:
                    # Sort by score (lower is better) - prioritizes alternate feeders then proximity
                    candidates.sort(key=lambda x: x[1])
                    best = candidates[0]
                    target, _, distance, is_alt = best

                    reason = f"Articulation point '{ap}' is a single point of failure. "
                    if is_alt:
                        reason += f"Connect to {target} on alternate feeder for true redundancy."
                    else:
                        reason += f"Connect to {target} ({distance:.0f}m) to reduce isolation risk."

                    suggestions.append({
                        "source": ap,
                        "target": target,
                        "distance_m": round(distance, 1),
                        "is_alternate_feeder": is_alt,
                        "reason": reason
                    })
        except Exception as e:
            pass
        return suggestions[:4]
        
    def suggest_best_parent(self, x: float, y: float) -> dict:
        """Recommend the optimal grid connection point for a new load."""
        candidates = []
        for n, node in self.nodes.items():
            if node.node_type in ["transformer", "substation", "service", "switch"]:
                dist = ((node.x - x)**2 + (node.y - y)**2)**0.5
                if dist < 500:
                    score = dist + (node.load * 150) # Penalize heavily loaded parents
                    candidates.append((n, score, dist))
                    
        if candidates:
            candidates.sort(key=lambda x: x[1])
            best = candidates[0]
            return {
                "parent": best[0], 
                "distance": round(best[2], 1), 
                "reason": "Optimal balance of proximity and available capacity."
            }
        return {}
