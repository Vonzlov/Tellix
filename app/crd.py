"""Reading and writing WorkflowRun custom resources.

This module is the boundary between "Kubernetes objects" (plain dicts,
camelCase, whatever the API server gives us) and "our types" (the
pydantic models in app/models.py). Everything above this line works with
models; everything below it works with dicts.

Phase 1 stored runs in a process-local dict that vanished on restart.
Here the cluster holds them instead, which is why `kubectl get
workflowruns` works and why nothing is lost when a process dies.
"""
from __future__ import annotations

import uuid

from kubernetes import client

from . import k8s_client
from .models import (
    PHASE_PENDING,
    StepRunState,
    WorkflowDef,
    WorkflowRun,
)

# Together these identify our custom resource type to the Kubernetes API,
# and they must match k8s/crd-workflowrun.yaml exactly.
GROUP = "tellix.dev"
VERSION = "v1alpha1"
PLURAL = "workflowruns"
KIND = "WorkflowRun"
API_VERSION = f"{GROUP}/{VERSION}"


def _api() -> client.CustomObjectsApi:
    """Client for custom resources.

    Built-in kinds get generated, typed clients (BatchV1Api for Jobs and
    so on). Custom kinds are only known to the cluster at runtime, so
    they go through this generic dict-in/dict-out client instead.
    """
    k8s_client.load_config()
    return client.CustomObjectsApi()


def run_name_for(workflow_name: str) -> str:
    """Build a readable, unique, DNS-1123-valid name for a run.

    Phase 1 used a bare UUID. Prefixing the workflow name means
    `kubectl get workflowruns` is readable at a glance, and the random
    suffix keeps repeated runs of the same workflow from colliding.
    """
    return f"{workflow_name}-{uuid.uuid4().hex[:8]}"


def manifest_for(workflow: WorkflowDef, name: str | None = None) -> dict:
    """Build the WorkflowRun custom resource for a submitted workflow."""
    return {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {
            "name": name or run_name_for(workflow.name),
            "namespace": k8s_client.NAMESPACE,
        },
        "spec": {
            "workflowName": workflow.name,
            "steps": [
                step.model_dump(by_alias=True, exclude_none=True)
                for step in workflow.steps
            ],
        },
    }


def create_run(workflow: WorkflowDef) -> dict:
    """Create a WorkflowRun in the cluster and return the stored object.

    Note what this function does *not* do: it does not start any Jobs.
    Creating the resource is the whole job of the API layer. The operator
    is watching this resource type and picks the run up from here.
    """
    return _api().create_namespaced_custom_object(
        group=GROUP,
        version=VERSION,
        namespace=k8s_client.NAMESPACE,
        plural=PLURAL,
        body=manifest_for(workflow),
    )


def get_run(name: str) -> dict | None:
    try:
        return _api().get_namespaced_custom_object(
            group=GROUP,
            version=VERSION,
            namespace=k8s_client.NAMESPACE,
            plural=PLURAL,
            name=name,
        )
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return None
        raise


def list_runs() -> list[dict]:
    response = _api().list_namespaced_custom_object(
        group=GROUP,
        version=VERSION,
        namespace=k8s_client.NAMESPACE,
        plural=PLURAL,
    )
    return response.get("items", [])


def delete_run(name: str) -> bool:
    """Delete a run. Its Jobs go with it, via ownerReferences."""
    try:
        _api().delete_namespaced_custom_object(
            group=GROUP,
            version=VERSION,
            namespace=k8s_client.NAMESPACE,
            plural=PLURAL,
            name=name,
        )
        return True
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return False
        raise


def crd_installed() -> bool:
    """Whether the WorkflowRun CRD has been applied to this cluster."""
    try:
        client.ApiextensionsV1Api().read_custom_resource_definition(
            f"{PLURAL}.{GROUP}"
        )
        return True
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return False
        raise


# --- conversions between custom resource dicts and our models ---------


def workflow_from_cr(cr: dict) -> WorkflowDef:
    """Recover the submitted workflow definition from a run's spec."""
    spec = cr.get("spec") or {}
    return WorkflowDef(name=spec["workflowName"], steps=spec.get("steps", []))


def run_from_cr(cr: dict) -> WorkflowRun:
    """Build a typed snapshot of a run from its custom resource.

    Steps that the operator has not touched yet have no entry under
    status.steps at all, so they are filled in as Pending here. That
    keeps every consumer from having to handle "key might be missing".
    """
    meta = cr.get("metadata") or {}
    spec = cr.get("spec") or {}
    status = cr.get("status") or {}
    step_status = status.get("steps") or {}

    steps: dict[str, StepRunState] = {}
    for raw_step in spec.get("steps", []):
        name = raw_step["name"]
        fields = dict(step_status.get(name) or {})
        fields.pop("name", None)
        steps[name] = StepRunState(name=name, **fields)

    return WorkflowRun(
        id=meta.get("name", ""),
        workflow_name=spec.get("workflowName", ""),
        phase=status.get("phase", PHASE_PENDING),
        steps=steps,
        created_at=meta.get("creationTimestamp"),
        started_at=status.get("startedAt"),
        finished_at=status.get("finishedAt"),
    )


def run_view(cr: dict) -> dict:
    """The JSON body the HTTP API returns for a run."""
    return run_from_cr(cr).model_dump(by_alias=True)
