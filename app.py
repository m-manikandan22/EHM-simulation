"""
Power Grid Simulation - Main Application
UI: Side Panel + Overlay for interactive explanation
"""

from flask import Flask, render_template, jsonify, request
from grid_optimizer import GridOptimizer, GridSimulator, Load, Feeder, LoadPriority
import json

app = Flask(__name__)

# Global simulator instance
simulator = GridSimulator()

@app.route('/')
def index():
    """Main UI with side panel."""
    return render_template('index.html')

@app.route('/api/grid/state')
def get_grid_state():
    """Get current grid state."""
    G = simulator.optimizer.G

    nodes = []
    for node in G.nodes():
        node_type = 'feeder' if any(f.source_node == node for f in simulator.optimizer.feeders.values()) else 'bus'

        # Check for loads
        loads = [l for l in simulator.optimizer.loads.values() if l.node_id == node]

        nodes.append({
            'id': node,
            'type': node_type,
            'loads': [{'id': l.id, 'priority': l.priority.name, 'power': l.power} for l in loads]
        })

    edges = []
    for u, v, data in G.edges(data=True):
        edges.append({
            'source': u,
            'target': v,
            'is_switch': data.get('switch', False),
            'is_tie': data.get('tie', False),
            'resistance': data.get('resistance', 0.1)
        })

    return jsonify({'nodes': nodes, 'edges': edges})

@app.route('/api/fault/simulate', methods=['POST'])
def simulate_fault():
    """Simulate fault and return restoration plan."""
    data = request.json
    fault_edge = (data['u'], data['v'])

    result = simulator.optimizer.generate_restoration_plan(fault_edge)

    if result['status'] == 'success':
        plan = result['plan']
        response = {
            'status': 'success',
            'fault': plan['fault_location'],
            'isolated': plan['isolated_nodes'],
            'path': plan['restoration_path'],
            'feeder': plan['source_feeder'],
            'score': plan['score'],
            'metrics': {
                'resistance': plan['metrics']['resistance'],
                'voltage_drop': plan['metrics']['voltage_drop'],
                'switch_count': plan['metrics']['switch_count'],
                'priority_bonus': plan['metrics']['priority_bonus'],
                'R_norm': plan['metrics']['R_norm'],
                'V_norm': plan['metrics']['V_norm'],
                'S_norm': plan['metrics']['S_norm']
            },
            'steps': [{'switch': s.switch_id, 'action': s.action, 'reason': s.reason}
                      for s in plan['switching_steps']],
            'alternatives': plan['alternative_options']
        }
    else:
        response = {'status': result['status'], 'message': 'No valid restoration path found'}

    return jsonify(response)

@app.route('/api/vulnerabilities')
def get_vulnerabilities():
    """Get AI-suggested vulnerabilities and fixes."""
    vulns = simulator.optimizer.find_vulnerabilities()

    response = []
    for v in vulns:
        response.append({
            'node': v['node'],
            'type': v['type'],
            'severity': v['severity'],
            'loads': v['critical_loads'],
            'suggestion': v['suggestion']
        })

    return jsonify({'vulnerabilities': response})

@app.route('/api/ai/assist', methods=['POST'])
def ai_assist():
    """
    AI Assist overlay - provides explanation for current situation.
    """
    data = request.json
    context = data.get('context', 'general')

    if context == 'fault':
        explanation = {
            'title': 'AI Restoration Analysis',
            'description': 'The system has analyzed multiple restoration paths using normalized scoring.',
            'factors': [
                {'name': 'Resistance (35%)', 'value': 'Normalized to 0-1 scale', 'why': 'Technical losses'},
                {'name': 'Voltage Drop (25%)', 'value': 'Normalized to 0-1 scale', 'why': 'Power quality'},
                {'name': 'Switch Count (25%)', 'value': 'Normalized to 0-1 scale', 'why': 'Switching time'},
                {'name': 'Priority Bonus (15%)', 'value': 'Critical loads preferred', 'why': 'Public safety'}
            ],
            'note': 'All metrics normalized for fair comparison. Hospital loads receive priority.'
        }
    elif context == 'vulnerability':
        explanation = {
            'title': 'AI Grid Design Assistant',
            'description': 'Articulation points detected using graph theory. These are single points of failure.',
            'recommendation': 'Add tie-switches to alternate feeders for redundancy.',
            'method': 'Uses articulation_points() algorithm for precise local detection'
        }
    else:
        explanation = {
            'title': 'Grid Optimization AI',
            'description': 'Self-healing grid with multi-objective optimization.',
            'features': [
                'Normalized scoring for fair comparison',
                'Switch-only path validation',
                'Priority-aware load handling',
                'Articulation point vulnerability detection'
            ]
        }

    return jsonify(explanation)

@app.route('/api/suggestions/execute', methods=['POST'])
def execute_suggestion():
    """Execute AI suggestion (simulated)."""
    data = request.json
    suggestion_type = data.get('type')

    if suggestion_type == 'ADD_TIE_SWITCH':
        node = data.get('node')
        connect_to = data.get('connect_to')

        # Simulate adding tie switch
        return jsonify({
            'status': 'simulated',
            'action': f'Added tie switch between {node} and {connect_to}',
            'improvement': 'Single point of failure eliminated'
        })

    return jsonify({'status': 'error', 'message': 'Unknown suggestion type'})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
