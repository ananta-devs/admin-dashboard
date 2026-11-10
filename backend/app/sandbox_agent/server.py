"""FastAPI HTTP service for the dedicated Sandbox Agent process."""

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, status

from app.analytics.python_sandbox import verify_sandbox_runtime_profile
from app.sandbox_agent.manager import SandboxRuntimeManager
from app.sandbox_agent.schemas import (
    SandboxExecutionRequest,
    SandboxExecutionResponse,
    SandboxLease,
)
from app.sandbox_agent.security import (
    SandboxTokenError,
    TokenRevocationRegistry,
    verify_scoped_execution_token,
)

app = FastAPI(
    title="VIBE Sandbox Agent Service",
    description="Dedicated privilege boundary and runtime manager for isolated container sandboxes",
    version="1.0.0",
)

# In-memory revocation registry and runtime manager
revocation_registry = TokenRevocationRegistry()
manager = SandboxRuntimeManager()

# Active rotation keys
PRIMARY_KEY = os.environ.get("VIBE_SANDBOX_AGENT_PRIMARY_KEY", "vibe-agent-primary-secret-key-2026")
ROTATION_KEY = os.environ.get("VIBE_SANDBOX_AGENT_ROTATION_KEY", "")


def get_active_keys() -> list[str]:
    keys = [PRIMARY_KEY]
    if ROTATION_KEY:
        keys.append(ROTATION_KEY)
    return keys


@app.get("/api/v1/sandbox/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint confirming daemon availability and service status."""
    docker_ok = manager.check_docker_available()
    return {
        "status": "healthy" if docker_ok else "degraded",
        "service": "vibe-sandbox-agent",
        "docker_available": docker_ok,
        "active_leases_count": len(manager.active_leases),
    }


@app.post("/api/v1/sandbox/execute", response_model=SandboxExecutionResponse)
async def execute_sandbox_request(
    req: SandboxExecutionRequest,
    x_sandbox_token: str | None = Header(None, alias="X-Sandbox-Token"),
) -> SandboxExecutionResponse:
    """Execute a strictly validated script request inside isolated OCI container."""
    if not x_sandbox_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Sandbox-Token authorization header.",
        )

    try:
        verify_scoped_execution_token(
            token_str=x_sandbox_token,
            expected_org_id=req.organization_id,
            expected_job_id=req.job_id,
            expected_exec_id=req.execution_id,
            active_keys=get_active_keys(),
            revocation_registry=revocation_registry,
        )
    except SandboxTokenError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Sandbox authorization error: {err}",
        ) from err

    return manager.execute(req)


@app.post("/api/v1/sandbox/terminate/{execution_id}")
async def terminate_execution(execution_id: str) -> dict[str, Any]:
    """Idempotently cancel and terminate an active sandbox execution."""
    success = manager.terminate_execution(execution_id)
    return {"execution_id": execution_id, "terminated": success}


@app.get("/api/v1/sandbox/leases", response_model=list[SandboxLease])
async def list_leases() -> list[SandboxLease]:
    """Inspect active execution leases."""
    with manager._lock:
        return list(manager.active_leases.values())


@app.post("/api/v1/sandbox/reap")
async def reap_orphaned_containers(max_age_seconds: int = Query(default=60, ge=1)) -> dict[str, Any]:
    """Sweep and remove orphaned containers and volumes whose lease expired."""
    count = manager.reap_orphans(max_age_seconds=max_age_seconds)
    return {"reaped_count": count}


@app.get("/api/v1/sandbox/attestation")
async def runtime_attestation() -> dict[str, Any]:
    """Inspect live container runtime security profile."""
    return verify_sandbox_runtime_profile()


@app.post("/api/v1/sandbox/rotate-key")
async def rotate_keys(new_primary_key: str, old_key_as_rotation: bool = True) -> dict[str, Any]:
    """Promote a new primary key and optionally retain previous key in rotation."""
    global PRIMARY_KEY, ROTATION_KEY
    if old_key_as_rotation:
        ROTATION_KEY = PRIMARY_KEY
    PRIMARY_KEY = new_primary_key
    return {"status": "keys_rotated", "has_rotation_key": bool(ROTATION_KEY)}


@app.post("/api/v1/sandbox/revoke-token")
async def revoke_token(nonce: str | None = None, execution_id: str | None = None) -> dict[str, Any]:
    """Revoke specific token nonce or execution ID."""
    if nonce:
        revocation_registry.revoke_nonce(nonce)
    if execution_id:
        revocation_registry.revoke_execution(execution_id)
    return {"status": "revoked", "nonce": nonce, "execution_id": execution_id}

