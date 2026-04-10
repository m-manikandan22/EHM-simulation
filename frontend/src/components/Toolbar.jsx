import React from 'react'

export const MODES = {
  SELECT: 'SELECT',
  ADD_NODE: 'ADD_NODE',
  CONNECT: 'CONNECT',
  CUT_EDGE: 'CUT_EDGE',
  FAIL_NODE: 'FAIL_NODE',
  DELETE_NODE: 'DELETE_NODE',
  ADD_HOUSE: 'ADD_HOUSE'
}

export default function Toolbar({
  currentMode, setCurrentMode,
  addNodeType, setAddNodeType,
  running, setRunning,
  onReset, loading
}) {
  const activeStyle = (mode) => currentMode === mode ? { filter: 'brightness(1.5)', outline: '2px solid rgba(255,255,255,0.8)' } : { opacity: 0.7 }

  return (
    <div className="glass-card" style={{ padding: '10px 16px', display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16 }}>
      <div style={{ fontWeight: 600, color: 'var(--text-secondary)', fontSize: 13, letterSpacing: '1px', textTransform: 'uppercase' }}>
        CAD Mode:
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <button className="btn btn-cyan" style={activeStyle(MODES.ADD_NODE)} onClick={() => setCurrentMode(MODES.ADD_NODE)}>
          ➕ Add Node
        </button>
        <select
          value={addNodeType}
          onChange={e => setAddNodeType(e.target.value)}
          style={{
            background: 'var(--bg-deep)', color: 'var(--text-primary)',
            border: '1px solid var(--border)', borderRadius: 4, padding: '8px',
            opacity: currentMode === MODES.ADD_NODE ? 1 : 0.4
          }}
          disabled={currentMode !== MODES.ADD_NODE}
        >
          <option value="house">House</option>
          <option value="solar">Solar Farm</option>
          <option value="wind">Wind Turbine</option>
          <option value="battery">Mega Battery</option>
          <option value="supercap">Supercapacitor</option>
          <option value="transformer">Transformer</option>
          <option value="substation">Substation</option>
          <option value="generator">Generator</option>
        </select>
      </div>

      <button className="btn btn-cyan" style={activeStyle(MODES.CONNECT)} onClick={() => setCurrentMode(MODES.CONNECT)}>
        🔗 Connect
      </button>

      <button className="btn btn-red" style={activeStyle(MODES.CUT_EDGE)} onClick={() => setCurrentMode(MODES.CUT_EDGE)}>
        ✂️ Cut Wire
      </button>

      <button className="btn btn-yellow" style={activeStyle(MODES.FAIL_NODE)} onClick={() => setCurrentMode(MODES.FAIL_NODE)}>
        ⚠️ Fail / Restore
      </button>

      <div style={{ flex: 1 }}></div>

      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-green" onClick={() => setRunning(!running)}>
          {running ? '⏸️ Pause Sim' : '▶️ Run Sim'}
        </button>
      </div>
    </div>
  )
}
