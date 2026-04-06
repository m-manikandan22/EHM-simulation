# AI Self-Healing Smart Grid — README

## Project Structure

```
simulation/
├── backend/
│   ├── main.py              # FastAPI entry point
│   ├── requirements.txt
│   ├── models/
│   │   ├── lstm_model.py    # LSTM demand forecaster (PyTorch)
│   │   └── rl_agent.py      # DQN reinforcement learning agent
│   ├── simulation/
│   │   ├── grid.py          # NetworkX smart grid simulation
│   │   └── node.py          # GridNode with hybrid storage
│   └── api/
│       └── routes.py        # All FastAPI endpoints
└── frontend/
    ├── package.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── App.jsx
        ├── index.css         # Dark-mode design system
        ├── main.jsx
        ├── components/
        │   ├── GridGraph.jsx     # D3.js force-directed graph
        │   ├── ControlPanel.jsx  # Left panel controls
        │   └── AIDecisionPanel.jsx # Right panel AI display
        ├── pages/
        │   └── Dashboard.jsx     # 3-panel main layout
        └── services/
            └── api.js            # Axios API wrapper
```

---

## 🚀 Quick Start

### Step 1 — Backend

```cmd
cd c:\Users\ELCOT\Music\TNWISE\simulation\backend

:: Install Python dependencies (only once)
pip install fastapi uvicorn[standard] numpy torch networkx scikit-learn pydantic python-multipart

:: Start the server
python main.py
```

Expected output:
```
AI Self-Healing Smart Grid — Backend Starting Up
[1/3] Initialising smart grid simulation...
[2/3] Loading LSTM demand forecaster...
[LSTM] Training complete. Final loss: 0.00xxxx
[3/3] Loading DQN reinforcement learning agent...
[DQN] Warm-up complete.
✅ All systems ready.
Uvicorn running on http://0.0.0.0:8000
```

### Step 2 — Frontend

```cmd
cd c:\Users\ELCOT\Music\TNWISE\simulation\frontend

:: Install JS dependencies (only once)
npm install

:: Start the dev server
npm run dev
```

Open: **http://localhost:5173**

---

## 🌐 API Endpoints

| Method | Endpoint    | Description |
|--------|-------------|-------------|
| GET    | `/state`    | Current grid state |
| POST   | `/simulate` | Step simulation + LSTM + DQN |
| POST   | `/event`    | Trigger failure / storm / demand |
| GET    | `/predict`  | LSTM forecast |
| POST   | `/action`   | Force RL action |
| POST   | `/reset`    | Reset grid |
| GET    | `/health`   | Health check |

### Example — Trigger Storm
```cmd
curl -X POST http://localhost:8000/event -H "Content-Type: application/json" -d "{\"type\":\"storm\"}"
```

### Example — Fail a Node
```cmd
curl -X POST http://localhost:8000/event -H "Content-Type: application/json" -d "{\"type\":\"failure\",\"node_id\":\"H2\"}"
```

---

## 🎮 Dashboard Controls

| Button | Effect |
|--------|--------|
| 📈 Increase Demand | Spikes load +0.3 MW on all house nodes |
| 🌩️ Trigger Storm | Weather event: load ×1.35, gen ×0.6 |
| ⚠️ Fail Node | Marks selected node as failed, triggers self-healing |
| 🔧 Restore Node | Brings node back online |
| ⚡ Boost Generation | Adds +0.4 MW to all substation generators |
| 🔄 Reset Grid | Returns to initial state |
| ⏸ Pause / ▶ Resume | Toggle auto-simulation (2s interval) |
| ⏭ Step | Manual single timestep when paused |

---

## 🧠 AI Architecture

### LSTM Forecaster
- 2-layer LSTM, hidden_size=32, CPU-only
- Input: `[load, generation, weather] × 10 timesteps`
- Output: predicted next-step demand (MW)
- Pre-trains on 500 synthetic samples at startup (~2 seconds)

### DQN Agent
- MLP: 40-dim state → 64 → 64 → 5 actions
- State: `[voltage, freq/50, load, generation, stress] × 8 nodes`
- Actions: increase_gen | use_battery | use_supercapacitor | shift_load | reroute_energy
- ε-greedy exploration (decays 1.0 → 0.05 over 200 steps)
- Target network synced every 20 steps

### Self-Healing Logic
1. Node failure injected via `inject_failure(node_id)`
2. All connecting edges disabled
3. BFS from substations checks all remaining nodes for connectivity
4. Disconnected nodes marked as `isolated`
5. DQN responds with `reroute_energy` action to reactivate cross-links

---

## ⚙️ Requirements

- Python ≥ 3.9
- Node.js ≥ 18
- No GPU required — all ML runs on CPU
