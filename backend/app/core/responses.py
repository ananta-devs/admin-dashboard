from typing import Any

from pydantic import BaseModel, Field


class APIResponse(BaseModel):
    success: bool
    data: Any | None = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str = Field(default="ok")
    version: str = Field(default="1.0.0")
    timestamp: str


class ReadinessResponse(BaseModel):
    status: str
    database: str
    redis: str
    e2e_mode: bool | None = Field(default=None)
