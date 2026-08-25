"""Tellix API server."""
from fastapi import FastAPI, HTTPException

from . import engine
from .models import WorkflowDef
from .store import store

app = FastAPI(title="Tellix", description="Kubernetes-native workflow engine")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/workflows/runs")
async def create_run(workflow: WorkflowDef):
    run = await engine.start_workflow_run(workflow)
    return run


@app.get("/workflows/runs")
async def list_runs():
    return store.list()


@app.get("/workflows/runs/{run_id}")
async def get_run(run_id: str):
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run
