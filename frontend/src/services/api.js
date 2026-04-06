/**
 * api.js — Axios service layer for the Smart Grid backend API.
 * All calls go to http://localhost:8000 (FastAPI).
 */

import axios from 'axios'

const BASE_URL = 'http://localhost:8000'

const api = axios.create({
  baseURL: BASE_URL,
  timeout: 10000,
  headers: { 'Content-Type': 'application/json' },
})

// ── Grid operations ───────────────────────────────────────────────────

/** Get current grid state without stepping the simulation. */
export const getState = () => api.get('/state').then(r => r.data)

/**
 * Advance 1 timestep: runs grid physics, LSTM forecast, DQN decision.
 * Returns { grid, ai: { predicted_load, decision, action_result, reward } }
 */
export const simulate = () => api.post('/simulate').then(r => r.data)

/** Reset grid to initial state. */
export const resetGrid = () => api.post('/reset').then(r => r.data)

// ── User Grid Construction (CAD Modes) ──────────────────────────────────

export const addNode = (type, position) => api.post('/add_node', { type, position }).then(r => r.data)
export const addEdge = (u, v) => api.post('/connect', { source: u, target: v }).then(r => r.data)
export const cutEdge = (u, v) => api.post('/cut_edge', { source: u, target: v }).then(r => r.data)
export const failNodeAPI = (node_id) => api.post('/fail_node', { node_id }).then(r => r.data)
export const restoreNodeAPI = (node_id) => api.post('/restore_node', { node_id }).then(r => r.data)
export const moveNodeAPI = (node_id, x, y) => api.put(`/nodes/${node_id}/move`, { x, y }).then(r => r.data)
export const addHouseAPI = (node_id) => api.post('/command/add_house', { node_id }).then(r => r.data)
export const deleteNodeAPI = (node_id) => api.delete(`/nodes/${node_id}`).then(r => r.data)

export const getAISuggestions = () => api.get('/ai/suggestions').then(r => r.data)
export const getSuggestParent = (x, y) => api.post('/ai/suggest_parent', { x, y }).then(r => r.data)

// ── Events ────────────────────────────────────────────────────────────

/**
 * Trigger a grid event.
 * @param {string} type - 'failure' | 'storm' | 'clear_storm' | 'demand' | 'generation' | 'restore'
 * @param {string|null} nodeId - required for 'failure' and 'restore'
 * @param {number|null} amount - optional for 'demand' and 'generation'
 */
export const triggerEvent = (type, nodeId = null, amount = null) =>
  api.post('/event', { type, node_id: nodeId, amount }).then(r => r.data)

// ── Prediction ────────────────────────────────────────────────────────

/** Get LSTM prediction for a specific node. */
export const predict = (nodeId = 'S0') =>
  api.get('/predict', { params: { node_id: nodeId } }).then(r => r.data)

// ── Manual action ─────────────────────────────────────────────────────

/** Force a specific RL action by ID (0–4). */
export const forceAction = (actionId) =>
  api.post('/action', { action_id: actionId }).then(r => r.data)

// ── Utility ───────────────────────────────────────────────────────────

/** Health check. */
export const healthCheck = () =>
  api.get('/health').then(r => r.data).catch(() => null)
