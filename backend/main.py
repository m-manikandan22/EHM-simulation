"""
main.py - FastAPI application entry point.

Uses the FastAPI lifespan context manager to initialise singletons and
store them in app.state, making them accessible via dependency injection
in routes (no mutable module-level globals).

Run with:
  python main.py
  OR
  uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import sys
import os
from contextlib import asynccontextmanager

# Ensure backend root is on the Python path so relative imports resolve
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore

from simulation.grid import SmartGrid  # type: ignore
from simulation.scada import ScadaControlCenter  # type: ignore
from simulation.ems import EnergyManagementSystem  # type: ignore
from api.routes import router  # type: ignore


# -----------------------------------------------------------------------
# Lifespan - initialise once, store in app.state
# -----------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: build singletons. Shutdown: nothing to clean up."""
    print("=" * 60)
    print("  AI Self-Healing Smart Grid - Backend Starting Up")
    print("=" * 60)

    print("\n[1/3] Initialising Structural Level (Physical Grid)...")
    app.state.grid = SmartGrid()
    g: SmartGrid = app.state.grid
    print(f"      Grid created: {len(g.nodes)} nodes, "
          f"{g.graph.number_of_edges()} edges")

    print("\n[2/3] Booting Energy Management System (EMS Layer)...")
    app.state.ems = EnergyManagementSystem()
    print("      EMS ready - absorption ratio: 50 %, partial control mode")

    print("\n[3/3] Booting SCADA Control Center (AI Layer)...")
    app.state.scada = ScadaControlCenter()
    app.state.scada.warmup_ai(g)  # Pre-train RL agent on grid bounds

    print("\n[OK] All systems ready.\n")
    print("=" * 60)

    yield   # application runs here

    # Teardown (nothing required)


# -----------------------------------------------------------------------
# FastAPI App
# -----------------------------------------------------------------------

app = FastAPI(
    title="AI Self-Healing Smart Grid SCADA API",
    description=(
        "Real-time smart grid architecture with a separated SCADA Control Center "
        "managing fault detection and multi-agent demand response."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# CORS - allow React dev server and any local origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


# -----------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn  # type: ignore
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
