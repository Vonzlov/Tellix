"""Pydantic models for workflow definitions and run state."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# Kubernetes object names must be lowercase alphanumeric with dashes
# (DNS-1123 label rules) - validate early with a clear error instead of
# letting a confusing Kubernetes API error surface later.
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


class StepDef(BaseModel):
    name: str
    image: str
    command: list[str] | None = None
    depends_on: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                f"step name {v!r} must be lowercase alphanumeric with dashes "
                "(Kubernetes naming rules), e.g. 'fetch-data'"
            )
        return v


class WorkflowDef(BaseModel):
    name: str
    steps: list[StepDef]


class StepRunState(BaseModel):
    name: str
    status: str = "pending"  # pending | launching | running | succeeded | failed
    job_name: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    logs: str | None = None


class WorkflowRun(BaseModel):
    id: str
    workflow_name: str
    status: str = "running"  # running | succeeded | failed
    steps: dict[str, StepRunState]
    created_at: str
