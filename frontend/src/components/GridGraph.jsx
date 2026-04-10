import React, { useRef, useEffect, useCallback, useState } from 'react'
import * as d3 from 'd3'
import { MODES } from './Toolbar'
import { addNode, addEdge, failNodeAPI, moveNodeAPI, deleteNodeAPI, getAISuggestions } from '../services/api'

const getShape = (type) => {
  // Generation Layer
  if (type === 'generator')               return d3.symbol().type(d3.symbolTriangle).size(800)()
  if (type === 'generator_solar')         return d3.symbol().type(d3.symbolStar).size(1000)()
  if (type === 'generator_wind')           return d3.symbol().type(d3.symbolTriangle).size(900)()
  if (type === 'generator_nuclear')        return d3.symbol().type(d3.symbolWye).size(1000)()
  if (type === 'generator_coal')           return d3.symbol().type(d3.symbolTriangle).size(800)()
  if (type === 'generator_gas')            return d3.symbol().type(d3.symbolTriangle).size(800)()
  // Legacy types
  if (type === 'solar')                    return d3.symbol().type(d3.symbolStar).size(800)()
  if (type === 'wind')                     return d3.symbol().type(d3.symbolTriangle).size(600)()
  // Storage
  if (type === 'battery')                  return d3.symbol().type(d3.symbolSquare).size(800)()
  if (type === 'supercap')                return d3.symbol().type(d3.symbolDiamond).size(700)()
  // Distribution
  if (type === 'substation')              return d3.symbol().type(d3.symbolSquare).size(1200)()
  if (type === 'transformer')             return d3.symbol().type(d3.symbolDiamond).size(600)()
  if (type === 'pole')                     return d3.symbol().type(d3.symbolCircle).size(200)()
  if (type === 'service')                 return d3.symbol().type(d3.symbolDiamond).size(150)()
  // Load Types
  if (type === 'house')                    return d3.symbol().type(d3.symbolCircle).size(300)()
  if (type === 'hospital')                return d3.symbol().type(d3.symbolSquare).size(500)()
  if (type === 'industry')                return d3.symbol().type(d3.symbolSquare).size(700)()
  if (type === 'commercial')              return d3.symbol().type(d3.symbolCircle).size(400)()
  // Other
  if (type === 'step_up')                 return d3.symbol().type(d3.symbolTriangle).size(400)()
  if (type === 'switch')                  return d3.symbol().type(d3.symbolCross).size(200)()
  if (type === 'scada')                   return d3.symbol().type(d3.symbolSquare).size(1200)()
  return d3.symbol().type(d3.symbolCircle).size(60)()
}

const getIcon = (type) => {
  // Generation Layer
  if (type === 'generator')               return '🏭'
  if (type === 'generator_solar')         return '☀️'
  if (type === 'generator_wind')           return '🌬️'
  if (type === 'generator_nuclear')        return '⚛️'
  if (type === 'generator_coal')           return '🏭'
  if (type === 'generator_gas')            return '🔥'
  // Legacy types
  if (type === 'solar')                    return '☀️'
  if (type === 'wind')                     return '🌬️'
  // Storage
  if (type === 'battery')                  return '🔋'
  if (type === 'supercap')                return '⚡'
  // Distribution
  if (type === 'step_up')                 return '🗼'
  if (type === 'substation')              return '⚡'
  if (type === 'transformer')             return '🔌'
  if (type === 'pole')                     return '📍'
  if (type === 'service')                 return '🔌'
  // Load Types
  if (type === 'house')                    return '🏠'
  if (type === 'hospital')                return '🏥'
  if (type === 'industry')                return '🏭'
  if (type === 'commercial')              return '🏢'
  if (type === 'switch')                  return '🎚️'
  if (type === 'scada')                   return '🧠'
  return '🏠'
}

function getNodeColor(node) {
  // ── Physics-first coloring: state > voltage > type ──────────────────
  // 🔴 Hard fault — root-cause node that failed
  if (node.failed) return '#ff3333'
  // 🟠 Isolated — downstream of fault, awaiting FLISR restoration
  if (node.isolated) return '#ff9900'
  // SCADA node is purely decorative
  if (node.is_scada) return 'none'
  // 🟡 Under-voltage warning (< 0.95 pu) — physics-driven stress indicator
  const v = node.voltage ?? 1.0
  if (v > 0 && v < 0.90) return '#ff4444'   // critical low voltage
  if (v > 0 && v < 0.95) return '#ffcc00'   // under-voltage warning
  // 🟢 Energized — type-based color (hierarchy)
  switch (node.node_type) {
    case 'generator':         return '#ffb84d'
    case 'generator_solar':   return '#ffee00'
    case 'generator_wind':    return '#87ceeb'
    case 'generator_nuclear': return '#3b82f6'
    case 'generator_coal':    return '#888888'
    case 'generator_gas':     return '#ff6633'
    case 'solar':             return '#ffee00'
    case 'wind':              return '#87ceeb'
    case 'battery':           return '#dd55ff'
    case 'supercap':          return '#ff3399'
    case 'step_up':           return '#ff6633'
    case 'substation':        return '#7777ff'
    case 'transformer':       return '#3399ff'
    case 'pole':              return '#00dddd'
    case 'switch':            return '#cc77ff'
    case 'service':           return '#00dddd'
    case 'house':             return '#33ff33'
    case 'hospital':          return '#ff4488'
    case 'industry':          return '#bb44ff'
    case 'commercial':        return '#00ccff'
    default:                  return '#cccccc'
  }
}


// ── Physics-driven edge color ───────────────────────────────────────
// Derived ONLY from grid state — NOT from RL action names or AI colors.
function getEdgeColor(edge) {
  // 🔴 Fault-locked breaker (protection relay tripped)
  if (edge.switch_status === 'fault_locked') return '#ff3333'
  // 🔴 Broken cable
  if (edge.status === 'broken') return '#ff5555'
  // 🟡 FLISR rerouted (new active path after tie-switch closed)
  if (edge.status === 'rerouted') return '#ffaa00'
  // ⚫ Inactive (open switch / disabled)
  if (!edge.active) return 'rgba(180,180,180,0.25)'
  // ─── Active edge: color by ENERGY SOURCE TYPE (set by physics engine) ───
  const flow = Math.abs(edge.flow || 0)
  if (flow > 0.01) {
    const src = edge.source_type || edge.source?.source_type || 'grid'
    if (src === 'solar')                    return '#FFD700'  // Yellow  — solar
    if (src === 'wind')                     return '#00BFFF'  // Cyan    — wind
    if (src === 'battery' || src === 'supercap') return '#cc44ff'  // Purple  — storage
    if (src === 'nuclear')                  return '#3b82f6'  // Blue    — nuclear
    if (src === 'coal')                     return '#888888'  // Gray    — coal
    if (src === 'gas')                      return '#ff6633'  // Orange  — gas
    return '#00FFCC'   // Teal — generic grid power
  }
  // Tie switch type colouring when closed (FLISR activated)
  if (edge.is_tie_switch && edge.active) return '#00FFCC'
  return 'rgba(180,180,180,0.3)'  // Idle / no flow
}

// Must match backend grid.py canvas dimensions
const MODEL_W = 1500
const MODEL_H = 920

export default function GridGraph({
  gridState,
  aiState,
  width = 1100, height = 700,
  selectedNode, onSelectNode,
  selectedEdge, onSelectEdge,
  onCutEdge,
  onFailNode,
  onAddHouse,
  currentMode,
  addNodeType,
  interactionState,
  setInteractionState,
  onMessage,
  onUpdate,
}) {
  const svgRef  = useRef(null)
  const dataRef = useRef({ nodes: [], links: [] })
  const zoomRef = useRef(null)
  const [suggestions, setSuggestions] = useState([])
  const [flowStatus, setFlowStatus] = useState('flowing') // flowing, stopped, rerouting, no-flow
  const canvasRef = useRef(null)
  
  // ── Canvas Animation Loop for flow ───────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    let animationFrameId
    let time = 0

    const resize = () => {
      canvas.width = canvas.parentElement.clientWidth * window.devicePixelRatio
      canvas.height = canvas.parentElement.clientHeight * window.devicePixelRatio
      ctx.scale(window.devicePixelRatio, window.devicePixelRatio)
    }
    resize()
    window.addEventListener('resize', resize)

    const FLOW_THRESHOLD = 0.02;  // ignore tiny noise / phantom flow

    const colorMap = {
      solar:   '#FFD700',
      wind:    '#87CEEB',
      battery: '#cc44ff',
      supercap:'#cc44ff',
      nuclear: '#3b82f6',
      coal:    '#888888',
      gas:     '#ff6633',
      reroute: '#f59e0b',
      grid:    '#00FFCC',
    };

    const drawEnergyFlow = (ctx, edge, time, transform, nodeMap) => {
      // Safely resolve source/target (D3 may give objects or string IDs)
      const source = typeof edge.source === 'string'
        ? nodeMap.get(edge.source)
        : edge.source;
      const target = typeof edge.target === 'string'
        ? nodeMap.get(edge.target)
        : nodeMap.get(edge.target.id) || edge.target; // ensure target

      if (!source || !target) return; // SAFEGUARD

      const flow = Math.abs(edge.flow || 0);
      if (flow < FLOW_THRESHOLD || !edge.active) return;

      const x1 = source.x * transform.k + transform.x;
      const y1 = source.y * transform.k + transform.y;
      const x2 = target.x * transform.k + transform.x;
      const y2 = target.y * transform.k + transform.y;

      if (!isFinite(x1) || !isFinite(y1) || !isFinite(x2) || !isFinite(y2)) return;

      // ── Color logic ──────────────────────────────────────────
      let color = colorMap[edge.source_type] || '#00FFCC';

      if (edge.charging) {
        // CHARGING: amber pulse — surplus power flowing INTO storage
        color = '#f59e0b';
        ctx.shadowBlur = 10 * transform.k;
        ctx.shadowColor = '#f59e0b';
      } else if (edge.is_tie_switch && edge.active) {
        // TIE-LINE REROUTE: bright green
        color = '#22c55e';
        ctx.shadowBlur = 6 * transform.k;
        ctx.shadowColor = '#22c55e';
      } else if (edge.status === 'rerouted') {
        color = '#f59e0b';
        ctx.shadowBlur = 8 * transform.k;
        ctx.shadowColor = '#f59e0b';
      } else {
        ctx.shadowBlur = 0;
      }

      // ── Thickness: physics-driven ─────────────────────────────
      ctx.lineWidth = (1 + flow * 1.2) * transform.k;

      // ── Speed: charging is slower pulse, discharge is faster ──
      ctx.setLineDash([8, 6]);
      const speed = edge.charging
        ? (time * 0.012)                        // slow amber charge
        : -(time * (0.015 + flow * 0.02));      // normal forward flow
      ctx.lineDashOffset = speed;

      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();

      ctx.setLineDash([]);
      ctx.shadowBlur = 0;
    };

    const animate = () => {
      time += 16
      const { width, height } = canvas
      ctx.clearRect(0, 0, width, height)

      const links = dataRef.current.links || []
      const nodes = dataRef.current.nodes || []
      const svgNode = svgRef.current
      if (!svgNode) {
          animationFrameId = requestAnimationFrame(animate)
          return
      }
      const transform = d3.zoomTransform(svgNode)

      const nodeMap = new Map(nodes.map(n => [n.id, n]))

      links.forEach(edge => {
        if (Math.abs(edge.flow || 0) > FLOW_THRESHOLD) {
          drawEnergyFlow(ctx, edge, time, transform, nodeMap)
        }
      })

      animationFrameId = requestAnimationFrame(animate)
    }
    animate()

    return () => {
      window.removeEventListener('resize', resize)
      cancelAnimationFrame(animationFrameId)
    }
  }, [])

  // ── Keyboard: Delete / Backspace removes selected edge ──────────
  useEffect(() => {
    const onKey = (e) => {
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedEdge && onCutEdge)
        onCutEdge(selectedEdge.u, selectedEdge.v)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedEdge, onCutEdge])



  // ── Flow Status Detection ───────────────────────────────────────────
  useEffect(() => {
    if (!gridState) return
    const edges = gridState.edges || []
    const hasFlow = edges.some(e => Math.abs(e.flow || 0) > 0.01)

    const nodes = Object.values(gridState.nodes || {})
    const hasFault = nodes.some(n => n.failed) || gridState.storm_active

    if (hasFault) {
      setFlowStatus(hasFlow ? 'rerouting' : 'stopped')
    } else {
      setFlowStatus(hasFlow ? 'flowing' : 'INITIALIZING') // 🔄 Replaces no-flow with initializing state
    }
  }, [gridState])

  // ── Convert backend state → d3 data arrays ───────────────────────
  const buildData = useCallback((state) => {
    if (!state) return
    const nodeMap = state.nodes || {}
    const edgeArr = state.edges || []
    dataRef.current.nodes = Object.keys(nodeMap).map(id => ({ id, ...nodeMap[id] }))
    
    // Inject fixed SCADA Control Layer Node
    dataRef.current.nodes.push({
      id: 'SCADA', node_type: 'scada',
      x: MODEL_W / 2, y: -40,
      generation: 0, load: 0,
      is_scada: true
    })

    const nMap = Object.fromEntries(dataRef.current.nodes.map(n => [n.id, n]))
    dataRef.current.links = edgeArr
      .map(e => ({ ...e, source: nMap[e.source], target: nMap[e.target] }))
      .filter(e => e.source && e.target)

    // — Store fault segment for overlay rendering
    dataRef.current.faultSegment = state.last_fault_segment || {}
  }, [])

  // ── One-time SVG init: zoom, markers, grid lines ──────────────────
  useEffect(() => {
    const svg = d3.select(svgRef.current)
    svg.selectAll('*').remove()

    // D3 zoom
    const zoom = d3.zoom()
      .scaleExtent([0.15, 5])
      .on('zoom', (event) => svg.select('g.zoom-root').attr('transform', event.transform))
    zoomRef.current = zoom
    svg.call(zoom)

    // Fit model into visible viewport on first load
    const s  = Math.min(width / MODEL_W, height / MODEL_H) * 0.90
    const tx = (width  - MODEL_W * s) / 2
    const ty = (height - MODEL_H * s) / 2
    svg.call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(s))

    // Root zoom group — everything inside scales together
    const root = svg.append('g').attr('class', 'zoom-root')

    // Arrow markers (declared outside root, but reference by ID)
    const defs = svg.append('defs')
    defs.append('marker').attr('id', 'arrow-blue')
      .attr('viewBox', '0 -4 10 8').attr('refX', 18).attr('refY', 0)
      .attr('markerWidth', 5).attr('markerHeight', 5).attr('orient', 'auto')
      .append('path').attr('d', 'M0,-4L10,0L0,4').attr('fill', '#3b82f6')
    defs.append('marker').attr('id', 'arrow-purple')
      .attr('viewBox', '0 -4 10 8').attr('refX', 18).attr('refY', 0)
      .attr('markerWidth', 5).attr('markerHeight', 5).attr('orient', 'auto-start-reverse')
      .append('path').attr('d', 'M0,-4L10,0L0,4').attr('fill', '#8b5cf6')

    // Engineering faint grid (in model space)
    const grid = root.append('g').style('pointer-events', 'none')
    for (let x = 0; x <= MODEL_W; x += 50)
      grid.append('line').attr('x1', x).attr('y1', 0).attr('x2', x).attr('y2', MODEL_H)
        .attr('stroke', 'rgba(255,255,255,0.03)')
    for (let y = 0; y <= MODEL_H; y += 50)
      grid.append('line').attr('x1', 0).attr('y1', y).attr('x2', MODEL_W).attr('y2', y)
        .attr('stroke', 'rgba(255,255,255,0.03)')

    // Zoom hint
    root.append('text')
      .attr('x', MODEL_W - 10).attr('y', MODEL_H - 8)
      .attr('text-anchor', 'end')
      .style('font-size', '9px').style('fill', 'rgba(255,255,255,0.15)')
      .style('font-family', 'JetBrains Mono').style('pointer-events', 'none')
      .text('Scroll to zoom  •  Drag to pan')

    // Preview wire (CONNECT mode)
    root.append('line').attr('class', 'preview-link')
      .attr('stroke', '#eab308').attr('stroke-width', 2)
      .attr('stroke-dasharray', '5 5')
      .style('opacity', 0).style('pointer-events', 'none')

    root.append('g').attr('class', 'links')
    root.append('g').attr('class', 'scada-layer')  // Above links, below nodes
    root.append('g').attr('class', 'nodes')

    // Background click → ADD_NODE (coords inverted through zoom transform)
    svg.on('click.canvas', async (event) => {
      if (currentMode !== MODES.ADD_NODE) return
      const t = d3.zoomTransform(svgRef.current)
      const [mx, my] = d3.pointer(event)
      const [x, y]   = t.invert([mx, my])
      try {
        const res = await addNode(addNodeType, [x, y])
        onUpdate?.(res.grid)
        onMessage?.(res.message)
      } catch (e) {
        onMessage?.('Failed to add node: ' + e.message)
      }
    })

    // Mousemove preview wire
    svg.on('mousemove.preview', (event) => {
      if (currentMode === MODES.CONNECT && interactionState.activeSrcNode) {
        const t = d3.zoomTransform(svgRef.current)
        const [mx, my] = d3.pointer(event)
        const [wx, wy] = t.invert([mx, my])
        const src = interactionState.activeSrcNode
        root.select('.preview-link')
          .attr('x1', src.x).attr('y1', src.y)
          .attr('x2', wx).attr('y2', wy)
          .style('opacity', 1)
      } else {
        root.select('.preview-link').style('opacity', 0)
      }
    })

    // Tooltip element
    const tip = d3.select('body').append('div')
      .attr('id', 'grid-tooltip')
      .style('position', 'fixed').style('background', 'rgba(15,23,42,0.95)')
      .style('border', '1px solid rgba(255,255,255,0.1)').style('border-radius', '6px')
      .style('padding', '8px 12px').style('font-size', '11px').style('color', '#e8f4ff')
      .style('pointer-events', 'none').style('opacity', 0).style('z-index', 9999)

    return () => {
      tip.remove()
      svg.on('mousemove.preview', null)
      svg.on('click.canvas', null)
    }
  }, [width, height, currentMode, interactionState, addNodeType])  // eslint-disable-line

  // ── Render on every grid state / mode change ──────────────────
  useEffect(() => {
    if (!gridState) return
    buildData(gridState)
    const { nodes, links, faultSegment } = dataRef.current
    if (!nodes.length) return

    const svg  = d3.select(svgRef.current)
    const root = svg.select('g.zoom-root')
    const tip  = d3.select('#grid-tooltip')

    // Focus+Context: which nodes are connected to selected?
    const connected = new Set()
    if (selectedNode) {
      connected.add(selectedNode)
      links.forEach(l => {
        if (l.source.id === selectedNode) connected.add(l.target.id)
        if (l.target.id === selectedNode) connected.add(l.source.id)
      })
    }

    // ── LINKS ──────────────────────────────────────────────────────
    const edgeKey = d => `${d.source.id}|${d.target.id}`

    // ── Power Flow paths (⚡ blue, continuous line) ────────────────
    const linkSel = root.select('.links')
      .selectAll('path.wire')
      .data(links, edgeKey)

    const linkEnter = linkSel.enter().append('path')
      .attr('class', 'wire')
      .attr('fill', 'none')
      .attr('stroke-linecap', 'round')
      .style('cursor', 'pointer')
      .on('click', (event, d) => {
        event.stopPropagation()
        if (currentMode === MODES.CUT_EDGE)  onCutEdge?.(d.source.id, d.target.id)
        if (currentMode === MODES.SELECT)    onSelectEdge?.({ u: d.source.id, v: d.target.id })
      })

    const pathD = d => `M${d.source.x},${d.source.y} L${d.target.x},${d.target.y}`

    linkEnter.merge(linkSel)
      .attr('d', pathD)
      .style('opacity', d => {
        if (!selectedNode) return d.active ? 1 : 0.25
        const conn = d.source.id === selectedNode || d.target.id === selectedNode
        return conn ? (d.active ? 1 : 0.4) : 0.12
      })
      .attr('stroke', d => {
        // Cut edge mode — highlight red to confirm action
        if (currentMode === MODES.CUT_EDGE) return '#ff3333'
        // All other coloring is physics-driven via getEdgeColor()
        return getEdgeColor(d)
      })
      .attr('stroke-width', d => {
        if (!d.active) return 1
        // Base width by hierarchy level
        let baseWidth = 1.5
        if (d.source.node_type === 'generator' || d.source.node_type === 'substation' || d.target.node_type === 'substation') baseWidth = 5.0
        else if (d.source.node_type === 'transformer' || d.target.node_type === 'transformer') baseWidth = 4.0
        else if (d.source.node_type === 'pole' && d.target.node_type === 'pole') baseWidth = 3.0
        else if (d.source.node_type === 'house' || d.target.node_type === 'house') baseWidth = 1.2
        else baseWidth = 2.5
        // STEP 9: Physics-driven thickness — thicker = more MW
        const flowBoost = Math.abs(d.flow || 0) * 1.2
        return Math.min(Math.max(baseWidth, 1.5 + flowBoost), 8)
      })
      .style('stroke-dasharray', d => {
        if (d.status === 'broken') return '10 5'
        if (d.is_tie_switch) return '6 4'
        return 'none'
      })
      .style('filter', d => {
        if (d.status === 'rerouted') return 'drop-shadow(0 0 6px #f59e0b)'
        if (d.status === 'broken') return 'drop-shadow(0 0 3px #7f1d1d)'
        return 'none'
      })
      .attr('class', d => {
        if (d.switch_status === 'fault_locked') return 'wire fault-segment'
        if (d.status === 'rerouted')            return 'wire reroute-active'     
        if (d.status === 'broken')              return 'wire reroute-broken'     
        if (d.is_tie_switch && d.active)        return 'wire tie-switch-closed'  
        if (d.is_tie_switch && !d.active)       return 'wire tie-switch-open'    
        if (!d.active && (d.source.failed || d.target.failed)) return 'wire fault-segment'
        return 'wire'
      })
      .attr('marker-end', null)

    linkSel.exit().remove()

    // ── Fault Segment Overlay (amber bounding box) ─────────────────────
    // Drawn when last_fault_segment is populated (after isolation, before repair)
    const overlayGroup = root.select('.links')  // insert below nodes
    overlayGroup.selectAll('rect.fault-segment-overlay').remove()
    if (faultSegment && faultSegment.affected_nodes && faultSegment.affected_nodes.length > 0) {
      const affectedNodes = faultSegment.affected_nodes
        .map(nid => nodes.find(n => n.id === nid))
        .filter(Boolean)
      if (affectedNodes.length > 0) {
        const pad = 18
        const xs  = affectedNodes.map(n => n.x)
        const ys  = affectedNodes.map(n => n.y)
        const x1  = Math.min(...xs) - pad
        const y1  = Math.min(...ys) - pad
        const x2  = Math.max(...xs) + pad
        const y2  = Math.max(...ys) + pad
        overlayGroup.insert('rect', ':first-child')
          .attr('class', 'fault-segment-overlay')
          .attr('x', x1).attr('y', y1)
          .attr('width',  x2 - x1)
          .attr('height', y2 - y1)
          .attr('rx', 6).attr('ry', 6)
          .attr('fill',   'rgba(255,51,51,0.12)')
          .attr('stroke', '#ff3333')
          .attr('stroke-width', 1.5)
          .attr('stroke-dasharray', '6 3')
          .style('pointer-events', 'none')
      }
    }

    // ── SCADA / AI Suggestion overlays REMOVED ────────────────────────
    // These were driven by RL action colors (not physics) and misled viewers.
    // All visual state now comes exclusively from the grid physics engine:
    //   node color  → node.failed / node.isolated / node.voltage
    //   edge color  → edge.active / edge.flow / edge.source_type
    //   edge width  → abs(edge.flow)
    // Clear the scada-layer so stale overlays from previous renders vanish.
    root.select('.scada-layer').selectAll('*').remove()

    const nodeSel = root.select('.nodes')
      .selectAll('g.node')
      .data(nodes, d => d.id)

    let dragStart = [0, 0];
    const dragHandler = d3.drag()
      .on('start', (event, d) => {
        dragStart = [event.x, event.y];
        if (currentMode !== MODES.SELECT) return
        event.sourceEvent.stopPropagation()
      })
      .on('drag', function(event, d) {
        if (currentMode !== MODES.SELECT) return
        const t = d3.zoomTransform(svgRef.current)
        // ✅ CORRECT CALC: Invert mouse pos to get world-space pos
        d.x = t.invertX(event.sourceEvent.clientX - svgRef.current.getBoundingClientRect().left)
        d.y = t.invertY(event.sourceEvent.clientY - svgRef.current.getBoundingClientRect().top)
        
        d3.select(this).attr('transform', `translate(${d.x},${d.y})`)
        root.selectAll('path.wire')
            .filter(l => l.source.id === d.id || l.target.id === d.id)
            .attr('d', l => `M${l.source.x},${l.source.y} L${l.target.x},${l.target.y}`)
      })
      .on('end', async (event, d) => {
        const dx = Math.abs(event.x - dragStart[0]);
        const dy = Math.abs(event.y - dragStart[1]);

        if (dx < 5 && dy < 5) {
          handleNodeClick(event, d);
          return;
        }

        if (currentMode !== MODES.SELECT) return
        try {
          const res = await moveNodeAPI(d.id, d.x, d.y)
          onUpdate?.(res.grid)
        } catch (e) {
          onMessage?.('Failed to move: ' + e.message)
        }
      })

    const handleNodeClick = async (event, d) => {
      event.stopPropagation?.();
      if (currentMode === MODES.SELECT) {
        onSelectNode?.(d.id)
      } else if (currentMode === MODES.FAIL_NODE) {
        try {
          const res = await failNodeAPI(d.id)
          onUpdate?.(res.grid)
          onMessage?.(res.message)
        } catch (e) {}
      } else if (currentMode === MODES.DELETE_NODE) {
        try {
          const res = await deleteNodeAPI(d.id)
          onUpdate?.(res.grid)
          onMessage?.(res.message)
        } catch (e) {
          onMessage?.('Delete failed: ' + e.message)
        }
      } else if (currentMode === MODES.ADD_HOUSE) {
        onAddHouse?.(d.id)
      } else if (currentMode === MODES.CONNECT) {
        if (!interactionState.activeSrcNode) {
          setInteractionState({ activeSrcNode: d })
        } else {
          try {
            const res = await addEdge(interactionState.activeSrcNode.id, d.id)
            onUpdate?.(res.grid)
            onMessage?.(res.message)
          } catch (e) {
            onMessage?.('Connection failed: ' + e.message)
          } finally {
            setInteractionState({})
            root.select('.preview-link').style('opacity', 0)
          }
        }
      }
    }

    const nodeEnter = nodeSel.enter().append('g')
      .attr('class', 'node')
      .style('cursor', 'pointer')
      .call(dragHandler)
      .on('mouseover', (event, d) => {
        if (currentMode === MODES.CUT_EDGE) return
        const n = gridState.nodes[d.id] || d
        
        let faultOverlay = ''
        if (n.failed && aiState?.fault_analysis) {
          // Backend returns 'node_scores' (not 'anomaly_scores')
          const score    = aiState.fault_analysis.node_scores?.[d.id] ?? 1.0
          const alertObj = aiState.fault_analysis.alerts?.find(a => a.node_id === d.id)
          const faultType = alertObj?.fault_type?.replace('_', ' ').toUpperCase() ?? 'HARD FAILURE'
          const severity  = alertObj?.severity ?? 'CRITICAL'
          const sevColor  = severity === 'CRITICAL' ? '#ef4444' : '#eab308'
          faultOverlay = `
            <div style="margin:8px 0;padding:8px;background:rgba(239,68,68,0.12);border:1px solid #ef4444;border-radius:6px;">
              <div style="color:#ef4444;font-weight:700;font-size:12px;margin-bottom:6px;letter-spacing:0.5px;">🚨 AI FAULT DIAGNOSIS</div>
              <div style="display:flex;justify-content:space-between;margin-bottom:3px;"><span>Fault Type:</span><b style="color:${sevColor}">${faultType}</b></div>
              <div style="display:flex;justify-content:space-between;margin-bottom:3px;"><span>ANN Confidence:</span><b style="color:#ef4444">${(score * 100).toFixed(1)}%</b></div>
              <div style="display:flex;justify-content:space-between;"><span>V-State:</span><b>${n.voltage ? n.voltage.toFixed(3) : '0.000'} pu</b></div>
            </div>
          `
        }

        if (d.is_scada) {
            tip.style('opacity', 1).html(`
              <div style="font-weight:600;color:#22c55e;font-size:14px;text-align:center;">🧠 SCADA Control Center</div>
              <div style="color:#94a3b8;font-size:10px;text-align:center;">AI-Driven Autonomous Agent Stack</div>
            `)
        } else {
            const switchInfo = n.switch_type
              ? `<div style="display:flex;justify-content:space-between;margin-top:4px;"><span>Switch:</span><b style="color:${
                  n.switch_status === 'fault_locked' ? '#ef4444' :
                  n.switch_status === 'open'         ? '#94a3b8' : '#22c55e'}">${
                  n.switch_type?.toUpperCase()} — ${n.switch_status?.replace('_',' ').toUpperCase()}</b></div>`
              : ''
            tip.style('opacity', 1).html(`
              <div style="font-weight:600;margin-bottom:6px;color:#3b82f6">${d.id} — ${d.node_type}</div>
              <div style="display:flex;justify-content:space-between"><span>Volt:</span><b>${n.voltage?.toFixed(3)} V</b></div>
              <div style="display:flex;justify-content:space-between"><span>Freq:</span><b>${n.frequency?.toFixed(2)} Hz</b></div>
              <div style="display:flex;justify-content:space-between"><span>Load:</span><b>${n.load?.toFixed(2)} MW</b></div>
              <div style="display:flex;justify-content:space-between"><span>Gen:</span><b>${n.generation?.toFixed(2)} MW</b></div>
              ${switchInfo}
              <div style="margin-top:6px;font-weight:600;color:${n.failed?'#ef4444':n.isolated?'#eab308':'#22c55e'}">${n.failed?'FAILED':n.isolated?'ISOLATED':'ONLINE'}</div>
              ${faultOverlay}
            `)
        }
      })
      .on('mousemove', e => tip.style('left', (e.clientX + 16) + 'px').style('top', (e.clientY - 10) + 'px'))
      .on('mouseout',  () => tip.style('opacity', 0))

    nodeEnter.append('path').attr('class', 'main-shape')
    nodeEnter.append('text').attr('class', 'icon-text')
      .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
      .style('pointer-events', 'none')
    nodeEnter.append('text').attr('class', 'label-text')
      .attr('text-anchor', 'middle').attr('dy', 22)
      .style('font-size', '10px').style('font-family', 'JetBrains Mono')
      .style('font-weight', '600').style('fill', 'rgba(255,255,255,0.8)')
      .style('pointer-events', 'none')
      .text(d => d.id)

    const nodeMerge = nodeEnter.merge(nodeSel)

    nodeMerge
      .style('opacity', d => !selectedNode ? 1 : (connected.has(d.id) ? 1 : 0.18))
      .attr('transform', d => `translate(${d.x},${d.y})`)

    nodeMerge.select('.main-shape')
      .attr('d',    d => getShape(d.node_type))
      .attr('fill', d => {
        if ((currentMode === MODES.FAIL_NODE || currentMode === MODES.DELETE_NODE) && !d.is_scada) return '#ff3333'
        if (currentMode === MODES.CONNECT && interactionState.activeSrcNode?.id === d.id) return '#ffaa00'
        if (selectedNode === d.id) return '#33ff33'
        if (d.is_scada) return 'none' // remove background for SCADA node outline
        return getNodeColor(d)
      })
      .attr('fill-opacity', d => d.is_scada ? 0 : 1.0)
      .attr('stroke', d => d.is_scada ? '#33ff33' : '#ffffff')
      .attr('stroke-dasharray', d => d.is_scada ? '4 4' : '0')
      .attr('stroke-width', d => (selectedNode === d.id || d.is_scada) ? 2 : 0)

    nodeMerge.select('.icon-text')
      .text(d => getIcon(d.node_type))
      .style('font-size', d => d.node_type === 'substation' ? '14px' : '11px')

    // ── Voltage stress ring: physics-only indicator ─────────────────────
    // Shows amber/red pulsing halo when voltage < 0.95 pu (under-voltage stress)
    // This is driven EXCLUSIVELY by node.voltage from the backend physics engine.
    const voltageRingGroup = root.select('.nodes').selectAll('g.voltage-ring')
      .data(nodes.filter(d => !d.failed && !d.isolated && (d.voltage ?? 1.0) > 0 && (d.voltage ?? 1.0) < 0.95), d => d.id)

    const voltageRingEnter = voltageRingGroup.enter().append('g').attr('class', 'voltage-ring')
    voltageRingEnter.append('circle')
      .attr('class', 'v-ring')
      .attr('r', 16)
      .attr('fill', 'none')
      .attr('stroke-width', 2)
      .style('pointer-events', 'none')

    voltageRingEnter.merge(voltageRingGroup)
      .attr('transform', d => `translate(${d.x},${d.y})`)
      .select('.v-ring')
      .attr('stroke', d => (d.voltage ?? 1.0) < 0.90 ? '#ff4444' : '#ffcc00')
      .attr('stroke-dasharray', '4 3')
      .style('opacity', 0.85)

    voltageRingGroup.exit().remove()

    // ═══════════════════════════════════════════════════════════════════
    // STORAGE VISUALIZATION - Battery level indicator
    // ═══════════════════════════════════════════════════════════════════
    const storageGroup = root.select('.nodes').selectAll('g.storage-level')
      .data(nodes.filter(d => d.node_type === 'battery' || d.node_type === 'supercap'), d => d.id)

    const storageEnter = storageGroup.enter().append('g')
      .attr('class', 'storage-level')

    // Battery level bar background
    storageEnter.append('rect')
      .attr('class', 'storage-bar-bg')
      .attr('x', -18).attr('y', 28)
      .attr('width', 36).attr('height', 5)
      .attr('rx', 2)
      .attr('fill', 'rgba(0,0,0,0.5)')

    // Battery level bar fill
    storageEnter.append('rect')
      .attr('class', 'storage-bar-fill')
      .attr('x', -18).attr('y', 28)
      .attr('width', 36).attr('height', 5)
      .attr('rx', 2)

    // Storage label (percentage)
    storageEnter.append('text')
      .attr('class', 'storage-text')
      .attr('text-anchor', 'middle')
      .attr('dy', 39)
      .style('font-size', '8px')
      .style('font-family', 'JetBrains Mono')
      .style('fill', 'rgba(255,255,255,0.7)')
      .style('pointer-events', 'none')

    const storageMerge = storageEnter.merge(storageGroup)

    storageMerge.select('.storage-bar-fill')
      .attr('fill', d => {
        // Get battery level (0-1 range)
        const level = d.battery_level || d.supercap_level || 0.5
        // Color based on level: red (<30%) -> yellow (30-70%) -> green (>70%)
        if (level < 0.3) return '#ff3333'
        if (level < 0.7) return '#ffaa00'
        return '#33ff33'
      })
      .attr('width', d => {
        const level = d.battery_level || d.supercap_level || 0.5
        return 36 * level
      })

    storageMerge.select('.storage-text')
      .text(d => {
        const level = d.battery_level || d.supercap_level || 0.5
        return `${Math.round(level * 100)}%`
      })

    storageGroup.exit().remove()

    // ═══════════════════════════════════════════════════════════════════
    // PRIORITY LOAD VISUALIZATION - Hospital badge
    // ═══════════════════════════════════════════════════════════════════
    const hospitalGroup = root.select('.nodes').selectAll('g.hospital-priority')
      .data(nodes.filter(d => d.node_type === 'hospital'), d => d.id)

    const hospitalEnter = hospitalGroup.enter().append('g')
      .attr('class', 'hospital-priority')

    // Priority shield icon
    hospitalEnter.append('text')
      .attr('class', 'priority-badge')
      .attr('text-anchor', 'middle')
      .attr('dy', -20)
      .style('font-size', '10px')
      .style('pointer-events', 'none')
      .text('🛡️ PRI')

    const hospitalMerge = hospitalEnter.merge(hospitalGroup)

    // Make priority badge pulse when system is under stress
    hospitalMerge.select('.priority-badge')
      .attr('fill', d => {
        // If hospital is cut off, show red; otherwise green
        if (d.failed) return '#ff3333'
        if (d.isolated) return '#ffaa00'
        return '#33ff33'
      })
      .style('filter', d => {
        if (d.failed || d.isolated) return 'drop-shadow(0 0 4px #ff3333)'
        return 'drop-shadow(0 0 3px #33ff33)'
      })

    hospitalGroup.exit().remove()

    nodeSel.exit().remove()
  }, [gridState, aiState, currentMode, selectedNode, selectedEdge, interactionState])

  const svgCursor = {
    [MODES.CONNECT]:   'crosshair',
    [MODES.CUT_EDGE]:  'not-allowed',
    [MODES.DELETE_NODE]: 'not-allowed',
    [MODES.ADD_NODE]:  'crosshair',
    [MODES.FAIL_NODE]: 'pointer',
    [MODES.SELECT]:    'default',
  }[currentMode] ?? 'grab'

  // Flow status indicator config (BRIGHTENED)
  const flowStatusConfig = {
    flowing:   { color: '#33ff33', icon: '⚡', text: 'FLOWING' },
    stopped:   { color: '#ff3333', icon: '⏹', text: 'STOPPED' },
    rerouting: { color: '#ffaa00', icon: '↻', text: 'REROUTING' },
    INITIALIZING: { color: '#aaaaaa', icon: '○', text: 'INITIALIZING' },
  }
  const status = flowStatusConfig[flowStatus] || flowStatusConfig['INITIALIZING']

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden' }}>
      <svg
        ref={svgRef}
        width="100%" height="100%"
        style={{ display: 'block', cursor: svgCursor, position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 10 }}
        onContextMenu={e => e.preventDefault()}
      />
      <canvas
        ref={canvasRef}
        style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 5, pointerEvents: 'none' }}
      />
      {/* Flow Status Indicator */}
      <div style={{
        position: 'absolute',
        top: 12,
        right: 12,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '6px 12px',
        background: 'rgba(15,23,42,0.9)',
        border: `1px solid ${status.color}40`,
        borderRadius: 6,
        fontSize: 11,
        fontFamily: 'JetBrains Mono, monospace',
        color: status.color,
        boxShadow: `0 0 12px ${status.color}20`,
      }}>
        <span style={{ fontSize: 14 }}>{status.icon}</span>
        <span style={{ fontWeight: 600, letterSpacing: 0.5 }}>{status.text}</span>
      </div>
    </div>
  )
}
