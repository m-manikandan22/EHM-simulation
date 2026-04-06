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
  // 🔴 Faulted — the root cause node
  if (node.failed) return '#ef4444'
  // 🟡 Isolated but healthy — cut off by sectionalization, awaiting tie restoration
  if (node.isolated) return '#f59e0b'
  if (node.is_scada)  return 'none'
  // 🟢 Energized — layer-based hierarchy colours
  switch (node.node_type) {
    // Generation Layer
    case 'generator':           return '#f59e0b'  // Amber  — Generic Generator
    case 'generator_solar':     return '#eab308'  // Yellow — Solar Farm
    case 'generator_wind':      return '#bfdbfe'  // Light blue — Wind Farm
    case 'generator_nuclear':   return '#10b981'  // Emerald — Nuclear Plant
    case 'generator_coal':      return '#374151'  // Gray-700 — Coal Plant
    case 'generator_gas':       return '#f97316'  // Orange — Gas Turbine
    // Legacy types
    case 'solar':               return '#eab308'
    case 'wind':                return '#bfdbfe'
    // Storage Layer
    case 'battery':             return '#a855f7'  // Purple — Storage
    case 'supercap':            return '#ec4899'  // Pink   — Fast response
    // Distribution
    case 'step_up':             return '#f97316'  // Orange — Step-Up Tx
    case 'substation':          return '#6366f1'  // Indigo — Transmission mesh
    case 'transformer':         return '#3b82f6'  // Blue   — Step-Down Tx
    case 'pole':                return '#06b6d4'  // Cyan   — Distribution Pole
    case 'switch':              return '#8b5cf6'  // Purple — Protection zone
    case 'service':             return '#06b6d4'  // Cyan   — Service Tx
    // Load Layer
    case 'house':               return '#22c55e'  // Green  — Residential
    case 'hospital':            return '#dc2626'  // Red    — Critical Load
    case 'industry':            return '#7c3aed'  // Violet — Industrial
    case 'commercial':          return '#0ea5e9'  // Sky    — Commercial
    default:                    return '#94a3b8'
  }
}


function getEdgeColor(edge) {
  // switch_status-aware color logic:
  // fault_locked  → 🔴 red   (opened by protection relay)
  // tie closed    → 🟢 green (FLISR restored this path)
  // rerouted      → 🟡 amber (NEW PATH after FLISR)
  // broken        → ⚫ dark red (OLD PATH that failed)
  // tie open      → grey dashed (standby — handled via stroke-dasharray)
  // normal flow   → blue
  // idle          → subdued

  // 🔴 Fault/broken path (original fault segment)
  if (edge.switch_status === 'fault_locked') return '#ef4444'
  if (edge.status === 'broken') return '#7f1d1d'  // Dark red for broken

  // 🟡 Rerouted path (NEW path after FLISR tie switch)
  if (edge.status === 'rerouted') return '#f59e0b'  // Amber/gold for rerouted

  // 🟢 Tie switch closed (FLISR restored)
  if (edge.is_tie_switch && edge.active)     return '#22c55e'

  // ⚫ Inactive edge
  if (!edge.active)                          return 'rgba(148,163,184,0.25)'

  // 🔵 Normal flow
  if (edge.flow && Math.abs(edge.flow) > 0.05) return '#3b82f6'

  return 'rgba(148,163,184,0.4)'             // idle — subdued
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
  showFlow,
  aiAssistMode,
  onMessage,
  onUpdate,
}) {
  const svgRef  = useRef(null)
  const dataRef = useRef({ nodes: [], links: [] })
  const zoomRef = useRef(null)
  const [suggestions, setSuggestions] = useState([])
  const [flowStatus, setFlowStatus] = useState('flowing') // flowing, stopped, rerouting, no-flow

  // ── Keyboard: Delete / Backspace removes selected edge ──────────
  useEffect(() => {
    const onKey = (e) => {
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedEdge && onCutEdge)
        onCutEdge(selectedEdge.u, selectedEdge.v)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedEdge, onCutEdge])

  // ── AI Assist Mode Fetch ──────────────────────────────────────────
  useEffect(() => {
    if (!aiAssistMode) {
      setSuggestions([])
      return
    }
    const fetchSugg = async () => {
      try {
        const res = await getAISuggestions()
        setSuggestions(res.suggestions || [])
      } catch (e) {}
    }
    fetchSugg()
    const int = setInterval(fetchSugg, 10000)
    return () => clearInterval(int)
  }, [aiAssistMode])

  // ── Flow Status Detection ───────────────────────────────────────────
  useEffect(() => {
    if (!gridState) return
    const nodes = Object.values(gridState.nodes || {})
    const failedNodes = nodes.filter(n => n.failed || n.isolated)
    const activePaths = gridState.active_paths || []
    const hasFault = failedNodes.length > 0 || gridState.storm_active
    const hasActiveFlow = activePaths.length > 0

    if (hasFault) {
      setFlowStatus(hasActiveFlow ? 'rerouting' : 'stopped')
    } else {
      setFlowStatus(hasActiveFlow ? 'flowing' : 'no-flow')
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
      .attr('stroke', d =>
        currentMode === MODES.CUT_EDGE ? '#ef4444' : getEdgeColor(d)
      )
      .attr('stroke-width', d => {
        if (!d.active) return 1
        
        let baseWidth = 1.5;
        if (d.source.node_type === 'generator' || d.source.node_type === 'substation' || d.target.node_type === 'substation') baseWidth = 5.0;
        else if (d.source.node_type === 'transformer' || d.target.node_type === 'transformer') baseWidth = 4.0;
        else if (d.source.id.startsWith('P') && d.target.id.startsWith('P')) baseWidth = 4.0; // trunk lines
        else if (d.source.node_type === 'house' || d.target.node_type === 'house') baseWidth = 1.2; // service drops
        else baseWidth = 2.5; // laterals

        // Give a tiny bump based on flow to feel alive, but respect the static architectural hierarchy mostly
        return Math.max(baseWidth, (baseWidth * 0.8) + Math.abs(d.flow || 0) * 0.3)
      })
      .style('stroke-dasharray', d => {
        if (d.status === 'broken') return '10 5'      // Long dash for broken
        if (d.status === 'rerouted') return 'none'   // Solid for rerouted (glowing)
        if (d.is_tie_switch) return '6 4'
        if (d.source.node_type === 'house' || d.target.node_type === 'house') return '3 3'
        return 'none'
      })
      .style('filter', d => {
        // Glowing effect for rerouted paths
        if (d.status === 'rerouted') return 'drop-shadow(0 0 6px #f59e0b)'
        if (d.status === 'broken') return 'drop-shadow(0 0 3px #7f1d1d)'
        return 'none'
      })
      .attr('class', d => {
        // switch_status state machine → CSS class
        if (d.switch_status === 'fault_locked') return 'wire fault-segment'
        if (d.status === 'rerouted')            return 'wire reroute-active'     // 🟡 FLISR new path
        if (d.status === 'broken')              return 'wire reroute-broken'     // 🔴 Old fault path
        if (d.is_tie_switch && d.active)        return 'wire tie-switch-closed'  // 🟢 FLISR restored
        if (d.is_tie_switch && !d.active)       return 'wire tie-switch-open'    // ⚪ standby
        // Non-switch faults (blown cable)
        if (!d.active && (d.source.failed || d.target.failed)) return 'wire fault-segment'
        return 'wire'
      })
      .attr('marker-end', d =>
        d.active && !d.source.failed && !d.target.failed && Math.abs(d.flow || 0) > 0.01
          ? (d.flow > 0 ? 'url(#arrow-blue)' : 'url(#arrow-purple)')
          : null
      )

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
          .attr('fill',   'rgba(239,68,68,0.07)')
          .attr('stroke', '#ef4444')
          .attr('stroke-width', 1.5)
          .attr('stroke-dasharray', '6 3')
          .style('pointer-events', 'none')
      }
    }

    // ═══════════════════════════════════════════════════════════════════
    // SYSTEM-LEVEL CONTINUOUS FLOW ANIMATION (NEW IMPLEMENTATION)
    // ═══════════════════════════════════════════════════════════════════

    // Step 1: Build adjacency graph from active paths
    function buildFlowGraph(activePaths) {
      const graph = {}
      const edgeFlows = {}

      if (!activePaths || activePaths.length === 0) return { graph, edgeFlows }

      activePaths.forEach(path => {
        const { from, to, flow } = path
        if (!graph[from]) graph[from] = []
        graph[from].push(to)
        edgeFlows[`${from}|${to}`] = flow
      })

      return { graph, edgeFlows }
    }

    // Step 2: EDGE-BASED FLOW - iterate ALL edges with flow, not route paths
    function getEdgesWithFlow(links, edgeFlows) {
      const flowEdges = []
      links.forEach(edge => {
        const flowKey = `${edge.source.id}|${edge.target.id}`
        const flow = edgeFlows[flowKey] || edge.flow || 0

        // Include edge if:
        // 1. It has meaningful flow, OR
        // 2. It's a storage device (battery/supercap) that's active, OR
        // 3. It's connected to a generator
        const isStorage = edge.source.node_type === 'battery' || edge.target.node_type === 'battery' ||
                          edge.source.node_type === 'supercap' || edge.target.node_type === 'supercap'
        const isGenerator = edge.source.node_type?.startsWith('generator') || edge.target.node_type?.startsWith('generator')

        if (Math.abs(flow) > 0.01 || isStorage || isGenerator) {
          flowEdges.push({
            edge,
            flow,
            source: edge.source,
            target: edge.target
          })
        }
      })
      return flowEdges
    }

    // Step 3: Get color based on source type and flow direction
    function getSourceColor(sourceId, nodeMap, flow = 0, isSourceBattery = false) {
      const node = nodeMap[sourceId]
      if (!node) return '#3b82f6' // Default blue

      // FIX 3: Storage flow direction - distinguish charging vs discharging
      // Battery discharging (flow > 0 leaving battery) = purple
      // Battery charging (flow < 0 entering battery) = pink
      if (node.node_type === 'battery') {
        return flow > 0 ? '#a855f7' : '#ec4899' // Purple (discharge) vs Pink (charge)
      }
      if (node.node_type === 'supercap') {
        return flow > 0 ? '#8b5cf6' : '#f472b6' // Purple (discharge) vs Pink (charge)
      }

      // Multi-source color coding
      switch (node.node_type) {
        case 'generator_solar': return '#eab308' // 🟡 Solar - Yellow
        case 'generator_wind': return '#3b82f6'  // 🔵 Wind - Blue
        case 'generator_nuclear': return '#10b981' // 🟢 Nuclear - Green
        case 'generator_coal':
        case 'generator_gas':
        case 'generator': return '#f97316'      // 🟠 Fossil - Orange
        default: return '#3b82f6'
      }
    }

    // Get active paths from grid state
    const activePaths = gridState.active_paths || []
    const { graph: flowGraph, edgeFlows } = buildFlowGraph(activePaths)

    // Identify sources (generators, battery) and loads (houses, hospital, industry)
    const sources = new Set()
    const loads = new Set()
    const nodeMap = {}

    nodes.forEach(n => {
      nodeMap[n.id] = n
      if (n.node_type?.startsWith('generator') || n.node_type === 'battery') {
        sources.add(n.id)
      }
      if (['house', 'hospital', 'industry', 'commercial'].includes(n.node_type)) {
        loads.add(n.id)
      }
    })

    // EDGE-BASED FLOW: Get all edges with flow (not route-based)
    const autoShowFlow = showFlow || (activePaths.length > 0)
    const flowEdges = autoShowFlow ? getEdgesWithFlow(links, edgeFlows) : []

    // Draw particles on each edge with flow
    const flowRouteSel = root.select('.links').selectAll('g.flow-route')
      .data(flowEdges, (d) => `${d.source.id}-${d.target.id}`)

    const flowRouteEnter = flowRouteSel.enter().append('g')
      .attr('class', 'flow-route')

    // Process each edge with flow
    flowEdges.forEach((flowEdge, idx) => {
      const { edge, flow, source, target } = flowEdge

      // Skip faulted/inactive edges - CRITICAL for fault visualization
      if (!edge.active || edge.status === 'fault_locked' || edge.status === 'broken') {
        return // Don't spawn particles on broken edges
      }

      // Build simple edge path "M x1 y1 L x2 y2"
      const pathD = `M ${source.x} ${source.y} L ${target.x} ${target.y}`

      // Determine color based on source type and storage flow direction (FIX 3)
      // Flow > 0: source -> target (discharging if source is battery)
      // Flow < 0: target -> source (charging if target is battery)
      let color
      const isSourceStorage = source.node_type === 'battery' || source.node_type === 'supercap'
      const isTargetStorage = target.node_type === 'battery' || target.node_type === 'supercap'

      if (isSourceStorage) {
        // Battery/supercap is SOURCE = DISCHARGING (flow leaving storage)
        color = getSourceColor(source.id, nodeMap, flow, true)
      } else if (isTargetStorage) {
        // Battery/supercap is TARGET = CHARGING (flow entering storage)
        // Use negative flow to indicate charging direction
        color = getSourceColor(source.id, nodeMap, -flow, false) // Get color based on what's feeding the storage
      } else {
        // Normal edge - use source color
        color = getSourceColor(source.id, nodeMap)
      }

      // FLOW MAGNITUDE determines particle count (FIX 2)
      // More flow = more particles, less flow = fewer particles
      const absFlow = Math.abs(flow) || 0.1 // Default minimum for edges without explicit flow
      const particleCount = Math.max(2, Math.min(30, Math.round(absFlow * 15)))

      // Animation speed based on flow magnitude
      const baseDur = 6.0
      const duration = absFlow > 0 ? Math.max(1.5, baseDur - absFlow * 0.3) : baseDur

      // Direction: flow > 0 = source->target, flow < 0 = target->source
      const forward = flow >= 0

      // Select this specific edge group
      const edgeGroup = flowRouteEnter.filter((d, i) => i === idx)

      // Add invisible path for animation reference
      edgeGroup.append('path')
        .attr('class', 'flow-path-reference')
        .attr('d', pathD)
        .attr('fill', 'none')
        .attr('stroke', 'none')
        .attr('id', `edge-path-${source.id}-${target.id}`)

      // Spawn particles on THIS edge (FIX 1: edge-based, not route-based)
      for (let p = 0; p < particleCount; p++) {
        // VARIABLE SPEED - quantum spread effect
        const speedVariation = 0.6 + Math.random() * 0.8 // 0.6 to 1.4x
        const particleDuration = duration / speedVariation

        // RANDOM START POSITION - organic flow
        const randomStart = Math.random() * particleDuration

        // VARIABLE SIZE - depth effect
        const sizeVariation = 1.5 + Math.random() * 4 // 1.5-5.5px radius

        // QUANTUM SPREAD (FIX 5): Add position offset randomness
        // This creates organic, non-linear particle movement
        const offsetX = (Math.random() - 0.5) * 8 // ±4px offset
        const offsetY = (Math.random() - 0.5) * 8

        // Add flowing particle with offset for quantum spread
        const particle = edgeGroup.append('circle')
          .attr('class', 'flow-particle')
          .attr('r', sizeVariation)
          .attr('fill', color)
          .attr('opacity', 0.6 + Math.random() * 0.4)
          .style('filter', `drop-shadow(0 0 ${3 + Math.random() * 5}px ${color})`)

        // SMIL animation with direction consideration
        if (forward) {
          particle.append('animateMotion')
            .attr('dur', `${particleDuration}s`)
            .attr('repeatCount', 'indefinite')
            .attr('path', pathD)
            .attr('rotate', 'auto')
            .attr('begin', `-${randomStart}s`)
        } else {
          // Reverse direction for negative flow
          particle.append('animateMotion')
            .attr('dur', `${particleDuration}s`)
            .attr('repeatCount', 'indefinite')
            .attr('path', pathD)
            .attr('rotate', 'auto')
            .attr('begin', `-${randomStart}s`)
            .attr('keyPoints', '1;0')
            .attr('keyTimes', '0;1')
            .attr('calcMode', 'linear')
        }

        // Add trailing glow for high-flow edges
        if (absFlow > 0.3 && p % 4 === 0) {
          const trailDelay = randomStart + (particleDuration * 0.15)
          const trail = edgeGroup.append('circle')
            .attr('class', 'flow-particle-trail')
            .attr('r', 1.2)
            .attr('fill', color)
            .attr('opacity', 0.3)
            .style('filter', `drop-shadow(0 0 2px ${color})`)

          if (forward) {
            trail.append('animateMotion')
              .attr('dur', `${particleDuration}s`)
              .attr('repeatCount', 'indefinite')
              .attr('path', pathD)
              .attr('begin', `-${trailDelay}s`)
          } else {
            trail.append('animateMotion')
              .attr('dur', `${particleDuration}s`)
              .attr('repeatCount', 'indefinite')
              .attr('path', pathD)
              .attr('begin', `-${trailDelay}s`)
              .attr('keyPoints', '1;0')
              .attr('keyTimes', '0;1')
              .attr('calcMode', 'linear')
          }
        }
      }
    })

    // Update existing flow edges
    flowRouteSel.exit().remove()

    // ═══════════════════════════════════════════════════════════════════
    // NODE-LEVEL FLOW EFFECTS (Continuity & Merge)
    // ═══════════════════════════════════════════════════════════════════
    const nodeFlowGroup = root.select('.flow-nodes')
    if (nodeFlowGroup.empty()) {
      root.append('g').attr('class', 'flow-nodes')
    }

    // Build adjacency for flow continuity
    const outgoingEdges = {}
    links.forEach(l => {
      if (l.active && Math.abs(l.flow || 0) > 0.01) {
        if (!outgoingEdges[l.source.id]) outgoingEdges[l.source.id] = []
        outgoingEdges[l.source.id].push({ target: l.target.id, flow: l.flow, edge: l })
      }
    })

    // Get nodes with multiple incoming flows (merge points)
    const incomingCounts = {}
    flowEdges.forEach(fe => {
      incomingCounts[fe.target.id] = (incomingCounts[fe.target.id] || 0) + 1
    })

    // Substation merge effect - pulsing glow when multiple flows converge
    const substations = nodes.filter(n => n.node_type === 'substation' && incomingCounts[n.id] > 1)
    const substationPulseSel = nodeFlowGroup.selectAll('circle.substation-pulse')
      .data(substations, d => d.id)

    substationPulseSel.enter().append('circle')
      .attr('class', 'substation-pulse')
      .attr('r', 25)
      .attr('fill', 'none')
      .attr('stroke', '#6366f1')
      .attr('stroke-width', 2)
      .attr('opacity', 0)
      .merge(substationPulseSel)
      .attr('cx', d => d.x)
      .attr('cy', d => d.y)
      .each(function(d) {
        const count = incomingCounts[d.id] || 0
        d3.select(this)
          .transition()
          .duration(800)
          .attr('r', 25 + count * 8)
          .attr('opacity', 0.3 + count * 0.1)
          .transition()
          .duration(800)
          .attr('r', 25)
          .attr('opacity', 0)
          .on('end', function() {
            d3.select(this).transition().duration(1600).attr('opacity', 0.3 + count * 0.1).attr('r', 25 + count * 8).transition().duration(800).attr('opacity', 0).attr('r', 25).on('end', function() { d3.select(this).call(arguments.callee) })
          })
      })
    substationPulseSel.exit().remove()

    // Storage burst effect - dramatic discharge from battery
    const storageNodes = nodes.filter(n =>
      (n.node_type === 'battery' || n.node_type === 'supercap') &&
      outgoingEdges[n.id] && outgoingEdges[n.id].length > 0
    )

    // Find edges with flow FROM storage (discharging)
    const storageDischargingEdges = flowEdges.filter(fe =>
      (fe.source.node_type === 'battery' || fe.source.node_type === 'supercap') &&
      fe.flow > 0
    )

    // Draw burst rings from discharging storage
    const storageBurstSel = nodeFlowGroup.selectAll('circle.storage-burst')
      .data(storageDischargingEdges, d => `${d.source.id}-${d.target.id}`)

    storageBurstSel.enter().append('circle')
      .attr('class', 'storage-burst')
      .attr('fill', 'none')
      .attr('stroke', '#a855f7')
      .attr('stroke-width', 3)
      .attr('opacity', 0.8)
      .merge(storageBurstSel)
      .attr('cx', d => d.source.x)
      .attr('cy', d => d.source.y)
      .attr('r', 15)
      .each(function(d) {
        const el = d3.select(this)
        el.transition()
          .duration(600)
          .attr('r', 40)
          .attr('opacity', 0)
          .on('end', function() {
            d3.select(this).attr('r', 15).attr('opacity', 0.8).transition().duration(600).attr('r', 40).attr('opacity', 0).call(arguments.callee)
          })
      })
    storageBurstSel.exit().remove()

    // ═══════════════════════════════════════════════════════════════════
    // REROUTE GLOW EFFECT - recently activated edges
    // ═══════════════════════════════════════════════════════════════════
    const reroutedEdges = links.filter(l =>
      l.status === 'rerouted' && l.active
    )

    const rerouteGlowSel = root.select('.links')
      .selectAll('path.reroute-glow')
      .data(reroutedEdges, edgeKey)

    rerouteGlowSel.enter().append('path')
      .attr('class', 'reroute-glow')
      .attr('fill', 'none')
      .attr('stroke', '#f59e0b')
      .attr('stroke-width', 8)
      .attr('stroke-linecap', 'round')
      .attr('opacity', 0.6)
      .attr('filter', 'drop-shadow(0 0 12px #f59e0b)')
      .merge(rerouteGlowSel)
      .attr('d', d => `M${d.source.x},${d.source.y} L${d.target.x},${d.target.y}`)
      .attr('opacity', d => {
        // Fade glow over time - check if edge was recently rerouted
        const age = gridState.timestep - (d.rerouted_at || gridState.timestep)
        return Math.max(0, 0.6 - age * 0.02)
      })
    rerouteGlowSel.exit().remove()

    // ═══════════════════════════════════════════════════════════════════
    // END SYSTEM-LEVEL FLOW ANIMATION
    // ═══════════════════════════════════════════════════════════════════

    // ── SCADA Control Signal overlay on SAME physical paths ──────────
    // Determines which edge types receive control signals based on AI action
    const scadaGroup = root.select('.scada-layer')
    let controlEdges = []
    if (aiState?.decision) {
      const act = aiState.decision.action_name
      controlEdges = links.filter(d => {
        if (!d.active || d.source.failed || d.target.failed) return false
        if (act === 'use_battery' || act === 'shift_load' || act === 'use_supercapacitor')
          return d.target.node_type === 'house' || d.source.node_type === 'house'
        if (act === 'increase_generation')
          return d.source.node_type === 'generator' || d.target.node_type === 'generator' ||
                 d.source.node_type === 'step_up'   || d.target.node_type === 'step_up'
        if (act === 'reroute_energy')
          return d.source.node_type === 'switch' || d.target.node_type === 'switch'
        return false
      })
    }

    const ctrlSel = scadaGroup.selectAll('path.ctrl-overlay').data(controlEdges, edgeKey)
    ctrlSel.enter().append('path')
      .attr('class', 'ctrl-overlay control-overlay')
      .attr('fill', 'none')
      .attr('stroke-linecap', 'round')
      .style('pointer-events', 'none')
      .merge(ctrlSel)
      .attr('d', pathD)
    ctrlSel.exit().remove()

    // ── AI Suggestion Overlays (purple dotted paths with interactive labels) ───────────────
    const suggSel = scadaGroup.selectAll('path.ai-sugg').data(suggestions, d => `${d.source}|${d.target}`)
    
    suggSel.enter().append('path')
      .attr('class', 'ai-sugg pulse')
      .style('pointer-events', 'auto')
      .attr('fill', 'none')
      .attr('stroke', '#a855f7')
      .attr('stroke-width', 5)
      .attr('stroke-dasharray', '8 6')
      .style('opacity', 0.8)
      .style('cursor', 'help')
      .on('mouseover', (event, d) => {
        tip.style('opacity', 1).html(`
          <div style="font-weight:700;color:#a855f7;font-size:12px;margin-bottom:4px;">💡 AI TIE-LINE SUGGESTION</div>
          <div style="display:flex;justify-content:space-between;margin-bottom:3px;font-size:11px;">
            <span>Connection:</span><b style="font-family:JetBrains Mono">${d.source} ↔ ${d.target}</b>
          </div>
          <div style="color:#e8f4ff;font-size:11px;line-height:1.4;">${d.reason}</div>
        `)
      })
      .on('mousemove', e => tip.style('left', (e.clientX + 16) + 'px').style('top', (e.clientY - 10) + 'px'))
      .on('mouseout', () => tip.style('opacity', 0))
      .merge(suggSel)
      .attr('d', d => {
        const s = nodes.find(n => n.id === d.source)
        const t = nodes.find(n => n.id === d.target)
        if (!s || !t) return ''
        return `M${s.x},${s.y} L${t.x},${t.y}`
      })
    suggSel.exit().remove()

    // Interactive badges at midpoint of suggestions
    const suggBadge = scadaGroup.selectAll('g.ai-sugg-badge').data(suggestions, d => `${d.source}|${d.target}`)
    const suggBadgeEnter = suggBadge.enter().append('g').attr('class', 'ai-sugg-badge')
    suggBadgeEnter.append('rect')
      .attr('fill', 'rgba(168,85,247,0.95)')
      .attr('rx', 4).attr('ry', 4)
      .style('pointer-events', 'none')
      .attr('stroke', '#ffffff').attr('stroke-width', 1)
    suggBadgeEnter.append('text')
      .attr('fill', 'white')
      .attr('font-size', '9px')
      .attr('font-family', 'Inter')
      .attr('font-weight', 'bold')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'central')
      .style('pointer-events', 'none')
      .text('💡 NEW TIE')
    
    suggBadgeEnter.merge(suggBadge)
      .attr('transform', d => {
        const s = nodes.find(n => n.id === d.source)
        const t = nodes.find(n => n.id === d.target)
        if (!s || !t) return 'translate(-9999,-9999)'
        return `translate(${(s.x + t.x)/2}, ${(s.y + t.y)/2})`
      })
      .each(function() {
        const text = d3.select(this).select('text')
        const bbox = text.node().getBBox()
        d3.select(this).select('rect')
          .attr('x', bbox.x - 5)
          .attr('y', bbox.y - 3)
          .attr('width', bbox.width + 10)
          .attr('height', bbox.height + 6)
      })
    suggBadge.exit().remove()

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
        d.x = event.x
        d.y = event.y
        d3.select(this).attr('transform', `translate(${d.x},${d.y})`)
        root.selectAll('path.wire').filter(l => l.source.id === d.id || l.target.id === d.id)
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
        if ((currentMode === MODES.FAIL_NODE || currentMode === MODES.DELETE_NODE) && !d.is_scada) return '#ef4444'
        if (currentMode === MODES.CONNECT && interactionState.activeSrcNode?.id === d.id) return '#eab308'
        if (selectedNode === d.id) return '#22c55e'
        if (d.is_scada) return 'none' // remove background for SCADA node outline
        return getNodeColor(d)
      })
      .attr('fill-opacity', d => d.is_scada ? 0 : 1.0)
      .attr('stroke', d => d.is_scada ? '#22c55e' : '#ffffff')
      .attr('stroke-dasharray', d => d.is_scada ? '4 4' : '0')
      .attr('stroke-width', d => (selectedNode === d.id || d.is_scada) ? 2 : 0)

    nodeMerge.select('.icon-text')
      .text(d => getIcon(d.node_type))
      .style('font-size', d => d.node_type === 'substation' ? '14px' : '11px')

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
        if (level < 0.3) return '#ef4444'
        if (level < 0.7) return '#eab308'
        return '#22c55e'
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
        if (d.failed) return '#ef4444'
        if (d.isolated) return '#f59e0b'
        return '#22c55e'
      })
      .style('filter', d => {
        if (d.failed || d.isolated) return 'drop-shadow(0 0 4px #ef4444)'
        return 'drop-shadow(0 0 3px #22c55e)'
      })

    hospitalGroup.exit().remove()

    nodeSel.exit().remove()
  }, [gridState, aiState, currentMode, selectedNode, selectedEdge, interactionState, showFlow])

  const svgCursor = {
    [MODES.CONNECT]:   'crosshair',
    [MODES.CUT_EDGE]:  'not-allowed',
    [MODES.DELETE_NODE]: 'not-allowed',
    [MODES.ADD_NODE]:  'crosshair',
    [MODES.FAIL_NODE]: 'pointer',
    [MODES.SELECT]:    'default',
  }[currentMode] ?? 'grab'

  // Flow status indicator config
  const flowStatusConfig = {
    flowing:   { color: '#22c55e', icon: '⚡', text: 'FLOWING' },
    stopped:   { color: '#ef4444', icon: '⏹', text: 'STOPPED' },
    rerouting: { color: '#f59e0b', icon: '↻', text: 'REROUTING' },
    'no-flow': { color: '#6b7280', icon: '○', text: 'NO FLOW' },
  }
  const status = flowStatusConfig[flowStatus] || flowStatusConfig['no-flow']

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <svg
        ref={svgRef}
        width="100%" height="100%"
        style={{ display: 'block', cursor: svgCursor }}
        onContextMenu={e => e.preventDefault()}
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
