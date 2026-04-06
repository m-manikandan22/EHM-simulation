"""
routes.py — FastAPI route definitions for the Smart Grid API.

Uses FastAPI dependency injection (Request → app.state) instead of
mutable module-level globals, which eliminates NoneType type errors.
"""

from __future__ import annotations

import threading
from typing import Optional
from fastapi import APIRouter, HTTPException, Request  # type: ignore
from pydantic import BaseModel  # type: ignore

from simulation.grid import SmartGrid  # type: ignore
from simulation.scada import ScadaControlCenter  # type: ignore
from simulation.ems import EnergyManagementSystem  # type: ignore
from models.rl_agent import ACTIONS, N_ACTIONS  # type: ignore

router = APIRouter()
grid_lock = threading.Lock()


# -----------------------------------------------------------------------
# Dependency helpers — pull singletons from app.state
# -----------------------------------------------------------------------

def get_grid(request: Request) -> SmartGrid:
    return request.app.state.grid


def get_scada(request: Request) -> ScadaControlCenter:
    return request.app.state.scada


def get_ems(request: Request) -> EnergyManagementSystem:
    return request.app.state.ems


def get_forecaster(request: Request):
    return get_scada(request).forecaster


def get_fault_detector(request: Request):
    return get_scada(request).fault_detector


# -----------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------

def _apply_action(action_name: str, state_before: dict, grid: SmartGrid, scada: ScadaControlCenter) -> str:
    """Apply an action using SCADA dispatch logic."""
    return scada._dispatch_control_signal(action_name, state_before, grid)


# -----------------------------------------------------------------------
# Request / Response Models
# -----------------------------------------------------------------------

class EventRequest(BaseModel):
    type: str                    # "failure" | "storm" | "clear_storm" | "demand" | "generation" | "restore"
    node_id: Optional[str] = None
    amount: Optional[float] = None


class ActionRequest(BaseModel):
    action_id: int               # 0–4

class NodeRequest(BaseModel):
    type: str                    # "generator" | "substation" | "house"
    position: list[float]        # [x, y]

class PositionRequest(BaseModel):
    x: float
    y: float

class EdgeRequest(BaseModel):
    source: str
    target: str

class NodeTargetRequest(BaseModel):
    node_id: str


# -----------------------------------------------------------------------
# GET /health
# -----------------------------------------------------------------------

@router.get("/health")
def health_check() -> dict:
    return {"status": "ok", "message": "Smart Grid API is running"}


# -----------------------------------------------------------------------
# GET /state
# -----------------------------------------------------------------------

@router.get("/state")
def get_state(request: Request) -> dict:
    """Return the full current grid state without advancing simulation."""
    grid: SmartGrid = get_grid(request)
    return grid.get_state()


# -----------------------------------------------------------------------
# POST /simulate
# -----------------------------------------------------------------------

@router.post("/simulate")
def simulate_step(request: Request) -> dict:
    """
    Advance simulation by 1 timestep.

    Real CPS execution order (enforced here):
      1. grid.step()          — Physics only (power flow, voltage, overload trips)
      2. ems.run(grid)        — EMS reacts to imbalance (partial storage dispatch)
      3. scada.execute(grid)  — SCADA AI (fault detection, DQN decision, FLISR)

    EMS runs AFTER physics so it reacts to real imbalance (not pre-empt it).
    EMS uses partial control (50 % absorption) so imbalance remains visible.
    """
    with grid_lock:
        grid:  SmartGrid             = get_grid(request)
        ems:   EnergyManagementSystem = get_ems(request)
        scada: ScadaControlCenter    = get_scada(request)

        # ── 1. Calculate generation (solar + wind) ──
        grid.update_generation()

        # ── 2. EMS balances (battery charge/discharge + priority allocation) ──
        ems_report = ems.run(grid)

        # ── 3. Physics flow (BFS + voltage) ──
        grid.update_power_flow()

        # ── 4 & 5. SCADA detects issues + FLISR reroutes ──
        # Note: scada.execute_control_loop includes FLISR which handles 
        # ── 6. If fails → use storage + shedding ── by calling ems.run_for_cluster()
        scada_report = scada.execute_control_loop(grid, ems)

        return {
            "grid": grid.get_state(),
            "ems": {
                "cycle":            ems_report["cycle"],
                "balance_mw":       ems_report["balance"],
                "total_gen":        ems_report["total_gen"],
                "total_load":       ems_report["total_load"],
                "absorption_ratio": ems_report["absorption_ratio"],
                "log":              ems_report["log"],
                "message":          ems_report["message"],
            },
            "ai": {
                "predicted_load":     scada_report["predicted_load"],
                "decision":           scada_report["decision"],
                "action_result":      scada_report["action_result"],
                "flisr_log":          scada_report.get("flisr_log", []),
                "fault_analysis":     scada_report["fault_analysis"],
                "cycle_id":           scada_report["cycle_id"],
                "timestamp":          scada_report["timestamp"],
                "hour_of_day":        scada_report["hour_of_day"],
                "control_divergence": scada_report["control_divergence"],
                "overload_warnings":  scada_report.get("overload_warnings", []),
            },
        }





# -----------------------------------------------------------------------
# Grid Construction APIs (User Controlled)
# -----------------------------------------------------------------------

@router.post("/reset")
def reset_grid(request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    grid.__init__()
    return {"message": "Grid reset to initial state.", "grid": grid.get_state()}

@router.post("/add_node")
def add_user_node(req: NodeRequest, request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    try:
        x, y = req.position[0], req.position[1]
        nid = grid.add_user_node(req.type, x, y)
        return {"message": f"Added node: {nid['id']}", "grid": grid.get_state()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/nodes/{node_id}/move")
def move_user_node(node_id: str, req: PositionRequest, request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    try:
        grid.move_node(node_id, req.x, req.y)
        return {"message": f"Moved {node_id}", "grid": grid.get_state()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/nodes/{node_id}")
def delete_user_node(node_id: str, request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    try:
        msg = grid.delete_node(node_id)
        return {"message": msg, "grid": grid.get_state()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/connect")
def add_user_edge(req: EdgeRequest, request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    try:
        msg = grid.add_user_edge(req.source, req.target)
        return {"message": msg, "grid": grid.get_state()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/cut_edge")
def cut_user_edge(req: EdgeRequest, request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    try:
        msg = grid.cut_user_edge(req.source, req.target)
        return {"message": msg, "grid": grid.get_state()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/fail_node")
def fail_user_node(req: NodeTargetRequest, request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    if req.node_id not in grid.nodes:
        raise HTTPException(status_code=404, detail="Node not found.")
    msg = grid.inject_failure(req.node_id)
    return {"message": msg, "grid": grid.get_state()}

@router.post("/restore_node")
def restore_user_node(req: NodeTargetRequest, request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    if req.node_id not in grid.nodes:
        raise HTTPException(status_code=404, detail="Node not found.")
    msg = grid.restore_node(req.node_id)
    return {"message": msg, "grid": grid.get_state()}

@router.post("/command/add_house")
def add_house_to_pole(req: NodeTargetRequest, request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    if req.node_id not in grid.nodes:
        raise HTTPException(status_code=404, detail="Node not found.")
    msg = grid.add_house(req.node_id)
    return {"message": msg, "grid": grid.get_state()}

@router.get("/ai/suggestions")
def get_ai_suggestions(request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    return {"suggestions": grid.suggest_tie_lines()}
    
@router.post("/ai/suggest_parent")
def post_suggest_parent(req: PositionRequest, request: Request) -> dict:
    grid: SmartGrid = get_grid(request)
    return grid.suggest_best_parent(req.x, req.y)

# -----------------------------------------------------------------------
# POST /event
# -----------------------------------------------------------------------

@router.post("/event")
def trigger_event(req: EventRequest, request: Request) -> dict:
    """
    Trigger a grid event.
    type: "failure" | "storm" | "clear_storm" | "demand" | "generation" | "restore"
    """
    grid: SmartGrid = get_grid(request)

    if req.type == "failure":
        if not req.node_id:
            raise HTTPException(status_code=400, detail="node_id required for failure event")
        msg = grid.inject_failure(req.node_id)
        return {"message": msg, "grid": grid.get_state()}

    elif req.type == "storm":
        msg = grid.trigger_storm()
        return {"message": msg, "grid": grid.get_state()}

    elif req.type == "clear_storm":
        msg = grid.clear_storm()
        return {"message": msg, "grid": grid.get_state()}

    elif req.type == "demand":
        amount = req.amount if req.amount is not None else 0.2
        msg = grid.increase_demand(amount)
        return {"message": msg, "grid": grid.get_state()}

    elif req.type == "generation":
        amount = req.amount if req.amount is not None else 0.3
        msg = grid.increase_generation(amount)
        return {"message": msg, "grid": grid.get_state()}

    elif req.type == "restore":
        if not req.node_id:
            raise HTTPException(status_code=400, detail="node_id required for restore event")
        msg = grid.restore_node(req.node_id)
        return {"message": msg, "grid": grid.get_state()}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown event type: {req.type}")


# -----------------------------------------------------------------------
# GET /predict
# -----------------------------------------------------------------------

@router.get("/predict")
def predict_demand(request: Request, node_id: str = "S0") -> dict:
    """Run LSTM forecasting for a given node."""
    grid: SmartGrid = get_grid(request)
    forecaster: DemandForecaster = get_forecaster(request)
    sequence = grid.get_lstm_input(node_id)
    predicted = forecaster.predict(sequence)
    return {
        "node_id": node_id,
        "predicted_load": round(float(predicted), 4),  # type: ignore
        "sequence_length": len(sequence),
    }


# -----------------------------------------------------------------------
# POST /action
# -----------------------------------------------------------------------

@router.post("/action")
def force_action(req: ActionRequest, request: Request) -> dict:
    """Force a specific RL action (0–4)."""
    grid: SmartGrid = get_grid(request)
    scada: ScadaControlCenter = get_scada(request)

    if req.action_id < 0 or req.action_id >= N_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action_id must be 0–{N_ACTIONS - 1}")

    state = grid.get_state()
    action = ACTIONS[req.action_id]
    result = _apply_action(action["name"], state, grid, scada)

    return {
        "action": action,
        "result": result,
        "grid": grid.get_state(),
    }


# -----------------------------------------------------------------------
# GET /fault_analysis
# -----------------------------------------------------------------------

@router.get("/fault_analysis")
def fault_analysis(request: Request) -> dict:
    """
    Run the AI Fault Detector across all live nodes.
    Returns per-node anomaly scores, fault types, and system health.
    """
    grid: SmartGrid     = get_grid(request)
    detector: FaultDetector = get_fault_detector(request)
    return detector.analyse(grid.nodes)


# -----------------------------------------------------------------------
# GET /islanding_analysis
# -----------------------------------------------------------------------

@router.get("/islanding_analysis")
def islanding_analysis(request: Request) -> dict:
    """
    Analyze potential microgrid formation for resilience.
    Returns viable island configurations around healthy generators.
    """
    grid: SmartGrid = get_grid(request)
    failed_nodes = [nid for nid, node in grid.nodes.items() if node.failed]
    return grid.predictive_islanding(failed_nodes)


# -----------------------------------------------------------------------
# POST /reset
# -----------------------------------------------------------------------

@router.post("/reset")
def reset_grid(request: Request) -> dict:
    """Reset the grid to its initial state."""
    grid: SmartGrid = get_grid(request)
    msg = grid.reset()
    return {"message": msg, "grid": grid.get_state()}
