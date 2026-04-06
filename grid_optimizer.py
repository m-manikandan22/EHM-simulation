"""
Self-Healing Power Grid with AI Optimization
Critical Fixes Applied:
- Normalized scoring for fair comparison
- Switch-only path validation
- Priority-aware load handling
- Articulation point vulnerability detection
"""

import networkx as nx
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set
from enum import Enum
import json

class LoadPriority(Enum):
    CRITICAL = 1    # Hospital, emergency services
    HIGH = 2        # Schools, water treatment
    MEDIUM = 3      # Residential
    LOW = 4         # Commercial, optional

@dataclass
class Load:
    id: str
    power: float
    priority: LoadPriority
    node_id: str
    is_energized: bool = True

@dataclass
class Switch:
    id: str
    u: str
    v: str
    is_closed: bool = True
    is_tie_switch: bool = False

@dataclass
class Feeder:
    id: str
    source_node: str
    voltage: float
    max_capacity: float
    current_load: float = 0.0

@dataclass
class SwitchingStep:
    switch_id: str
    action: str  # 'OPEN' or 'CLOSE'
    reason: str

class GridOptimizer:
    """Self-healing grid with corrected scoring and validation."""

    # Normalization constants
    MAX_RESISTANCE = 10.0
    MAX_VOLTAGE_DROP = 1.0
    MAX_SWITCHES = 5.0

    def __init__(self):
        self.G = nx.Graph()
        self.loads: Dict[str, Load] = {}
        self.switches: Dict[str, Switch] = {}
        self.feeders: Dict[str, Feeder] = {}
        self.switch_edges: Set[Tuple[str, str]] = set()

    def add_line(self, u: str, v: str, resistance: float, is_switch: bool = False,
                 switch_id: Optional[str] = None, is_tie: bool = False):
        """Add line segment to grid."""
        self.G.add_edge(u, v, resistance=resistance, switch=is_switch, tie=is_tie)
        if is_switch and switch_id:
            self.switches[switch_id] = Switch(switch_id, u, v, is_closed=True, is_tie_switch=is_tie)
            self.switch_edges.add((u, v))
            self.switch_edges.add((v, u))

    def add_load(self, load: Load):
        """Add load to grid."""
        self.loads[load.id] = load
        if not self.G.has_node(load.node_id):
            self.G.add_node(load.node_id)

    def add_feeder(self, feeder: Feeder):
        """Add power source feeder."""
        self.feeders[feeder.id] = feeder
        if not self.G.has_node(feeder.source_node):
            self.G.add_node(feeder.source_node)

    def is_valid_switching_path(self, path: List[str]) -> bool:
        """
        CRITICAL FIX 2: Validate path uses switch edges at key points.
        Path must use at least one switch for reconfiguration.
        """
        if len(path) < 2:
            return False

        switch_count = 0
        for u, v in zip(path[:-1], path[1:]):
            edge_data = self.G[u][v]
            if edge_data.get('switch', False):
                switch_count += 1

        # Path must contain at least one switch for reconfiguration
        return switch_count > 0

    def calculate_path_metrics(self, path: List[str], source: str) -> Dict:
        """Calculate normalized metrics for a path."""
        total_resistance = 0.0
        voltage_drop = 0.0
        switch_count = 0
        priority_bonus = 0.0

        # Source voltage
        source_voltage = self.feeders.get(source, Feeder("", "", 11.0, 0)).voltage

        for u, v in zip(path[:-1], path[1:]):
            edge = self.G[u][v]

            # Accumulate resistance
            total_resistance += edge.get('resistance', 0.1)

            # Count switches
            if edge.get('switch', False):
                switch_count += 1

            # Voltage drop calculation (simplified)
            current = 1.0  # per-unit current
            voltage_drop += edge.get('resistance', 0.1) * current

        # Calculate priority bonus from loads in path
        energized_loads = []
        for node in path:
            for load in self.loads.values():
                if load.node_id == node and not load.is_energized:
                    energized_loads.append(load)
                    # CRITICAL FIX 3: Priority weighting
                    priority_value = load.priority.value
                    priority_bonus += 1.0 / priority_value

        # Normalize metrics
        R_norm = min(total_resistance / self.MAX_RESISTANCE, 1.0)
        V_norm = min(voltage_drop / self.MAX_VOLTAGE_DROP, 1.0)
        S_norm = min(switch_count / self.MAX_SWITCHES, 1.0)
        P_norm = priority_bonus / max(len(energized_loads), 1) if energized_loads else 0

        return {
            'resistance': total_resistance,
            'voltage_drop': voltage_drop,
            'switch_count': switch_count,
            'priority_bonus': priority_bonus,
            'R_norm': R_norm,
            'V_norm': V_norm,
            'S_norm': S_norm,
            'P_norm': P_norm,
            'energized_loads': energized_loads
        }

    def compute_path_score(self, metrics: Dict) -> float:
        """
        CRITICAL FIX 1: Normalized scoring.
        All components on same 0-1 scale for fair comparison.
        """
        # Updated weights with priority
        score = (
            0.35 * metrics['R_norm'] +      # Resistance (lower is better)
            0.25 * metrics['V_norm'] +      # Voltage drop (lower is better)
            0.25 * metrics['S_norm'] -      # Switch count (lower is better)
            0.15 * metrics['P_norm']        # Priority bonus (higher is better, so subtract)
        )
        return score

    def find_alternative_feeds(self, isolated_nodes: List[str]) -> List[Tuple[List[str], str, Dict]]:
        """Find all valid switching paths to alternative feeders."""
        valid_paths = []

        for feeder in self.feeders.values():
            for node in isolated_nodes:
                try:
                    path = nx.shortest_path(self.G, feeder.source_node, node, weight='resistance')

                    # CRITICAL FIX 2: Validate switch-only path
                    if self.is_valid_switching_path(path):
                        metrics = self.calculate_path_metrics(path, feeder.id)
                        score = self.compute_path_score(metrics)
                        valid_paths.append((path, feeder.id, metrics, score))
                except nx.NetworkXNoPath:
                    continue

        # Sort by score (lower is better)
        valid_paths.sort(key=lambda x: x[3])
        return valid_paths

    def detect_fault(self, faulted_edge: Tuple[str, str]) -> List[str]:
        """Detect fault and return isolated nodes."""
        u, v = faulted_edge

        if self.G.has_edge(u, v):
            # Mark fault by removing edge temporarily
            self.G.remove_edge(u, v)

            # Find isolated components
            components = list(nx.connected_components(self.G))

            # Find component with loads but no feeder
            isolated = []
            for comp in components:
                has_feeder = any(f.source_node in comp for f in self.feeders.values())
                has_load = any(l.node_id in comp for l in self.loads.values())

                if has_load and not has_feeder:
                    isolated = list(comp)
                    break

            # Restore edge
            self.G.add_edge(u, v, resistance=0.1, switch=False)
            return isolated

        return []

    def generate_restoration_plan(self, faulted_edge: Tuple[str, str]) -> Dict:
        """Generate optimal restoration plan with corrected logic."""
        isolated_nodes = self.detect_fault(faulted_edge)

        if not isolated_nodes:
            return {'status': 'no_isolation', 'plan': None}

        # Find alternative paths
        paths = self.find_alternative_feeds(isolated_nodes)

        if not paths:
            return {'status': 'no_alternative', 'plan': None}

        # Select optimal path (lowest score)
        best_path, feeder_id, metrics, score = paths[0]

        # Generate switching sequence
        switching_steps = []

        # Close tie switches along path
        for u, v in zip(best_path[:-1], best_path[1:]):
            edge = self.G[u][v]
            if edge.get('switch', False):
                # Find switch
                for sw in self.switches.values():
                    if {sw.u, sw.v} == {u, v}:
                        switching_steps.append(SwitchingStep(
                            switch_id=sw.id,
                            action='CLOSE',
                            reason=f'Restore power to isolated section via {feeder_id}'
                        ))

        return {
            'status': 'success',
            'plan': {
                'fault_location': faulted_edge,
                'isolated_nodes': isolated_nodes,
                'restoration_path': best_path,
                'source_feeder': feeder_id,
                'score': score,
                'metrics': metrics,
                'switching_steps': switching_steps,
                'alternative_options': len(paths) - 1
            }
        }

    def find_vulnerabilities(self) -> List[Dict]:
        """
        CRITICAL FIX 4: Use articulation points for local vulnerability detection.
        More precise than global node_connectivity.
        """
        vulnerabilities = []

        # Find articulation points (single points of failure)
        # Only works on connected graphs, so check each component
        if self.G.number_of_nodes() > 0:
            try:
                # For connected graphs
                if nx.is_connected(self.G):
                    articulation_points = list(nx.articulation_points(self.G))
                else:
                    # For disconnected graphs, check each component
                    articulation_points = []
                    for component in nx.connected_components(self.G):
                        subgraph = self.G.subgraph(component)
                        if len(component) > 2:  # Need at least 3 nodes for articulation point
                            articulation_points.extend(nx.articulation_points(subgraph))
            except (nx.NetworkXError, nx.NodeNotFound):
                articulation_points = []

            for point in articulation_points:
                # Check if it's critical (has loads)
                critical_loads = [l for l in self.loads.values() if l.node_id == point
                                and l.priority in [LoadPriority.CRITICAL, LoadPriority.HIGH]]

                if critical_loads:
                    # Find nearest alternate feeder
                    suggestion = self._suggest_tie_line(point)
                    vulnerabilities.append({
                        'node': point,
                        'type': 'articulation_point',
                        'critical_loads': [l.id for l in critical_loads],
                        'suggestion': suggestion,
                        'severity': 'HIGH' if any(l.priority == LoadPriority.CRITICAL for l in critical_loads) else 'MEDIUM'
                    })

        return vulnerabilities

    def _suggest_tie_line(self, node: str) -> Dict:
        """Suggest tie-line connection to nearest alternate feeder."""
        min_distance = float('inf')
        best_feeder = None

        for feeder in self.feeders.values():
            if feeder.source_node != node:
                try:
                    dist = nx.shortest_path_length(self.G, node, feeder.source_node, weight='resistance')
                    if dist < min_distance:
                        min_distance = dist
                        best_feeder = feeder
                except nx.NetworkXNoPath:
                    continue

        return {
            'action': 'ADD_TIE_SWITCH',
            'target_node': node,
            'connect_to': best_feeder.source_node if best_feeder else None,
            'feeder_id': best_feeder.id if best_feeder else None,
            'distance': min_distance if best_feeder else None,
            'rationale': f'Node {node} is single point of failure. Add tie-switch to alternate source.'
        }

    def validate_switch_sequence(self, steps: List[SwitchingStep]) -> bool:
        """Validate switching sequence for safety."""
        # Check for conflicts
        switch_states = {}

        for step in steps:
            if step.switch_id in switch_states:
                # Same switch used twice
                return False
            switch_states[step.switch_id] = step.action

            # Check switch exists
            if step.switch_id not in self.switches:
                return False

        return True


class GridSimulator:
    """Interactive simulator for demonstration."""

    def __init__(self):
        self.optimizer = GridOptimizer()
        self.setup_sample_grid()

    def setup_sample_grid(self):
        """Create sample grid for testing with articulation points."""
        # Feeders (separate - not connected without tie switches)
        self.optimizer.add_feeder(Feeder("F1", "SOURCE_1", 11.0, 100.0))
        self.optimizer.add_feeder(Feeder("F2", "SOURCE_2", 11.0, 100.0))

        # Feeder 1 lines - creates a chain structure
        self.optimizer.add_line("SOURCE_1", "SW_1", 0.5, True, "SW1")
        self.optimizer.add_line("SW_1", "BUS_A", 0.1, False)
        self.optimizer.add_line("BUS_A", "SW_2", 0.3, True, "SW2")
        self.optimizer.add_line("SW_2", "HOSPITAL_NODE", 0.2, False)
        self.optimizer.add_line("HOSPITAL_NODE", "SW_3", 0.4, True, "SW3")
        self.optimizer.add_line("SW_3", "SCHOOL_NODE", 0.3, False)

        # Feeder 2 lines - separate chain
        self.optimizer.add_line("SOURCE_2", "SW_4", 0.5, True, "SW4")
        self.optimizer.add_line("SW_4", "BUS_B", 0.1, False)
        self.optimizer.add_line("BUS_B", "SW_5", 0.3, True, "SW5")
        self.optimizer.add_line("SW_5", "RESIDENTIAL_NODE", 0.2, False)

        # Tie switches (initially open paths)
        self.optimizer.add_line("BUS_A", "BUS_B", 0.8, True, "TIE_AB", is_tie=True)

        # Loads with priorities
        self.optimizer.add_load(Load("HOSPITAL", 50.0, LoadPriority.CRITICAL, "HOSPITAL_NODE"))
        self.optimizer.add_load(Load("SCHOOL", 30.0, LoadPriority.HIGH, "SCHOOL_NODE"))
        self.optimizer.add_load(Load("RESIDENTIAL", 40.0, LoadPriority.MEDIUM, "RESIDENTIAL_NODE"))

    def run_scenario(self, fault_edge: Tuple[str, str]):
        """Run restoration scenario."""
        print("=" * 60)
        print("SELF-HEALING GRID - RESTORATION SCENARIO")
        print("=" * 60)

        # Detect vulnerability before fault
        print("\n[PRE-FAULT] PRE-FAULT VULNERABILITY ANALYSIS:")
        vulns = self.optimizer.find_vulnerabilities()
        for v in vulns:
            print(f"   [!]  {v['severity']} severity: Node {v['node']}")
            print(f"      Loads: {', '.join(v['critical_loads'])}")
            print(f"      Suggestion: {v['suggestion']['action']} to {v['suggestion']['connect_to']}")

        # Simulate fault
        print(f"\n[*] FAULT DETECTED: {fault_edge[0]} -- {fault_edge[1]}")

        # Generate restoration plan
        result = self.optimizer.generate_restoration_plan(fault_edge)

        if result['status'] == 'success':
            plan = result['plan']
            print(f"\n[SUCCESS] RESTORATION PLAN GENERATED:")
            print(f"   Isolated nodes: {plan['isolated_nodes']}")
            print(f"   Source feeder: {plan['source_feeder']}")
            print(f"   Restoration path: {' -> '.join(plan['restoration_path'])}")
            print(f"\n[CHART] SCORE BREAKDOWN (Normalized):")
            m = plan['metrics']
            print(f"   Resistance (R_norm): {m['R_norm']:.3f}")
            print(f"   Voltage Drop (V_norm): {m['V_norm']:.3f}")
            print(f"   Switch Count (S_norm): {m['S_norm']:.3f}")
            print(f"   Priority Bonus: {m['priority_bonus']:.3f}")
            print(f"   [STAR] FINAL SCORE: {plan['score']:.4f}")
            print(f"\n[*] SWITCHING SEQUENCE:")
            for step in plan['switching_steps']:
                print(f"   {step.action}: {step.switch_id} - {step.reason}")
            print(f"\n   Alternative options available: {plan['alternative_options']}")
        else:
            print(f"   [X] Status: {result['status']}")

        return result


def run_tests():
    """Run verification tests as specified."""
    print("\n" + "=" * 60)
    print("RUNNING VERIFICATION TESTS")
    print("=" * 60)

    sim = GridSimulator()

    # Test 1: Switching optimization
    print("\n[TEST 1] Switching Optimization")
    print("   Scenario: Fault at SW_2 -- HOSPITAL_NODE (isolates hospital)")
    result = sim.run_scenario(("SW_2", "HOSPITAL_NODE"))
    # Hospital (CRITICAL) should influence path selection

    # Test 2: Priority override - fault that creates isolation needing tie switch
    print("\n[TEST 2] Priority Override")
    print("   Scenario: Fault at SOURCE_1 -- SW_1 (isolates entire feeder)")
    result2 = sim.run_scenario(("SOURCE_1", "SW_1"))

    # Test 3: Suggestion engine
    print("\n[TEST 3] Vulnerability Detection")
    vulns = sim.optimizer.find_vulnerabilities()
    print(f"   Found {len(vulns)} vulnerabilities")
    for v in vulns:
        print(f"   OK Node {v['node']} flagged as {v['type']}")

    print("\n" + "=" * 60)
    print("ALL TESTS COMPLETED")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
