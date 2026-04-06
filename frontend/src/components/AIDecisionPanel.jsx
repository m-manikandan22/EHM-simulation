/**
 * AIDecisionPanel.jsx — Right panel showing LSTM predictions and DQN decisions.
 *
 * Displays:
 *  - Predicted demand gauge
 *  - Current AI action with colour-coded badge
 *  - Human-readable reasoning string
 *  - Scrollable decision log (last 20 entries)
 *  - Agent stats (ε, reward, timestep)
 */

import React, { useRef, useEffect, useState } from 'react'
import { getAISuggestions } from '../services/api'

const ACTION_COLORS = {
  green:  'badge-green',
  blue:   'badge-blue',
  cyan:   'badge-cyan',
  yellow: 'badge-yellow',
  purple: 'badge-purple',
  red:    'badge-red',
}

function SystemMetricsCard({ gridState, faultAnalysis }) {
  if (!gridState) return null
  const nodes = Object.values(gridState.nodes || {}).filter(n => !n.is_scada)
  
  const houseNodes = nodes.filter(n => n.node_type === 'house' && n.priority === 3)
  const activeHouses = houseNodes.filter(n => (n.voltage || 0) > 0.1).length
  
  const hospNode = nodes.find(n => n.priority === 1)
  const hospActive = hospNode && (hospNode.voltage || 0) > 0.1
  
  const vsi = faultAnalysis?.system_health ? (faultAnalysis.system_health * 100).toFixed(1) : 100.0

  return (
    <div className="glass-card" style={{ padding: '16px', marginBottom: '16px', border: '1px solid var(--border)' }}>
      <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 0.5, color: '#f8fafc', marginBottom: 12 }}>
        ⚡ GRID HEALTH: {vsi}%
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 10px', background: hospActive ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)', borderRadius: 4, border: `1px solid ${hospActive ? 'var(--accent-green)' : 'var(--accent-red)'}` }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>🏥 Hospital</span>
          <b style={{ fontSize: 13, color: hospActive ? 'var(--accent-green)' : 'var(--accent-red)' }}>
            {hospActive ? 'ACTIVE' : 'OFFLINE'}
          </b>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 4 }}>
          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>🏠 Houses Served</span>
          <b style={{ fontSize: 13, color: activeHouses === houseNodes.length ? 'var(--accent-cyan)' : 'var(--accent-yellow)' }}>
            {activeHouses} / {houseNodes.length}
          </b>
        </div>
      </div>
    </div>
  )
}

function DemandGauge({ predicted, current }) {
  const pct = Math.min(100, Math.round((predicted / 2.0) * 100))
  const cPct = Math.min(100, Math.round((current / 2.0) * 100))
  const color =
    pct > 75 ? 'var(--accent-red)' :
    pct > 50 ? 'var(--accent-yellow)' : 'var(--accent-green)'

  return (
    <div className="glass-card" style={{ padding: '16px' }}>
      <div className="panel-title">🧠 LSTM Demand Forecast</div>

      {/* Circular gauge (SVG) */}
      <div style={{ display: 'flex', justifyContent: 'center', margin: '4px 0 12px' }}>
        <svg width="120" height="80" viewBox="0 0 120 80">
          {/* Background arc */}
          <path
            d="M 10,70 A 50,50 0 0,1 110,70"
            fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="10" strokeLinecap="round"
          />
          {/* Value arc */}
          {pct > 0 && (
            <path
              d="M 10,70 A 50,50 0 0,1 110,70"
              fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
              strokeDasharray="157"
              strokeDashoffset={157 - (pct / 100) * 157}
              style={{ transition: 'stroke-dashoffset 0.8s ease, stroke 0.5s' }}
            />
          )}
          {/* Label */}
          <text x="60" y="65" textAnchor="middle" fill={color}
            fontSize="18" fontFamily="JetBrains Mono" fontWeight="600">
            {predicted.toFixed(2)}
          </text>
          <text x="60" y="78" textAnchor="middle" fill="rgba(255,255,255,0.35)"
            fontSize="9" fontFamily="Inter">
            MW
          </text>
        </svg>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span className="metric-label">Current Avg Load</span>
        <span className="metric-value" style={{ color: 'var(--text-secondary)' }}>
          {current.toFixed(2)} MW
        </span>
      </div>

      <div style={{ marginTop: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <span className="metric-label">Predicted Load</span>
          <span style={{ fontSize: 11, color }}>
            {pct > 70 ? '🔴 HIGH' : pct > 40 ? '🟡 MODERATE' : '🟢 LOW'}
          </span>
        </div>
        <div className="progress-bar">
          <div className="progress-fill"
            style={{ width: `${pct}%`, background: color }} />
        </div>
      </div>
    </div>
  )
}

function FLISRDecisionLog({ flisrLog }) {
  if (!flisrLog || flisrLog.length === 0) return null

  const statusStyle = {
    ok:     { border: 'var(--accent-green)',  bg: 'rgba(34,197,94,0.07)',   icon: '✅' },
    warn:   { border: 'var(--accent-yellow)', bg: 'rgba(234,179,8,0.07)',   icon: '⚠️' },
    reject: { border: 'var(--accent-red)',    bg: 'rgba(239,68,68,0.07)',   icon: '❌' },
    info:   { border: 'var(--accent-cyan)',   bg: 'rgba(0,212,255,0.05)',   icon: '🔹' },
  }

  const stepColors = {
    LOCATE:   '#f59e0b',
    ISOLATE:  '#ef4444',
    CLUSTER:  '#8b5cf6',
    EVALUATE: '#3b82f6',
    RESTORE:  '#22c55e',
    METRICS:  '#ec4899',
  }

  return (
    <div style={{ padding: '14px', borderRadius: 10, background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.08)' }}>
      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.5px', color: 'var(--text-muted)', marginBottom: 10 }}>
        ⚡ FLISR DECISION LOG — THOUGHT PROCESS
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {flisrLog.map((entry, i) => {
          const s = statusStyle[entry.status] || statusStyle.info
          const stepColor = stepColors[entry.step] || 'var(--accent-cyan)'
          return (
            <div key={i} style={{
              display: 'flex', gap: 8, alignItems: 'flex-start',
              padding: '7px 10px', borderRadius: 7,
              background: s.bg, borderLeft: `2px solid ${s.border}`,
            }}>
              <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                <span style={{ fontSize: 9, fontWeight: 800, color: stepColor,
                               background: 'rgba(0,0,0,0.3)', borderRadius: 3, padding: '1px 4px',
                               letterSpacing: '0.3px', fontFamily: 'JetBrains Mono' }}>
                  {entry.step}
                </span>
              </div>
              <span style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                {entry.detail}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function FaultAnalysisCard({ faultAnalysis }) {

  if (!faultAnalysis) return null

  const { alerts = [], system_health = 1.0, fault_count = 0 } = faultAnalysis
  
  const healthPct = Math.round(system_health * 100)
  const healthColor = healthPct > 85 ? 'var(--accent-green)' : healthPct > 60 ? 'var(--accent-yellow)' : 'var(--accent-red)'

  return (
    <div className="glass-card" style={{ padding: '16px' }}>
      <div className="panel-title" style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span>🔍 AI Fault Detection</span>
        <span style={{ color: healthColor, fontSize: 13 }}>
          Health: {healthPct}%
        </span>
      </div>

      {alerts.length === 0 ? (
        <div style={{ padding: '12px', background: 'rgba(0,255,136,0.05)', borderRadius: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 16 }}>✅</span>
          <span style={{ fontSize: 12, color: 'var(--accent-green)' }}>Grid Operating Normally. No anomalies detected.</span>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {alerts.map((alert, idx) => (
            <div key={idx} style={{
              padding: '10px 12px',
              background: alert.severity === 'CRITICAL' ? 'rgba(239, 68, 68, 0.1)' : 
                          alert.severity === 'HIGH' ? 'rgba(234, 179, 8, 0.1)' : 'rgba(59, 130, 246, 0.1)',
              borderRadius: 8,
              borderLeft: `2px solid ${alert.severity === 'CRITICAL' ? 'var(--accent-red)' : 
                                      alert.severity === 'HIGH' ? 'var(--accent-yellow)' : 'var(--accent-blue)'}`
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                  {alert.node_id} - {alert.fault_type.replace('_', ' ').toUpperCase()}
                </span>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Score: {alert.score.toFixed(2)}</span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{alert.message}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function DecisionCard({ decision }) {
  if (!decision) return null
  const badgeClass = ACTION_COLORS[decision.color] || 'badge-cyan'

  return (
    <div className="glass-card" style={{ padding: '16px' }}>
      <div className="panel-title">🤖 DQN Decision</div>

      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <span className={`badge ${badgeClass}`} style={{ marginTop: 2, flexShrink: 0 }}>
          ACT-{decision.action_id}
        </span>
        <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)', lineHeight: 1.4 }}>
          {decision.label}
        </span>
      </div>

      <div style={{
        marginTop: 12, padding: '10px 12px',
        background: 'rgba(0,212,255,0.05)',
        borderRadius: 8, borderLeft: '2px solid var(--accent-cyan)'
      }}>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Reasoning</div>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          {decision.reasoning}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16, marginTop: 12 }}>
        <div>
          <div className="metric-label">Exploration ε</div>
          <div className="metric-value" style={{ color: 'var(--accent-purple)', fontSize: 12 }}>
            {(decision.epsilon || 0).toFixed(3)}
          </div>
        </div>
        <div>
          <div className="metric-label">Action</div>
          <div className="metric-value" style={{ color: 'var(--accent-cyan)', fontSize: 12 }}>
            {decision.action_name}
          </div>
        </div>
        {decision.confidence !== undefined && (
          <div>
            <div className="metric-label">Confidence</div>
            <div className="metric-value" style={{ color: decision.confidence > 0.8 ? 'var(--accent-green)' : 'var(--accent-yellow)', fontSize: 12 }}>
              {Math.round(decision.confidence * 100)}%
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function AIArchitectCard({ timestep }) {
  const [suggestions, setSuggestions] = useState([])
  const [loading, setLoading] = useState(false)
  
  const fetchSuggestions = async () => {
    setLoading(true)
    try {
      const res = await getAISuggestions()
      setSuggestions(res.suggestions || [])
    } catch { 
    } finally {
      setLoading(false)
    }
  }

  // Poll exactly every 50 timesteps to avoid spanning backend excessively, or on component mount
  useEffect(() => {
    if (!timestep || timestep % 50 === 0) fetchSuggestions()
  }, [timestep])

  return (
    <div className="glass-card" style={{ padding: '16px', marginBottom: '16px', border: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span className="panel-title" style={{ margin: 0, fontSize: 13 }}>💡 AI Architect</span>
        <button className="btn btn-cyan" onClick={fetchSuggestions} disabled={loading} style={{ padding: '2px 6px', fontSize: 10 }}>Scan</button>
      </div>
      
      {suggestions.length === 0 ? (
        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Grid topology is structurally stable. No critical single-points of failure detected.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {suggestions.map((s, i) => (
            <div key={i} style={{ padding: '8px 10px', background: 'rgba(234,179,8,0.1)', borderRadius: 6, borderLeft: '2px solid var(--accent-yellow)' }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent-yellow)', marginBottom: 4 }}>SUGGEST TIE LINE</div>
              <div style={{ fontSize: 12, color: 'var(--text-primary)', marginBottom: 2, fontFamily: 'JetBrains Mono' }}>{s.source} ↔ {s.target}</div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.4 }}>{s.reason}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function AIDecisionPanel({ aiState, gridState }) {
  const logRef = useRef(null)

  const system = gridState?.system || {}
  const currentLoad = (system.total_load || 0) / Math.max(
    Object.keys(gridState?.nodes || {}).length, 1
  )

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = 0
    }
  }, [aiState?.log?.length])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, height: '100%', overflowY: 'auto', paddingRight: 4 }}>

      {/* System Pulse Metrics Array */}
      <SystemMetricsCard gridState={gridState} faultAnalysis={aiState?.latest?.fault_analysis} />
      
      {/* AI Topological Suggestions */}
      <AIArchitectCard timestep={gridState?.timestep} />

      {/* Header */}
      <div>
        <div className="panel-title">🔵 AI Decision Engine</div>

        {/* Status pill */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <div className={`pulse`} style={{
            width: 8, height: 8, borderRadius: '50%',
            background: 'var(--accent-green)',
          }} />
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            LSTM + DQN Active — Step {gridState?.timestep || 0}
          </span>
        </div>
      </div>

      {/* Demand Gauge */}
      <DemandGauge
        predicted={aiState?.latest?.predicted_load || 0.5}
        current={currentLoad}
      />

      {/* AI Fault Detection Card */}
      {aiState?.latest?.fault_analysis && (
        <FaultAnalysisCard faultAnalysis={aiState.latest.fault_analysis} />
      )}

      {/* Decision Card */}
      <DecisionCard decision={aiState?.latest?.decision} />

      {/* FLISR / Action Result — Prominent Reasoning Card */}
      {aiState?.latest?.action_result && (() => {
        const result = aiState.latest.action_result
        const isFlisr   = result.includes('FLISR')
        const isSuccess = result.includes('✅')
        const isWarning = result.includes('⚠️')

        // Parse tie info from FLISR result string
        const tieMatch  = result.match(/via tie (\S+)/)
        const vMatch    = result.match(/V≈([\d.]+)/)
        const hmMatch   = result.match(/Headroom (\d+)%/)
        const mwMatch   = result.match(/([\d.]+) MW/)

        const borderColor = isSuccess ? 'var(--accent-green)' : isWarning ? 'var(--accent-yellow)' : 'rgba(255,255,255,0.1)'
        const bgColor     = isSuccess ? 'rgba(34,197,94,0.07)' : isWarning ? 'rgba(234,179,8,0.07)' : 'rgba(255,255,255,0.03)'

        return (
          <div style={{
            padding: '14px', borderRadius: 10,
            background: bgColor,
            border: `1px solid ${borderColor}`,
          }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.5px', color: 'var(--text-muted)', marginBottom: 8 }}>
              {isFlisr ? '⚡ SCADA FLISR DECISION' : '🤖 CONTROL ACTION'}
            </div>

            {/* Main result text */}
            <div style={{ fontSize: 12, color: isSuccess ? 'var(--accent-green)' : isWarning ? 'var(--accent-yellow)' : 'var(--text-secondary)', lineHeight: 1.6, marginBottom: isFlisr ? 10 : 0 }}>
              {result}
            </div>

            {/* FLISR metrics grid */}
            {isFlisr && (tieMatch || vMatch || hmMatch) && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 8, paddingTop: 8, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                {tieMatch && (
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Tie Closed</div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--accent-cyan)', fontFamily: 'JetBrains Mono' }}>{tieMatch[1]}</div>
                  </div>
                )}
                {vMatch && (
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Voltage</div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: parseFloat(vMatch[1]) >= 0.95 ? 'var(--accent-green)' : 'var(--accent-yellow)', fontFamily: 'JetBrains Mono' }}>
                      {vMatch[1]} pu
                    </div>
                  </div>
                )}
                {hmMatch && (
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Headroom</div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: parseInt(hmMatch[1]) > 30 ? 'var(--accent-green)' : 'var(--accent-yellow)', fontFamily: 'JetBrains Mono' }}>
                      {hmMatch[1]}%
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })()}


      {/* FLISR Decision Log — step-by-step AI reasoning */}
      {aiState?.latest?.flisr_log?.length > 0 && (
        <FLISRDecisionLog flisrLog={aiState.latest.flisr_log} />
      )}

      {/* Reward */}
      {aiState?.latest?.reward !== undefined && (
        <div className="glass-card" style={{ padding: '12px 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div>
              <div className="metric-label">Last Reward</div>
              <div className="metric-value" style={{
                color: aiState.latest.reward >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                fontSize: 16, marginTop: 4,
              }}>
                {aiState.latest.reward >= 0 ? '+' : ''}{aiState.latest.reward.toFixed(2)}
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div className="metric-label">Energy Loss</div>
              <div className="metric-value" style={{ color: 'var(--accent-yellow)', fontSize: 16, marginTop: 4 }}>
                {(system.total_energy_loss || 0).toFixed(2)} MWh
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Event Log — merged AI log + grid physical events */}
      <div>
        <div className="section-title">📋 Event Log</div>
        {/* Grid physical events (FLISR breaker ops, relay trips) */}
        {(gridState?.recent_events || []).length > 0 && (
          <div style={{ marginBottom: 8 }}>
            {(gridState.recent_events).map((evt, i) => {
              const isIsolate = evt.includes('ISOLATE') || evt.includes('trip')
              const isRestore = evt.includes('✅') || evt.includes('Restored')
              const color = isIsolate ? 'var(--accent-red)' : isRestore ? 'var(--accent-green)' : 'var(--accent-yellow)'
              return (
                <div key={i} style={{
                  padding: '6px 10px', marginBottom: 4,
                  background: isIsolate ? 'rgba(239,68,68,0.06)' : isRestore ? 'rgba(34,197,94,0.06)' : 'rgba(234,179,8,0.06)',
                  borderRadius: 6, borderLeft: `2px solid ${color}`,
                  fontSize: 11, color,
                }}>
                  {evt}
                </div>
              )
            })}
          </div>
        )}
        <div className="log-scroll" ref={logRef}>
          {(aiState?.log || []).slice().reverse().map((entry, i) => (
            <div
              key={i}
              className="slide-in"
              style={{
                padding: '8px 12px',
                background: 'rgba(255,255,255,0.03)',
                borderRadius: 8,
                borderLeft: `2px solid ${entry.color || 'var(--accent-cyan)'}`,
                fontSize: 11,
                color: 'var(--text-secondary)',
                lineHeight: 1.5,
              }}
            >
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 2 }}>
                <span className={`badge ${ACTION_COLORS[entry.color] || 'badge-cyan'}`} style={{ fontSize: 9 }}>
                  T{entry.timestep}
                </span>
                <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>{entry.time}</span>
              </div>
              <div>{entry.text}</div>
            </div>
          ))}
          {(!aiState?.log || aiState.log.length === 0) && (
            <div style={{ color: 'var(--text-muted)', fontSize: 12, textAlign: 'center', padding: 20 }}>
              Awaiting simulation data...
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
