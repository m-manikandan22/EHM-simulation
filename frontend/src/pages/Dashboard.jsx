/**
 * Dashboard.jsx — Main 3-panel dashboard page.
 *
 * Layout:
 *  [LEFT]  ControlPanel  — 280px fixed
 *  [CENTER] GridGraph    — flex 1
 *  [RIGHT] AIDecisionPanel — 300px fixed
 *
 * Polling:
 *  - Calls /simulate every 2 seconds (auto-step)
 *  - Allows manual pause via button
 *
 * State management:
 *  - gridState: full grid snapshot from backend
 *  - aiState: { latest: {...}, log: [...] }
 */

import React, { useState, useEffect, useCallback, useRef } from 'react'
import { simulate, getState, triggerEvent, cutEdge, resetGrid, addHouseAPI } from '../services/api'
import ControlPanel from '../components/ControlPanel'
import GridGraph from '../components/GridGraph'
import AIDecisionPanel from '../components/AIDecisionPanel'
import Toolbar, { MODES } from '../components/Toolbar'

const POLL_INTERVAL = 2000  // ms

const ACTION_BADGE_COLOR_MAP = {
  increase_generation: 'green',
  use_battery:         'blue',
  use_supercapacitor:  'cyan',
  shift_load:          'yellow',
  reroute_energy:      'purple',
}

export default function Dashboard() {
  const [gridState, setGridState]   = useState(null)
  const [aiState, setAiState]       = useState({ latest: null, log: [] })
  const [running, setRunning]       = useState(true)
  const [backendOk, setBackendOk]   = useState(null)  // null=checking, true, false
  const [statusMsg, setStatusMsg]   = useState('')
  const [selectedNode, setSelectedNode] = useState('H0')
  const [selectedEdge, setSelectedEdge] = useState(null)
  
  // CAD Mode State
  const [currentMode, setCurrentMode] = useState(MODES.SELECT)
  const [addNodeType, setAddNodeType] = useState('house')
  const [interactionState, setInteractionState] = useState({})
  const [showFlow, setShowFlow] = useState(true)
  const [faultSimMode, setFaultSimMode] = useState(false)
  const [aiAssistMode, setAiAssistMode] = useState(false)
  
  const timerRef = useRef(null)

  // ── Load initial state ─────────────────────────────────────────────
  useEffect(() => {
    getState()
      .then(s => { setGridState(s); setBackendOk(true) })
      .catch(() => setBackendOk(false))
  }, [])

  // ── AI log updater ─────────────────────────────────────────────────
  const pushToLog = useCallback((ai, timestep) => {
    if (!ai?.decision) return
    const entry = {
      timestep,
      text: ai.decision.reasoning,
      color: ai.decision.color,
      time: new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    }
    setAiState(prev => ({
      latest: { ...ai },
      log: [...prev.log.slice(-19), entry],
    }))
  }, [])

  // ── Simulation step ────────────────────────────────────────────────
  const doStep = useCallback(async () => {
    try {
      if (faultSimMode && Math.random() < 0.15) {
         // Randomly inject a storm/fault during faultSimMode simulation
         await triggerEvent('storm')
      }
      const result = await simulate()
      setGridState(result.grid)
      setBackendOk(true)
      pushToLog(result.ai, result.grid.timestep)
    } catch {
      setBackendOk(false)
    }
  }, [pushToLog, faultSimMode])

  // ── Auto-poll ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!running || !backendOk) {
      clearInterval(timerRef.current)
      return
    }
    timerRef.current = setInterval(doStep, POLL_INTERVAL)
    return () => clearInterval(timerRef.current)
  }, [running, backendOk, doStep])

  // ── Handlers from ControlPanel & Graph ─────────────────────────────
  const handleUpdate = useCallback(state => setGridState(state), [])
  const handleMessage = useCallback(msg => {
    setStatusMsg(msg)
    setTimeout(() => setStatusMsg(''), 5000)
  }, [])

  const handleFailNode = useCallback(async (nid) => {
    try {
      const result = await triggerEvent('failure', nid)
      setGridState(result.grid)
      handleMessage(result.message)
    } catch (e) {
      handleMessage('❌ Error: ' + (e?.response?.data?.detail || e.message))
    }
  }, [handleMessage])

  const handleCutEdge = useCallback(async (u, v) => {
    try {
      const result = await cutEdge(u, v)
      setGridState(result.grid)
      handleMessage(result.message)
      setSelectedEdge(null)
    } catch (e) {
      handleMessage('❌ Error: ' + (e?.response?.data?.detail || e.message))
    }
  }, [handleMessage])

  const handleReset = useCallback(async () => {
    try {
      const result = await resetGrid()
      setGridState(result)
      handleMessage('Grid Reset Successfully.')
      setCurrentMode(MODES.SELECT)
      setInteractionState({})
    } catch (e) {
      handleMessage('❌ Reset Error')
    }
  }, [handleMessage])

  const handleAddHouse = useCallback(async (node_id) => {
    try {
      const result = await addHouseAPI(node_id)
      setGridState(result.grid)
      handleMessage(result.message)
    } catch (e) {
      handleMessage('❌ Error adding house: ' + (e?.response?.data?.detail || e.message))
    }
  }, [handleMessage])

  // ── Failed node count ──────────────────────────────────────────────
  const failedCount = gridState
    ? Object.values(gridState.nodes || {}).filter(n => n.failed || n.isolated).length
    : 0

  const storm = gridState?.storm_active

  // ── Backend offline screen ─────────────────────────────────────────
  if (backendOk === false) {
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', height: '100vh', gap: 20,
        background: 'var(--bg-deep)',
      }}>
        <div style={{ fontSize: 48 }}>⚠️</div>
        <div style={{ fontSize: 20, fontWeight: 600, color: 'var(--accent-red)' }}>
          Cannot connect to backend
        </div>
        <div style={{ fontSize: 14, color: 'var(--text-secondary)', textAlign: 'center', maxWidth: 420 }}>
          Make sure the FastAPI server is running:<br />
          <code style={{ color: 'var(--accent-cyan)', background: 'rgba(0,212,255,0.08)',
            padding: '4px 10px', borderRadius: 6, display: 'inline-block', marginTop: 8 }}>
            cd backend &amp;&amp; python main.py
          </code>
        </div>
        <button
          className="btn btn-cyan"
          onClick={() => {
            setBackendOk(null)
            getState().then(s => { setGridState(s); setBackendOk(true) }).catch(() => setBackendOk(false))
          }}
        >
          🔄 Retry Connection
        </button>
      </div>
    )
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      height: '100vh', overflow: 'hidden',
      background: 'var(--bg-deep)',
    }}>

      {/* ── Top Header Bar ─────────────────────────────────────────── */}
      <header style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 24px', height: 52, flexShrink: 0,
        background: 'rgba(5,15,40,0.9)',
        borderBottom: '1px solid rgba(0,212,255,0.12)',
        backdropFilter: 'blur(12px)',
      }}>
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{
            width: 32, height: 32, borderRadius: 8,
            background: 'linear-gradient(135deg, #00d4ff, #00ff88)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 18,
          }}>⚡</div>
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: 0.5, color: 'var(--text-primary)' }}>
              AI Smart Grid
            </div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', letterSpacing: 1 }}>
              SELF-HEALING NETWORK
            </div>
          </div>
        </div>

        {/* Status badges */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {gridState && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
              T:{gridState.timestep}
            </span>
          )}
          {storm && <span className="badge badge-yellow">🌩️ STORM</span>}
          {failedCount > 0 && (
            <span className="badge badge-red pulse-red">⚠️ {failedCount} FAILED</span>
          )}
          {failedCount === 0 && !storm && (
            <span className="badge badge-green">
              <span className="pulse" style={{ width: 6, height: 6, borderRadius: '50%',
                background: 'var(--accent-green)', display: 'inline-block', marginRight: 5 }} />
              STABLE
            </span>
          )}
          <span className={`badge ${backendOk ? 'badge-cyan' : 'badge-red'}`}>
            {backendOk ? '🟢 ONLINE' : '🔴 OFFLINE'}
          </span>
        </div>

        {/* Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            className="btn btn-cyan"
            style={{ padding: '6px 14px', fontSize: 12 }}
            disabled={running}
            onClick={doStep}
          >
            ⏭ Forward Step Sequence
          </button>
        </div>
      </header>

      {/* ── Toolbar ──────────────────────────────────────────────── */}
      <div style={{ padding: '0 16px' }}>
        <Toolbar
          currentMode={currentMode} setCurrentMode={setCurrentMode}
          addNodeType={addNodeType} setAddNodeType={setAddNodeType}
          running={running} setRunning={setRunning}
          showFlow={showFlow} setShowFlow={setShowFlow}
          faultSimMode={faultSimMode} setFaultSimMode={setFaultSimMode}
          aiAssistMode={aiAssistMode} setAiAssistMode={setAiAssistMode}
          onReset={handleReset} loading={!gridState}
        />
      </div>

      {/* ── Status Message Toast ───────────────────────────────────── */}
      {statusMsg && (
        <div style={{
          position: 'fixed', top: 64, left: '50%', transform: 'translateX(-50%)',
          background: 'rgba(10,25,55,0.95)', border: '1px solid var(--border)',
          borderRadius: 10, padding: '8px 20px', fontSize: 13,
          color: 'var(--text-primary)', zIndex: 1000, maxWidth: 600,
          backdropFilter: 'blur(12px)',
          animation: 'slide-in 0.3s ease',
        }}>
          {statusMsg}
        </div>
      )}

      {/* ── Main 3-Panel Layout ────────────────────────────────────── */}
      <main style={{
        display: 'flex', flex: 1, gap: 0, overflow: 'hidden',
        padding: '12px 16px', paddingTop: 8,
      }}>

        {/* LEFT PANEL */}
        <div style={{
          width: 255, flexShrink: 0,
          background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          padding: '16px 14px',
          marginRight: 12,
          overflow: 'hidden',
          backdropFilter: 'var(--glass-blur)',
        }}>
          <ControlPanel
            gridState={gridState}
            onUpdate={handleUpdate}
            onMessage={handleMessage}
            selectedNode={selectedNode}
            setSelectedNode={setSelectedNode}
          />
        </div>

        {/* CENTER PANEL */}
        <div style={{
          flex: 1,
          background: 'var(--bg-panel)',
          border: `1px solid ${storm ? 'rgba(255,214,0,0.25)' : 'var(--border)'}`,
          borderRadius: 'var(--radius-lg)',
          overflow: 'hidden',
          backdropFilter: 'var(--glass-blur)',
          display: 'flex',
          flexDirection: 'column',
          ...(storm ? { boxShadow: '0 0 60px rgba(255,214,0,0.1)' } : {}),
        }}>
          {/* Center header */}
          <div style={{
            padding: '10px 20px',
            borderBottom: '1px solid var(--border)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <span style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600, letterSpacing: 1 }}>
              ⚡ LIVE GRID VISUALIZATION
            </span>
            <div style={{ display: 'flex', gap: 16, fontSize: 10, color: 'var(--text-muted)', flexWrap: 'wrap' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ width: 18, height: 3, background: 'var(--accent-green)', display: 'inline-block', borderRadius: 2 }} />
                Healthy Node
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ width: 18, height: 3, background: 'var(--accent-yellow)', display: 'inline-block', borderRadius: 2 }} />
                Stressed Node
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ width: 18, height: 3, background: 'var(--accent-red)', display: 'inline-block', borderRadius: 2 }} />
                Failed Node
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <svg width="18" height="6"><line x1="0" y1="3" x2="18" y2="3" stroke="var(--accent-cyan)" strokeWidth="3"/></svg>
                ⚡ Power Flow
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <svg width="18" height="6">
                  <line x1="0" y1="3" x2="18" y2="3" stroke="var(--accent-green)" strokeWidth="2" strokeDasharray="4 3"/>
                </svg>
                🧠 Control Signal
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <svg width="18" height="6"><line x1="0" y1="3" x2="18" y2="3" stroke="var(--accent-red)" strokeWidth="3"/></svg>
                🔴 Fault Segment
              </span>
            </div>
          </div>

          {/* Graph area */}
          <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
            {gridState ? (
              <GridGraph 
                gridState={gridState} 
                aiState={aiState.latest}
                selectedNode={selectedNode}
                onSelectNode={setSelectedNode}
                selectedEdge={selectedEdge}
                onSelectEdge={setSelectedEdge}
                onFailNode={handleFailNode}
                onCutEdge={handleCutEdge}
                onAddHouse={handleAddHouse}
                
                // CAD Mode Injection
                currentMode={currentMode}
                addNodeType={addNodeType}
                interactionState={interactionState}
                setInteractionState={setInteractionState}
                showFlow={showFlow}
                aiAssistMode={aiAssistMode}
                onMessage={handleMessage}
                onUpdate={handleUpdate}
              />
            ) : (
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                height: '100%', flexDirection: 'column', gap: 12,
              }}>
                <div style={{ fontSize: 32 }}>⚡</div>
                <div style={{ color: 'var(--text-muted)', fontSize: 14 }}>
                  Connecting to grid...
                </div>
              </div>
            )}
          </div>

          {/* Status Bar */}
          <div style={{
            height: 28, borderTop: '1px solid var(--border)', background: 'rgba(5,15,40,0.4)',
            display: 'flex', alignItems: 'center', padding: '0 16px', gap: 16,
            fontSize: 11, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', flexShrink: 0
          }}>
            <span>Mode: <span style={{ color: 'var(--accent-cyan)' }}>{currentMode}</span></span>
            <span>|</span>
            <span>Selected: <span style={{ color: selectedNode ? 'var(--accent-green)' : 'inherit' }}>{selectedNode || 'NONE'}</span></span>
          </div>
        </div>

        {/* RIGHT PANEL */}
        <div style={{
          width: 295, flexShrink: 0,
          background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          padding: '16px 14px',
          marginLeft: 12,
          overflow: 'hidden',
          backdropFilter: 'var(--glass-blur)',
        }}>
          <AIDecisionPanel aiState={aiState} gridState={gridState} />
        </div>

      </main>
    </div>
  )
}
