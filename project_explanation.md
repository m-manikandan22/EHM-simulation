# Cyber-Physical System (CPS) Smart Grid Simulation
## Comprehensive Technical Architecture & In-Depth File-by-File Workflow Analysis

This document provides a highly exhaustive, in-depth architectural map and line-by-line workflow analysis of the entire TNWISE Smart Grid Simulation project. It traces the fundamental logic from the core physics algorithms and deep reinforcement learning layers in the backend Python engine, down to the multi-particle SVG rendering and glassmorphism interface on the frontend React UI.

---

## 1. System Architecture & Information Pipeline

The application structurally maps directly to a classic 3-tier Cyber-Physical architecture:
1. **Physical Layer (Grid Physics)**: Simulated inside `grid.py` and `node.py`. Evaluates real-world electrical laws (Kirchhoff’s laws, DC forward/backward sweeps) over a strict mathematical grid spanning a 1500x920 topology.
2. **Cyber/Control Layer (SCADA, FLISR, EMS, AI Orchestrator)**: Evaluated in `scada.py`, `ems_optimizer.py`, `rl_agent.py`, `fault_detector.py`, and `lstm_model.py`. Uses Artificial Intelligence (DQN, ANN, LSTM) and Linear Programming to monitor telemetry, predict load, classify anomalies, and autonomously issue control signals (FLISR tie-switch closures or load shedding).
3. **Visualization Layer (React + D3)**: Built with `App.jsx`, `GridGraph.jsx`, and `AIDecisionPanel.jsx`. Constantly loops against the FastAPI backend, deserializing JSON telemetry to map constraints into exact SVG vectors, animate node statuses, and visually trace RL Agent logic.

---

## 2. Backend Orchestration & API Routing

### 2.1 `main.py` (FastAPI Application Entry Point)
The core server lifecycle manager.
* **Lines [36-64] (`lifespan`)**: Uses the FastAPI standard Async Context Manager to initialize the massive singletons precisely once upon server start. 
  * Bootstraps the `SmartGrid` (physical layer), `EnergyManagementSystem` (EMS layer), and `ScadaControlCenter` (AI layer). 
  * Calls `app.state.scada.warmup_ai(g)` physically pre-training the Reinforcement Learning agent rapidly before standard network requests are processed.
* **Lines [79-86] (`CORSMiddleware`)**: Standard CORS injection bridging the React dev ports explicitly to the secure Python endpoints.

### 2.2 `api/routes.py` (The Interface Surface)
Translates HTTP polling from the frontend into precise simulation ticks.
* **Dependency Handlers [28-48]**: Uses FastAPI `Request` injection. By pulling modules dynamically via `request.app.state`, the application avoids race conditions from mutable global variables module-wide.
* **Lines [110-166] (`POST /simulate`)**: The Master Event Loop. Locked behind a strict ThreadLock `with grid_lock:`.
    1. `grid.update_generation()`: Updates solar/wind capacities based on time metrics.
    2. `ems.run(grid)`: PyPSA balances baseline load storage logic prior to calculating sweeping distributions.
    3. `grid.update_power_flow()`: Breadth-First-Search (BFS) physics execution across current graphs.
    4. `scada.execute_control_loop()`: Executes the intelligence block (Fault monitoring, RL Agent choice, FLISR).
* **Lines [175-260] (Interactive Builders)**: Exposes precise graph manipulation functions (`add_user_node`, `connect`, `cut_edge`, `fail_node`) mapping directly to underlying `networkx` topological mutations on `grid.py`.

---

## 3. Physical Layer (Grid Physics & Mathematics)

### 3.1 `simulation/grid.py` (The Physics Core)
Represents the exact physical topography of the network using `networkx`. 
* **Initialization Block & Time Profiles**: Declares constant arrays (`SOLAR_CURVE`, `WIND_CURVE`, `LOAD_CURVE`). These dynamically scale structural properties throughout the artificial 24-hour cycle.
* **Grid Building (`_build_grid`)**: Instantiates the complete 1500x920 node hierarchy split into Base Generation (Zone 1), Substation Storage (Zone 2), and Radial Feeders (Zone 3). Lateral branches inject Sectionalizers natively to mimic real grid protection schema.
* **`_isolate_fault_segments`**: Real relays. When nodes are `.failed`, it spans out via `nx.node_connected_component`, locating active reclosers bounding the affected array, mathematically blocking pathways (`switch_status = "fault_locked"`).
* **`_simulate_energy_flow`**:
    * **Stage 1 (Backward Sweep)**: Iterates off a conceptual `SUPER_SOURCE` root, spanning upwards from leaf transformers aggregating net limits (Load vs Generation).
    * **Stage 2 (Forward Sweep)**: Propagates voltage down from slack. Evaluates resistive drop: `V_drop = (|P| * R) / V_parent`. Prosumer generation injecting upwards adjusts voltage locally mirroring precise physical constraints. Will trip branches explicitly (`"active" = False`) if limits burst thermal constants.

### 3.2 `simulation/ems_optimizer.py` (Optimal Unit Commitment)
* **Optimization Setup**: Leverages `pypsa` and `linopy`. Generates a simplified single-bus abstraction of the grid mapped over variable time.
* **`_pypsa_optimize` [163-286]**: Builds dynamic mathematical constraints mapping Solar/Wind (Cost $0), Battery storage units (state tracking, Cost $5), and bulk Fossil power (Cost $50). 
* **Solving**: Dynamically iterations over Solvers (`"highs"`, `"glpk"`, `"cbc"`) utilizing Dual-Simplex Matrix processing to mathematically identify the cheapest possible unit dispatch combinations satisfying the net load.
* **Fallback Matrix**: If LP optimization fails mathematically, defaults back to strict priority queues allocating base battery draws linearly.

---

## 4. Cyber Layer (SCADA, FLISR & AI Models)

### 4.1 `simulation/scada.py` (The Central Nervous System)
* **Lines [51-145] (`execute_control_loop`)**: The central synchronization point.
  1. Polls raw graph objects mapping them into clean vectors.
  2. Submits telemetry into AI layer (`fault_detector`, `lstm`).
  3. Proactively infers (`_predict_overloads`) looking for overloads in local ratios.
  4. Polls `DQNAgent` for strategic choices.
  5. Implements the *Simulation Relay Delay* (it applies the previous cycle's action currently to model realistic communication lagging, queuing the newly generated action for the future).
* **Lines [235-580] (`_flisr_restore` - Fault Location, Isolation & Service Restoration)**:
  * Uses component block graph techniques (`nx.connected_components`) across un-energized domains mapping isolated local grids.
  * Filters Tie-Line candidates requiring paths to consist strictly of viable switch components (`valid_switch_path`).
  * Normalizes Tie selection constraints across metrics consisting of local priority nodes (Hospitals), mathematical line resistance, projected drops, and fewest overall switch operations.
  * If Tie-closure fails or is un-available locally, executes `ems.run_for_cluster()` to isolate specific battery draws locally, before ruthlessly shedding priority 3 loads (Residential) while inherently protecting priority 1s (HOSP).
  
### 4.2 `models/fault_detector.py` (ANN Pattern Matching)
* **Architecture [33-52]**: A specialized multi-layer perceptron. Projects `[voltage, frequency, load, generation, stress]` onto a hidden 32→16 layer network assigning Logit priorities.
* **Synthetic Bootstrapping [55-104]**: Genereates artificial faults randomly seeding boundaries (Overload, Undervoltage) dynamically training its network continuously against 1000 items in milliseconds upon boot processing.
* **Physics Checks (`_anomaly_score`)**: Mixes pure neural tracking with hard limit threshold bounds to classify true states preventing drift over 1.05pu domains.

### 4.3 `models/lstm_model.py` (Recurrent Load Predictor)
* **Architecture [24-55]**: 2-Layer LSTM (Long Short Term Memory) cell system interpreting 10 step arrays comprising [load, generation, weather]. 
* Output layer passes memory blocks evaluating single sequence demand prediction scalars.
* Scales internally via `MinMaxScaler` arrays preventing gradient vanishing blocks mathematically.

### 4.4 `models/rl_agent.py` (Deep Q-Network Control Planner)
* **Features**: Interprets vast 52-dimension telemetry arrays against 5 concrete macro actions (Generation boost, Battery engage, Load shift, Capacitor jump, Tie-Reroutes).
* **Mechanism [132-388]**: 
  * Employs standard continuous ε-greedy exploration (Decaying `EPSILON`). 
  * Soft-Maxes action logic returning not just exact triggers but raw confidence factors natively exposed to the frontend log renderer.
* **Reward Mapping (`compute_reward`)**: Highly dense reward functions heavily penalizing imbalance or failed physical limits while rewarding perfect voltage curves +2.0.
* **Warm-up Block (`smart_warmup`)**: Runs an automated 150-step loop dynamically imitating rule-based optimal logic quickly structuring the replay memory matrices before active simulation opens.

---

## 5. Frontend Environment: React, DX & Data Mapping

### 5.1 `src/components/GridGraph.jsx` (Topology & Animation Vector Model)
* **Mounting [190-203]**: Standard `useEffect` binds scaling states updating directly against React state matrix.
* **Edge & Flow Construction [355-638]**: 
  * Employs literal D3 projection techniques parsing exact math vector pairs onto browser SVGs. Uses logical `<path>` strokes morphing class conditions natively on fault detections (`fault-segment`, `tie-switch-closed`).
  * **Particle Multiplicity**: `buildRoutes` evaluates DFS flows traversing grid structures. Generates 20-50 localized flow variables varying exactly matched against internal component speeds generating beautiful continuous animations of energy transmission.
* **Isolator Overlays**: Uses absolute math extremums mapping `affected_nodes`. Applies padding to draw glass-panel bounding boxes framing precise disconnected microgrids dynamically.

### 5.2 `src/components/AIDecisionPanel.jsx` (Human-AI UX Layer)
* An interface dedicated entirely against tracking the internal thought pathways originating in the back-python nodes. 
* **Demand Forecast Gauge [59-122]**: Connects directly to `predicted_load` scalars generating colored SVG arcs modulating smoothly tracking capacity predictions against local load variables.
* **FLISR Log Monitor [123-174]**: Decodes the precise `"step"` metrics array dumped continuously by `scada.py` applying exact colorization boundaries separating (LOCATE, ISOLATE, CLUSTER) steps in human readable contexts.
* **AI Architect [278-321]**: Tracks suggestions natively. Evaluates external array polling `getAISuggestions` recommending structural additions on weak system blocks.
* Uses modern glassmorphism (translucency + blurred backdrops) matching modern SCADA monitoring environments dynamically tied back to variables (`var(--accent-red)` vs `#f59e0b`).

---

## 6. End-to-End Execution Workflow

1.  **React App** mounts and initializes a continuous polling loop (`setInterval`) requesting the `GET /state` and `POST /simulate` vectors.
2.  **FastAPI `simulate` Endpoint** activates logic inside the Python context lock.
3.  **Physical Properties Update** modifying generic capacities against weather modifiers. 
4.  **EMS Run Cycle** maps linear logic calculating exact required balances assigning bounds mathematically.
5.  **SmartGrid Forward/Backward Sweep** maps the variables across graph edges mathematically generating precise voltage drop gradients. If boundaries fail, Relays flip breaking boundaries instantly.
6.  **SCADA Central Processing Trigger**:
    * **Fault Detection ANN** classifies physical statuses identifying failed hardware mappings.
    * **LSTM Demand Predictor** estimates local weather impact loops tracking capacities.
    * **RL Agent Forward Pass** feeds variables through deep Q-logic determining optimal strategic fixes, applying prior decisions.
    * **FLISR Evaluation** evaluates failed clusters dynamically running path algorithms tracing complex closures across remaining physical bounds locally assigning values natively avoiding isolated cascades.
7.  **JSON Payload Formatting** converts complete multi-nodal arrays safely shipping structures matching precisely human expectations.
8.  **Frontend State Hydration** consumes variables mapping exact values across SVGs natively re-rendering fluid layouts tracking energy vectors actively. CSS handles transition boundaries yielding beautiful smooth animations organically spanning the full map.
