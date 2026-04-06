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
import { triggerEvent, resetGrid } from '../services/api'

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
            onClick={() => call(() => triggerEvent('demand', null, 0.3), 'Demand')}
          >
            📈 Increase Demand
          </button>

          <button
            className="btn btn-red"
            disabled={loading || gridState?.storm_active}
            onClick={() => call(() => triggerEvent('storm'), 'Storm')}
          >
            🌩️ Trigger Storm
          </button>

          {gridState?.storm_active && (
            <button
              className="btn btn-cyan"
              disabled={loading}
              onClick={() => call(() => triggerEvent('clear_storm'), 'Clear Storm')}
            >
              ☀️ Clear Storm
            </button>
          )}

          <button
            className="btn btn-red"
            disabled={loading}
            onClick={() => call(() => triggerEvent('failure', selectedNode), 'Failure')}
          >
            ⚠️ Fail Node: {selectedNode}
          </button>

          <button
            className="btn btn-cyan"
            disabled={loading}
            onClick={() => call(() => triggerEvent('restore', selectedNode), 'Restore')}
          >
            🔧 Restore Node: {selectedNode}
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

      <div>
        <div className="section-title">✨ Presets (Demo Ready)</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <button className="btn btn-red" disabled={loading}
            onClick={() => call(() => triggerEvent('storm'), 'Storm Triggered')}>
            🌩️ Storm Scenario
          </button>
          <button className="btn btn-yellow" disabled={loading}
            onClick={() => call(() => triggerEvent('failure', 'S0'), 'S0 Failed')}>
            ⚡ Substation Failure
          </button>
          <button className="btn btn-purple" disabled={loading}
            onClick={() => call(() => triggerEvent('demand', null, 0.6), 'Spike')}>
            📈 High Demand Event
          </button>
        </div>
      </div>

      {/* Node Selector */}
      <div>
        <div className="section-title">Target Node</div>
        <div className="node-selector">
          {Object.keys(nodes)
            .filter(n => n.startsWith('G') || n.startsWith('S'))
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
