"""Unit tests for Phase 10 Backup & Disaster Recovery API Endpoints."""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.membership import OrganizationMembership
from app.models.user import User


@pytest.fixture
def mock_auth_user():
    return User(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        email="admin@enterprise.vibe",
        normalized_email="admin@enterprise.vibe",
        display_name="Admin",
        email_verified_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_membership():
    return OrganizationMembership(
        id=uuid.UUID("00000000-0000-0000-0000-000000000100"),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000010"),
    )


@pytest.mark.asyncio
async def test_get_backup_status_endpoint(monkeypatch, mock_auth_user, mock_membership):
    """Verify GET /api/v1/backups/status returns recovery posture, RPO, RTO, and spool metrics."""
    from app.api.dependencies import auth

    async def mock_user_dep():
        return mock_auth_user

    async def mock_membership_dep():
        return mock_membership

    app.dependency_overrides[auth.get_current_user] = mock_user_dep
    app.dependency_overrides[auth.get_current_membership] = mock_membership_dep
    app.dependency_overrides[auth.require_backup_read] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/backups/status")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "HEALTHY"
    assert "rpo" in data
    assert data["rpo"]["target_minutes"] == 15.0
    assert data["rpo"]["sla_compliant"] is True
    assert "rto" in data
    assert data["rto"]["target_minutes"] == 60.0
    assert "spool" in data
    assert "gfs_retention" in data


@pytest.mark.asyncio
async def test_get_backup_history_endpoint(mock_auth_user, mock_membership):
    """Verify GET /api/v1/backups/history returns executed backup records."""
    from app.api.dependencies import auth

    app.dependency_overrides[auth.get_current_user] = lambda: mock_auth_user
    app.dependency_overrides[auth.get_current_membership] = lambda: mock_membership
    app.dependency_overrides[auth.require_backup_read] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/backups/history")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    records = response.json()
    assert isinstance(records, list)
    assert len(records) >= 1
    assert "backup_type" in records[0]
    assert "start_lsn" in records[0]


@pytest.mark.asyncio
async def test_get_restore_verifications_endpoint(mock_auth_user, mock_membership):
    """Verify GET /api/v1/backups/verifications returns 16-point matrix outcomes."""
    from app.api.dependencies import auth

    app.dependency_overrides[auth.get_current_user] = lambda: mock_auth_user
    app.dependency_overrides[auth.get_current_membership] = lambda: mock_membership
    app.dependency_overrides[auth.require_backup_read] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/backups/verifications")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    verifications = response.json()
    assert isinstance(verifications, list)
    assert len(verifications) >= 1
    assert verifications[0]["all_points_passed"] is True
    assert verifications[0]["passed_checks_count"] == 16
