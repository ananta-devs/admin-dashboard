import time
from datetime import UTC, datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.responses import HealthResponse, ReadinessResponse
from app.db.session import get_db_session

router = APIRouter(tags=["Health & Diagnostics"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", version="1.0.0", timestamp=datetime.now(UTC).isoformat())


@router.get("/ready", response_model=ReadinessResponse)
async def readiness_check(db: AsyncSession = Depends(get_db_session)):
    db_status = "unavailable"
    redis_status = "unavailable"

    # 1. Check PostgreSQL
    try:
        t0 = time.time()
        result = await db.execute(text("SELECT 1"))
        if result.scalar() == 1:
            latency = (time.time() - t0) * 1000
            db_status = f"connected ({latency:.1f}ms)"
    except Exception as e:
        db_status = f"error: {e!s}"

    # 2. Check Redis
    try:
        t0 = time.time()
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        pong = await r.ping()
        await r.aclose()
        if pong:
            latency = (time.time() - t0) * 1000
            redis_status = f"connected ({latency:.1f}ms)"
    except Exception as e:
        redis_status = f"error: {e!s}"

    all_ready = "connected" in db_status and "connected" in redis_status
    status_code = status.HTTP_200_OK if all_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    content = {
        "status": "ready" if all_ready else "degraded",
        "database": db_status,
        "redis": redis_status,
    }
    if getattr(settings, "E2E_EXPECTED_DATABASE", None):
        content["e2e_mode"] = True

    return JSONResponse(
        status_code=status_code,
        content=content,
    )
