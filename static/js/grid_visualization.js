
// Grid Visualization with D3.js
// Interactive power grid with fault simulation

let svg, simulation, nodes, links;
let faultedEdge = null;
let restorationPath = null;

const width = window.innerWidth - 350; // Account for side panel
const height = window.innerHeight - 60; // Account for toolbar

const colors = {
    source: '#00d4aa',
    bus: '#4a9eff',
    load: '#ff4444',
    switch: '#00d4aa',
    tie: '#ffc107',
    fault: '#ff4444',
    restoration: '#00ff88'
};

// Initialize on load
document.addEventListener('DOMContentLoaded', function() {
    initGraph();
    loadGridState();
});

function initGraph() {
    const container = document.getElementById('graph-container');
    container.innerHTML = '';

    svg = d3.select('#graph-container')
        .append('svg')
        .attr('width', '100%')
        .attr('height', '100%')
        .attr('viewBox', `0 0 ${width} ${height}`);

    // Add zoom behavior
    const zoom = d3.zoom()
        .scaleExtent([0.5, 3])
        .on('zoom', (event) => {
            g.attr('transform', event.transform);
        });

    svg.call(zoom);

    const g = svg.append('g');

    // Define arrow markers
    const defs = svg.append('defs');

    defs.append('marker')
        .attr('id', 'arrow-switch')
        .attr('viewBox', '0 -5 10 10')
        .attr('refX', 20)
        .attr('refY', 0)
        .attr('markerWidth', 6)
        .attr('markerHeight', 6)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,-5L10,0L0,5')
        .attr('fill', colors.switch);
}

function loadGridState() {
    fetch('/api/grid/state')
        .then(res => res.json())
        .then(data => {
            renderGraph(data);
        });
}

function renderGraph(data) {
    const container = d3.select('#graph-container svg g');
    container.selectAll('*').remove();

    // Prepare nodes and links for D3
    nodes = data.nodes.map(n => ({
        id: n.id,
        type: n.type,
        loads: n.loads,
        x: Math.random() * width * 0.8 + width * 0.1,
        y: Math.random() * height * 0.8 + height * 0.1
    }));

    links = data.edges.map(e => ({
        source: e.source,
        target: e.target,
        isSwitch: e.is_switch,
        isTie: e.is_tie,
        resistance: e.resistance
    }));

    // Create force simulation
    simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(d => d.id).distance(100))
        .force('charge', d3.forceManyBody().strength(-500))
        .force('center', d3.forceCenter(width / 2, height / 2))
        .force('collision', d3.forceCollide().radius(40));

    // Draw links
    const linkElements = container.selectAll('.link')
        .data(links)
        .enter()
        .append('line')
        .attr('class', d => {
            let classes = 'link';
            if (d.isSwitch) classes += ' switch';
            if (d.isTie) classes += ' tie';
            if (faultedEdge &&
                ((d.source.id || d.source) === faultedEdge[0] &&
                 (d.target.id || d.target) === faultedEdge[1])) {
                classes += ' fault';
            }
            if (restorationPath) {
                const pathNodes = restorationPath;
                const source = d.source.id || d.source;
                const target = d.target.id || d.target;
                for (let i = 0; i < pathNodes.length - 1; i++) {
                    if ((pathNodes[i] === source && pathNodes[i+1] === target) ||
                        (pathNodes[i] === target && pathNodes[i+1] === source)) {
                        classes += ' restoration';
                        break;
                    }
                }
            }
            return classes;
        })
        .on('click', function(event, d) {
            if (currentMode === 'fault') {
                const u = d.source.id || d.source;
                const v = d.target.id || d.target;
                handleFault([u, v]);
            }
        });

    // Draw nodes
    const nodeElements = container.selectAll('.node')
        .data(nodes)
        .enter()
        .append('g')
        .attr('class', 'node')
        .call(d3.drag()
            .on('start', dragstarted)
            .on('drag', dragged)
            .on('end', dragended));

    // Node circles
    nodeElements.append('circle')
        .attr('r', d => {
            if (d.type === 'feeder') return 25;
            if (d.loads && d.loads.length > 0) return 20;
            return 15;
        })
        .attr('fill', d => {
            if (d.type === 'feeder') return colors.source;
            if (d.loads && d.loads.length > 0) return colors.load;
            return colors.bus;
        })
        .attr('stroke', d => {
            if (d.loads && d.loads.some(l => l.priority === 'CRITICAL')) {
                return '#ffc107'; // Gold border for critical loads
            }
            return '#0a1628';
        })
        .attr('stroke-width', d => {
            if (d.loads && d.loads.some(l => l.priority === 'CRITICAL')) {
                return 3;
            }
            return 2;
        });

    // Node labels
    nodeElements.append('text')
        .attr('dy', d => {
            if (d.type === 'feeder') return 35;
            if (d.loads && d.loads.length > 0) return 30;
            return 25;
        })
        .text(d => {
            if (d.loads && d.loads.length > 0) {
                return d.loads[0].id;
            }
            return d.id;
        })
        .style('font-size', d => d.type === 'feeder' ? '12px' : '10px')
        .style('font-weight', d => d.type === 'feeder' ? 'bold' : 'normal');

    // Priority indicator for critical loads
    nodeElements.filter(d => d.loads && d.loads.some(l => l.priority === 'CRITICAL'))
        .append('text')
        .attr('dy', -25)
        .text('🏥')
        .style('font-size', '14px');

    // Update positions on tick
    simulation.on('tick', () => {
        linkElements
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y);

        nodeElements
            .attr('transform', d => `translate(${d.x},${d.y})`);
    });

    function dragstarted(event, d) {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
    }

    function dragged(event, d) {
        d.fx = event.x;
        d.fy = event.y;
    }

    function dragended(event, d) {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
    }
}

function handleFault(edge) {
    faultedEdge = edge;
    console.log('Fault detected at:', edge);

    // Update visual
    loadGridState();

    // Send to backend
    fetch('/api/fault/simulate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({u: edge[0], v: edge[1]})
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            restorationPath = data.path;
            updateStatusBar(data);
            showRestorationOverlay(data);
            loadGridState(); // Refresh with restoration path
        } else {
            alert('No restoration path found: ' + data.message);
        }
    });
}

function updateStatusBar(data) {
    const statusBar = document.getElementById('status-bar');
    statusBar.classList.add('active');

    document.getElementById('score-value').textContent = data.score.toFixed(4);
    document.getElementById('res-value').textContent = data.metrics.resistance.toFixed(2);
    document.getElementById('switch-value').textContent = data.metrics.switch_count;
    document.getElementById('priority-value').textContent = data.metrics.priority_bonus.toFixed(2);
}

function showRestorationOverlay(data) {
    const overlay = document.getElementById('ai-overlay');
    const title = document.getElementById('overlay-title');
    const body = document.getElementById('overlay-body');

    title.textContent = '⚡ Restoration Plan Generated';

    let content = `
        <p style="margin-bottom: 15px; color: #b0c4de;">
            Fault detected at <strong>${data.fault[0]} — ${data.fault[1]}</strong>.<br>
            Isolated nodes: <strong>${data.isolated.join(', ')}</strong>
        </p>

        <div class="factor-grid">
            <div class="factor-card">
                <div class="label">Resistance (35%)</div>
                <div class="value">${data.metrics.R_norm.toFixed(3)}</div>
                <div class="why">Normalized: ${data.metrics.resistance.toFixed(2)}Ω</div>
            </div>
            <div class="factor-card">
                <div class="label">Voltage Drop (25%)</div>
                <div class="value">${data.metrics.V_norm.toFixed(3)}</div>
                <div class="why">Tech. losses</div>
            </div>
            <div class="factor-card">
                <div class="label">Switch Count (25%)</div>
                <div class="value">${data.metrics.S_norm.toFixed(3)}</div>
                <div class="why">${data.metrics.switch_count} switches</div>
            </div>
            <div class="factor-card">
                <div class="label">Priority Bonus (-15%)</div>
                <div class="value">-${data.metrics.P_norm.toFixed(3)}</div>
                <div class="why">Critical loads</div>
            </div>
        </div>

        <div class="note-box">
            <h4>⭐ Final Score: ${data.score.toFixed(4)} (Lower is Better)</h4>
            <p>Restoration path: <strong>${data.path.join(' → ')}</strong></p>
            <p>Source feeder: <strong>${data.feeder}</strong></p>
            <p>Alternative options available: <strong>${data.alternatives}</strong></p>
        </div>

        <h4 style="margin-top: 20px; margin-bottom: 10px; color: #00d4aa;">🔧 Switching Sequence:</h4>
        <div style="background: rgba(0,0,0,0.3); padding: 15px; border-radius: 8px;">
    `;

    data.steps.forEach((step, i) => {
        content += `
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px; padding: 8px; background: rgba(0,212,170,0.1); border-radius: 4px;">
                <span style="color: #00d4aa; font-weight: bold;">${i + 1}.</span>
                <span style="color: #00ff88; font-weight: 600;">${step.action}</span>
                <span style="color: #e0e6ed;">${step.switch}</span>
                <span style="color: #8b9dc3; font-size: 11px; margin-left: auto;">${step.reason}</span>
            </div>
        `;
    });

    content += '</div>';

    // Add AI Assist button
    content += `
        <button class="action-btn" style="margin-top: 20px;"
            onclick="showAIAssist('fault'); closeOverlay(); setTimeout(() => document.getElementById('ai-overlay').classList.add('active'), 100);">
            🤖 Explain Scoring Method
        </button>
    `;

    body.innerHTML = content;
    overlay.classList.add('active');
}

// Handle window resize
window.addEventListener('resize', () => {
    const newWidth = window.innerWidth - 350;
    const newHeight = window.innerHeight - 60;
    d3.select('#graph-container svg')
        .attr('viewBox', `0 0 ${newWidth} ${newHeight}`);
    if (simulation) {
        simulation.force('center', d3.forceCenter(newWidth / 2, newHeight / 2));
        simulation.alpha(0.3).restart();
    }
});
