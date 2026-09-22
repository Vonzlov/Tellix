"""Pydantic models for workflow definitions and run state.

These models do double duty. They validate what users POST to the API,
and they are the shape the operator reasons about in memory.

Field names are snake_case in Python but serialise to camelCase, because
that is the convention every Kubernetes resource follows - so
`depends_on` in Python is `dependsOn` inside a WorkflowRun custom
resource. Both spellings are accepted on input, which keeps the existing
example YAML files working.
"""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

# Kubernetes object names must be lowercase alphanumeric with dashes
# (DNS-1123 label rules) - validate early with a clear error instead of
# letting a confusing Kubernetes API error surface later.
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

# Phase names deliberately match how core Kubernetes objects such as Pods
# report state, so `kubectl get workflowruns` reads the same way as
# `kubectl get pods`. Used for both individual steps and the whole run.
PHASE_PENDING = "Pending"
PHASE_RUNNING = "Running"
PHASE_SUCCEEDED = "Succeeded"
PHASE_FAILED = "Failed"

TERMINAL_PHASES = frozenset({PHASE_SUCCEEDED, PHASE_FAILED})


class _CamelModel(BaseModel):
    """Base model that speaks snake_case in Python and camelCase on the wire."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class StepDef(_CamelModel):
    """One step of a workflow: a container image plus how to run it."""

    name: str
    image: str
    command: list[str] | None = None
    depends_on: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: Annotated[int, Field(ge=1)] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                f"step name {v!r} must be lowercase alphanumeric with dashes "
                "(Kubernetes naming rules), e.g. 'fetch-data'"
            )
        return v


class WorkflowDef(_CamelModel):
    """A whole workflow: named steps wired together by dependencies."""

    name: str = Field(max_length=50)
    steps: list[StepDef] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                f"workflow name {v!r} must be lowercase alphanumeric with dashes "
                "(Kubernetes naming rules), e.g. 'toy-ml-pipeline'"
            )
        return v

    @model_validator(mode="after")
    def validate_graph(self) -> WorkflowDef:
        """Reject duplicate names, unknown dependencies and dependency cycles.

        Without this, a typo like depends_on: ["fetch-dat"] would be
        accepted happily and then simply never become ready - the run
        would sit there looking stuck for no visible reason. Catching it
        at submit time turns a mystery into an error message.
        """
        names = [step.name for step in self.steps]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate step names: {sorted(duplicates)}")

        known = set(names)
        for step in self.steps:
            for dep in step.depends_on:
                if dep == step.name:
                    raise ValueError(f"step {step.name!r} depends on itself")
                if dep not in known:
                    raise ValueError(
                        f"step {step.name!r} depends on unknown step {dep!r}; "
                        f"known steps are {sorted(known)}"
                    )

        cycle = _find_cycle({s.name: list(s.depends_on) for s in self.steps})
        if cycle:
            raise ValueError(
                "dependency cycle detected: " + " -> ".join(cycle)
            )
        return self


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Return one cycle as a list of step names, or None if the graph is acyclic.

    Plain depth-first search with a colouring scheme: white = not seen,
    grey = on the current path, black = fully explored. Meeting a grey
    node means we walked back onto our own path, which is a cycle.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(graph, WHITE)
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        colour[node] = GREY
        path.append(node)
        for dep in graph.get(node, []):
            if colour.get(dep) == GREY:
                return path[path.index(dep):] + [dep]
            if colour.get(dep) == WHITE:
                found = visit(dep)
                if found:
                    return found
        path.pop()
        colour[node] = BLACK
        return None

    for node in graph:
        if colour[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return None


class StepRunState(_CamelModel):
    """Live state of one step within a run - mirrors status.steps[name] in the CR."""

    name: str
    phase: str = PHASE_PENDING
    job_name: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    logs: str | None = None


class WorkflowRun(_CamelModel):
    """In-memory view of a WorkflowRun custom resource.

    The custom resource in the cluster is the source of truth; this is
    just a convenient typed snapshot of it that the engine and the API
    layer can work with.
    """

    id: str
    workflow_name: str
    phase: str = PHASE_PENDING
    steps: dict[str, StepRunState]
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
