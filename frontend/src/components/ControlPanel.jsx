/**
 * ControlPanel.jsx — Left panel with simulation control buttons.
 *
 * Buttons:
 *  - Increase Demand      → triggerEvent('demand')
 *  - Trigger Storm        → triggerEvent('storm')
 *  - Simulate Node Failure → triggerEvent('failure', selectedNode)
 *  - Adjust Generation    → triggerEvent('generation')
 *  - Reset Grid           → resetGrid()
 *
 * Also shows: selected node picker, live system stats, and storage levels.
 */

import React, { useState } from 'react'
import { triggerEvent, resetGrid, randomFaultAPI, failNodeAPI, restoreNodeAPI } from '../services/api'

export default function ControlPanel({ gridState, onUpdate, onMessage, selectedNode, setSelectedNode }) {
  const [loading, setLoading] = useState(false)

  const nodes = gridState?.nodes || {}
  const system = gridState?.system || {}

  const call = async (fn, label) => {
    if (loading) return
    setLoading(true)
    try {
      const result = await fn()
      onUpdate(result.grid || result)
      if (result.message) onMessage(result.message)
    } catch (e) {
      onMessage('❌ Error: ' + (e?.response?.data?.detail || e.message))
    } finally {
      setLoading(false)
    }
  }

  // Bar helper
  const Bar = ({ value, color }) => (
    <div className="progress-bar" style={{ marginTop: 4 }}>
      <div
        className="progress-fill"
        style={{ width: `${Math.round(value * 100)}%`, background: color }}
      />
    </div>
  )

  const healthPct = Math.round((system.health_score || 0) * 100)
  const healthColor =
    healthPct > 70 ? 'var(--accent-green)' :
    healthPct > 40 ? 'var(--accent-yellow)' : 'var(--accent-red)'

  // Get selected node state for contextual actions
  const selectedNodeData = nodes[selectedNode]
  const isNodeFailed = selectedNodeData?.failed
  const isNodeIsolated = selectedNodeData?.isolated

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, height: '100%', overflowY: 'auto', paddingRight: 4 }}>

      {/* Header */}
      <div>
        <div className="panel-title">🎮 Simulation Controls</div>

        {/* Action Buttons */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <button
            className="btn btn-yellow"
            disabled={loading}
            onClick={() => call(() => randomFaultAPI(), 'Random Fault')}
          >
            ⚠️ Random Fault
          </button>

          <button
            className="btn btn-yellow"
            disabled={loading}
            onClick={() => call(() => triggerEvent('demand', null, 0.3), 'Demand')}
          >
            📈 Increase Demand
          </button>



          <button
            className="btn btn-green"
            disabled={loading}
            onClick={() => call(() => triggerEvent('generation', null, 0.4), 'Generation')}
          >
            ⚡ Boost Generation
          </button>

          <button
            className="btn btn-purple"
            disabled={loading}
            onClick={() => call(() => resetGrid(), 'Reset')}
          >
            🔄 Reset Grid
          </button>
        </div>
      </div>

      {/* Node Selector */}
      <div>
        <div className="section-title">Target Node</div>
        <div className="node-selector">
          {Object.keys(nodes)
            .sort()
            .map(nid => {
            const n = nodes[nid]
            const isFailed = n?.failed || n?.isolated
            return (
              <button
                key={nid}
                className={`node-btn ${selectedNode === nid ? 'active' : ''} ${isFailed ? 'failed' : ''}`}
                onClick={() => setSelectedNode(nid)}
              >
                {nid}
              </button>
            )
          })}
        </div>
      </div>

      {/* Contextual Node Actions - Show only relevant inverse actions */}
      {selectedNodeData && (
        <div className="glass-card" style={{ padding: '12px 16px', background: 'rgba(100,180,255,0.08)', border: '1px solid rgba(100,180,255,0.2)' }}>
          <div className="section-title">⚙️ Node Actions</div>

          <button
            className={`btn ${selectedNodeData.failed ? 'btn-cyan' : 'btn-red'}`}
            disabled={loading}
            onClick={async () => {
              if (selectedNodeData.failed) {
                await call(() => restoreNodeAPI(selectedNode), `Restore ${selectedNode}`);
              } else {
                await call(() => failNodeAPI(selectedNode), `Fail ${selectedNode}`);
              }

              // 🔥 FORCE REAL UPDATE
              const sim = await simulate();
              onUpdate(sim.grid);
            }}
          >
            {selectedNodeData.failed ? '🔧 Restore Node' : '⚠️ Fail Node'}
          </button>
        </div>
      )}

      {/* System Stats */}
      <div className="glass-card" style={{ padding: '14px 16px' }}>
        <div className="panel-title">📊 System Stats</div>

        <div className="metric-row">
          <span className="metric-label">Total Generation</span>
          <span className="metric-value" style={{ color: 'var(--accent-green)' }}>
            {(system.total_generation || 0).toFixed(2)} MW
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Total Load</span>
          <span className="metric-value" style={{ color: 'var(--accent-yellow)' }}>
            {(system.total_load || 0).toFixed(2)} MW
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Balance</span>
          <span
            className="metric-value"
            style={{ color: (system.balance || 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}
          >
            {(system.balance || 0) >= 0 ? '+' : ''}{(system.balance || 0).toFixed(2)} MW
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Avg Voltage</span>
          <span className="metric-value" style={{ color: 'var(--accent-cyan)' }}>
            {(system.avg_voltage || 0).toFixed(3)} p.u.
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Avg Frequency</span>
          <span className="metric-value" style={{ color: 'var(--accent-cyan)' }}>
            {(system.avg_frequency || 0).toFixed(2)} Hz
          </span>
        </div>
        {/* ✅ FIX: separate failed poles vs no-power houses */}
        {(() => {
          const nodesArr = Object.entries(nodes)
          const failedPoles = nodesArr.filter(([, n]) => (n.node_type === 'pole') && n.failed).length
          const noPowerHouses = nodesArr.filter(([, n]) => (n.node_type === 'house') && n.isolated).length
          return (
            <>
              <div className="metric-row">
                <span className="metric-label">🚨 Failed Poles</span>
                <span className="metric-value" style={{ color: failedPoles > 0 ? 'var(--accent-red)' : 'var(--accent-green)' }}>
                  {failedPoles}
                </span>
              </div>
              <div className="metric-row">
                <span className="metric-label">🏠 No-Power Houses</span>
                <span className="metric-value" style={{ color: noPowerHouses > 0 ? 'var(--accent-yellow)' : 'var(--accent-green)' }}>
                  {noPowerHouses}
                </span>
              </div>
            </>
          )
        })()}

        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span className="metric-label">Grid Health</span>
            <span className="metric-value" style={{ color: healthColor }}>{healthPct}%</span>
          </div>
          <Bar value={system.health_score || 0} color={healthColor} />
        </div>
      </div>

      {/* Selected Node Detail */}
      {nodes[selectedNode] && (
        <div className="glass-card" style={{ padding: '14px 16px' }}>
          <div className="panel-title">🔍 Node: {selectedNode}</div>
          {(() => {
            const n = nodes[selectedNode]
            return (
              <>
                <div className="metric-row">
                  <span className="metric-label">Status</span>
                  <span className={`badge badge-${n.failed ? 'red' : n.isolated ? 'yellow' : 'green'}`}>
                    {n.failed ? 'FAILED' : n.isolated ? 'ISOLATED' : 'ONLINE'}
                  </span>
                </div>
                <div className="metric-row">
                  <span className="metric-label">Voltage</span>
                  <span className="metric-value">{n.voltage.toFixed(3)} p.u.</span>
                </div>
                <div className="metric-row">
                  <span className="metric-label">Load / Gen</span>
                  <span className="metric-value">{n.load.toFixed(2)} / {n.generation.toFixed(2)} MW</span>
                </div>
                <div style={{ marginTop: 10 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span className="metric-label">🔋 Battery</span>
                    <span className="metric-value" style={{ color: 'var(--accent-blue)' }}>
                      {Math.round(n.battery_level * 100)}%
                    </span>
                  </div>
                  <Bar value={n.battery_level} color="var(--accent-blue)" />
                </div>
                <div style={{ marginTop: 8 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span className="metric-label">⚡ Supercap</span>
                    <span className="metric-value" style={{ color: 'var(--accent-cyan)' }}>
                      {Math.round(n.supercap_level * 100)}%
                    </span>
                  </div>
                  <Bar value={n.supercap_level} color="var(--accent-cyan)" />
                </div>
              </>
            )
          })()}
        </div>
      )}
    </div>
  )
}
