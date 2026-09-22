"""Tellix API server.

A thin layer over the Kubernetes API. Submitting a workflow creates a
WorkflowRun custom resource; reading a run reads that resource back.
That is all this process does.

Notice what is missing compared to Phase 1: no background tasks, no
in-memory dict, no knowledge of Jobs. Creating and watching Jobs is the
operator's job (app/operator.py), and the cluster is the only place run
state lives. This server can be restarted mid-run, or replaced by three
replicas behind a load balancer, and nothing changes.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from kubernetes.client.exceptions import ApiException

from . import crd, k8s_client
from .models import WorkflowDef

app = FastAPI(
    title="Tellix",
    description="Kubernetes-native workflow engine",
    version="0.2.0",
)

_CRD_MISSING = (
    "The WorkflowRun custom resource type is not installed in this cluster. "
    "Run: kubectl apply -f k8s/"
)


def _handle_api_exception(e: ApiException) -> HTTPException:
    if e.status == 404:
        return HTTPException(status_code=503, detail=_CRD_MISSING)
    return HTTPException(
        status_code=502, detail=f"Kubernetes rejected the request: {e.reason}"
    )


@app.get("/health")
def health():
    """Liveness plus a note on whether the cluster is actually set up."""
    try:
        k8s_client.load_config()
        return {"status": "ok", "crdInstalled": crd.crd_installed()}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


@app.post("/workflows/runs", status_code=201)
def create_run(workflow: WorkflowDef):
    """Submit a workflow.

    This returns as soon as the resource exists, with every step still
    Pending - the operator has not necessarily even seen it yet. Poll
    GET /workflows/runs/{id}, or watch it with
    `kubectl get workflowruns -w`.
    """
    try:
        k8s_client.ensure_namespace()
        return crd.run_view(crd.create_run(workflow))
    except ApiException as e:
        raise _handle_api_exception(e) from e


@app.get("/workflows/runs")
def list_runs():
    try:
        return [crd.run_view(cr) for cr in crd.list_runs()]
    except ApiException as e:
        raise _handle_api_exception(e) from e


@app.get("/workflows/runs/{run_id}")
def get_run(run_id: str):
    try:
        cr = crd.get_run(run_id)
    except ApiException as e:
        raise _handle_api_exception(e) from e
    if cr is None:
        raise HTTPException(status_code=404, detail="run not found")
    return crd.run_view(cr)


@app.delete("/workflows/runs/{run_id}", status_code=204)
def delete_run(run_id: str):
    """Delete a run and, through owner references, all of its Jobs."""
    try:
        deleted = crd.delete_run(run_id)
    except ApiException as e:
        raise _handle_api_exception(e) from e
    if not deleted:
        raise HTTPException(status_code=404, detail="run not found")
