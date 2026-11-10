"""Schemas and DTOs for the isolated Sandbox Agent service."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.python_sandbox import EnforcementDetails, ExecutionStatus, JSONValue


class SandboxExecutionRequest(BaseModel):
    """Strictly validated execution request payload for isolated sandbox execution.

    Extra fields are strictly forbidden to prevent injection of Docker flags,
    host paths, environment variables, or container commands.
    """

    model_config = ConfigDict(extra="forbid")

    execution_id: str = Field(..., min_length=1, max_length=128)
    organization_id: uuid.UUID
    job_id: uuid.UUID
    script_hash: str = Field(..., min_length=1, max_length=128)
    code_str: str = Field(..., max_length=256 * 1024)
    input_data: dict[str, Any] | list[Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=30)
    runtime_id: Literal["python-3.11-standard"] = "python-3.11-standard"


class SandboxExecutionResponse(BaseModel):
    """Execution response returned by Sandbox Agent."""

    execution_id: str
    status: ExecutionStatus
    execution_time_ms: int
    memory_peak_mb: float
    stdout: str
    stderr: str
    error_message: str | None = None
    result_payload: dict[str, JSONValue] | None = None
    isolation_backend: str = "sandbox_agent_oci"
    enforcement_details: EnforcementDetails = Field(default_factory=lambda: EnforcementDetails())


class SandboxLease(BaseModel):
    """Lease tracking execution lifecycle and ownership."""

    execution_id: str
    container_name: str
    container_id: str | None = None
    organization_id: uuid.UUID
    job_id: uuid.UUID
    started_at: float
    expires_at: float
    status: Literal["running", "completed", "failed", "terminated"]
