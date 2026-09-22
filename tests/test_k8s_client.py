"""Job manifest construction - the part of Kubernetes we can test offline."""
from app.k8s_client import (
    MAX_NAME_LENGTH,
    NAMESPACE,
    build_job_manifest,
    job_name_for,
)
from app.models import StepDef


def test_job_name_is_readable_when_it_fits():
    assert job_name_for("toy-ml-pipeline-a1b2c3d4", "fetch-data") == (
        "toy-ml-pipeline-a1b2c3d4-fetch-data"
    )


def test_job_name_is_truncated_to_the_kubernetes_limit():
    name = job_name_for("x" * 60, "a-very-long-step-name-indeed")
    assert len(name) <= MAX_NAME_LENGTH


def test_job_name_is_deterministic_even_when_truncated():
    """Idempotent Job creation depends on this: same inputs, same name."""
    args = ("y" * 60, "another-really-long-step-name")
    assert job_name_for(*args) == job_name_for(*args)


def test_truncated_job_names_stay_distinct():
    """Names that only differ past the cut-off must not collide."""
    prefix = "z" * 60
    first = job_name_for(prefix, "step-number-one-with-padding")
    second = job_name_for(prefix, "step-number-two-with-padding")
    assert first != second


def test_manifest_has_the_shape_kubernetes_expects():
    step = StepDef(name="train", image="python:3.12-slim", command=["python", "-c", "1"])
    manifest = build_job_manifest("run-1", step)

    assert manifest["apiVersion"] == "batch/v1"
    assert manifest["kind"] == "Job"
    assert manifest["metadata"]["namespace"] == NAMESPACE
    assert manifest["metadata"]["name"] == "run-1-train"

    pod_spec = manifest["spec"]["template"]["spec"]
    assert pod_spec["restartPolicy"] == "Never"
    container = pod_spec["containers"][0]
    assert container["image"] == "python:3.12-slim"
    assert container["command"] == ["python", "-c", "1"]


def test_steps_are_labelled_with_their_run_so_they_can_be_found_later():
    step = StepDef(name="train", image="busybox")
    manifest = build_job_manifest("run-1", step)
    expected = {"tellix.dev/run": "run-1", "tellix.dev/step": "train"}
    assert manifest["metadata"]["labels"] == expected
    assert manifest["spec"]["template"]["metadata"]["labels"] == expected


def test_optional_fields_are_omitted_rather_than_sent_as_null():
    step = StepDef(name="train", image="busybox")
    manifest = build_job_manifest("run-1", step)
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "command" not in container
    assert "env" not in container
    assert "activeDeadlineSeconds" not in manifest["spec"]


def test_timeout_becomes_an_active_deadline():
    step = StepDef(name="train", image="busybox", timeout_seconds=120)
    manifest = build_job_manifest("run-1", step)
    assert manifest["spec"]["activeDeadlineSeconds"] == 120


def test_env_becomes_a_list_of_name_value_pairs():
    step = StepDef(name="train", image="busybox", env={"SEED": "42"})
    manifest = build_job_manifest("run-1", step)
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["env"] == [{"name": "SEED", "value": "42"}]


def test_a_failed_step_is_not_silently_retried():
    step = StepDef(name="train", image="busybox")
    manifest = build_job_manifest("run-1", step)
    assert manifest["spec"]["backoffLimit"] == 0
