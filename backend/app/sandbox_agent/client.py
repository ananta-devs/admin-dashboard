"""HTTP Client for communicating with the separate Sandbox Agent service."""

import logging
import uuid
from typing import Any

import httpx

from app.sandbox_agent.schemas import (
    SandboxExecutionRequest,
    SandboxExecutionResponse,
)
from app.sandbox_agent.security import create_scoped_execution_token

logger = logging.getLogger(__name__)


class SandboxAgentClient:
    """Client for delegating isolated code execution to the Sandbox Agent service."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8008",
        secret_key: str = "vibe-agent-primary-secret-key-2026",
        timeout_seconds: float = 35.0,
        app: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret_key = secret_key
        self.timeout_seconds = timeout_seconds
        self.app = app

    def execute(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        execution_id: str,
        code_str: str,
        input_data: dict[str, Any] | list[Any],
        parameters: dict[str, Any] | None = None,
        script_hash: str = "sha256-verified",
        timeout_seconds: int = 30,
    ) -> SandboxExecutionResponse:
        """Sign request with scoped execution token and dispatch to Sandbox Agent."""
        token = create_scoped_execution_token(
            organization_id=organization_id,
            job_id=job_id,
            execution_id=execution_id,
            secret_key=self.secret_key,
            ttl_seconds=60,
        )

        req_payload = SandboxExecutionRequest(
            execution_id=execution_id,
            organization_id=organization_id,
            job_id=job_id,
            script_hash=script_hash,
            code_str=code_str,
            input_data=input_data,
            parameters=parameters or {},
            timeout_seconds=timeout_seconds,
        )

        headers = {
            "Content-Type": "application/json",
            "X-Sandbox-Token": token,
        }

        try:
            if self.app is not None:
                from starlette.testclient import TestClient

                with TestClient(self.app, base_url=self.base_url) as client:
                    resp = client.post(
                        "/api/v1/sandbox/execute",
                        json=req_payload.model_dump(mode="json"),
                        headers=headers,
                    )
            else:
                with httpx.Client(
                    base_url=self.base_url,
                    timeout=self.timeout_seconds,
                ) as client:
                    resp = client.post(
                        "/api/v1/sandbox/execute",
                        json=req_payload.model_dump(mode="json"),
                        headers=headers,
                    )

            if resp.status_code == 200:
                return SandboxExecutionResponse.model_validate(resp.json())
            else:
                return SandboxExecutionResponse(
                    execution_id=execution_id,
                    status="failed",
                    execution_time_ms=0,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr=f"Agent service error {resp.status_code}: {resp.text}",
                    error_message=f"Agent service error: {resp.status_code}",
                    isolation_backend="sandbox_agent_client",
                )
        except Exception as e:
            logger.error(f"Failed to communicate with Sandbox Agent service: {e}")
            return SandboxExecutionResponse(
                execution_id=execution_id,
                status="failed",
                execution_time_ms=0,
                memory_peak_mb=0.0,
                stdout="",
                stderr=f"Sandbox Agent connection failure: {e}",
                error_message="Sandbox Agent unavailable.",
                isolation_backend="sandbox_agent_client",
            )
