"""Translation between WorkflowRun custom resources and our models.

No cluster involved: these are the pure conversion functions from
app/crd.py, which is where camelCase Kubernetes dicts meet snake_case
pydantic models.
"""
from app import crd
from app.models import PHASE_PENDING, PHASE_RUNNING, PHASE_SUCCEEDED, WorkflowDef


def _workflow() -> WorkflowDef:
    return WorkflowDef(
        name="toy-ml-pipeline",
        steps=[
            {"name": "fetch-data", "image": "busybox"},
            {"name": "train", "image": "busybox", "depends_on": ["fetch-data"]},
        ],
    )


def test_run_names_are_readable_and_unique():
    first = crd.run_name_for("toy-ml-pipeline")
    second = crd.run_name_for("toy-ml-pipeline")
    assert first.startswith("toy-ml-pipeline-")
    assert first != second


def test_manifest_identifies_our_custom_resource_type():
    manifest = crd.manifest_for(_workflow(), name="run-1")
    assert manifest["apiVersion"] == "tellix.dev/v1alpha1"
    assert manifest["kind"] == "WorkflowRun"
    assert manifest["metadata"]["name"] == "run-1"


def test_manifest_spec_uses_kubernetes_field_names():
    manifest = crd.manifest_for(_workflow(), name="run-1")
    spec = manifest["spec"]
    assert spec["workflowName"] == "toy-ml-pipeline"
    assert spec["steps"][1]["dependsOn"] == ["fetch-data"]


def test_a_submitted_workflow_survives_the_round_trip():
    original = _workflow()
    recovered = crd.workflow_from_cr(crd.manifest_for(original, name="run-1"))
    assert recovered == original


def test_untouched_steps_read_back_as_pending():
    """A brand new resource has no status at all; every step is Pending."""
    run = crd.run_from_cr(crd.manifest_for(_workflow(), name="run-1"))
    assert run.id == "run-1"
    assert run.phase == PHASE_PENDING
    assert {name: s.phase for name, s in run.steps.items()} == {
        "fetch-data": PHASE_PENDING,
        "train": PHASE_PENDING,
    }


def test_status_from_the_cluster_is_read_back_onto_the_steps():
    cr = crd.manifest_for(_workflow(), name="run-1")
    cr["metadata"]["creationTimestamp"] = "2026-08-28T10:00:00Z"
    cr["status"] = {
        "phase": PHASE_RUNNING,
        "steps": {
            "fetch-data": {
                "phase": PHASE_SUCCEEDED,
                "jobName": "run-1-fetch-data",
                "logs": "done",
            }
        },
    }

    run = crd.run_from_cr(cr)
    assert run.phase == PHASE_RUNNING
    assert run.created_at == "2026-08-28T10:00:00Z"
    assert run.steps["fetch-data"].job_name == "run-1-fetch-data"
    assert run.steps["fetch-data"].logs == "done"
    # Present in spec but not yet in status - filled in as Pending.
    assert run.steps["train"].phase == PHASE_PENDING


def test_the_http_view_of_a_run_uses_kubernetes_field_names():
    view = crd.run_view(crd.manifest_for(_workflow(), name="run-1"))
    assert view["id"] == "run-1"
    assert view["workflowName"] == "toy-ml-pipeline"
    assert set(view["steps"]) == {"fetch-data", "train"}
