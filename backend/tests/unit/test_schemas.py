import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.membership import MembershipRead
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationRead,
    ProvisionOrganizationInput,
    validate_slug_format,
)
from app.schemas.user import UserCreate, normalize_email_address
from app.schemas.workspace import WorkspaceCreate, WorkspaceRead


def test_email_normalization():
    """Unit test: verifies email normalization trims whitespace and lowercases."""
    assert normalize_email_address("  Admin@AcmeCorp.COM  ") == "admin@acmecorp.com"
    assert normalize_email_address("USER.NAME+tag@DOMAIN.CO.UK") == "user.name+tag@domain.co.uk"


def test_user_create_validation():
    """Unit test: verifies UserCreate schema accepts valid inputs and normalizes email."""
    user_in = UserCreate(email="  TEST@Example.com ", display_name="Test User")
    assert user_in.email == "test@example.com"
    assert user_in.display_name == "Test User"

    with pytest.raises(ValidationError):
        UserCreate(email="invalid-email-no-at", display_name="Invalid")

    with pytest.raises(ValidationError):
        UserCreate(email="valid@email.com", display_name="")


def test_slug_validation():
    """Unit test: verifies slug validator accepts valid slugs and rejects invalid ones."""
    assert validate_slug_format("acme-corp") == "acme-corp"
    assert validate_slug_format("workspace-123") == "workspace-123"
    assert validate_slug_format("simple") == "simple"

    # Rejection of uppercase, spaces, special characters, or consecutive hyphens
    with pytest.raises(ValueError, match="Slug must consist of lowercase"):
        validate_slug_format("Acme-Corp")

    with pytest.raises(ValueError, match="Slug must consist of lowercase"):
        validate_slug_format("acme--corp")

    with pytest.raises(ValueError, match="Slug must consist of lowercase"):
        validate_slug_format("-leading-hyphen")

    with pytest.raises(ValueError, match="Slug must consist of lowercase"):
        validate_slug_format("trailing-hyphen-")

    with pytest.raises(ValueError, match="Slug must consist of lowercase"):
        validate_slug_format("has spaces in slug")


def test_organization_schemas():
    """Unit test: verifies OrganizationCreate and OrganizationRead schemas."""
    org_in = OrganizationCreate(name="Acme Corporation", slug="acme-corp")
    assert org_in.name == "Acme Corporation"
    assert org_in.slug == "acme-corp"

    now = datetime.now(UTC)
    org_read = OrganizationRead(
        id=uuid.uuid4(),
        name="Acme",
        slug="acme",
        created_at=now,
        updated_at=now,
    )
    assert org_read.name == "Acme"


def test_workspace_schemas():
    """Unit test: verifies WorkspaceCreate and WorkspaceRead schemas."""
    ws_in = WorkspaceCreate(name="Financial Modeling", slug="fin-model")
    assert ws_in.name == "Financial Modeling"
    assert ws_in.slug == "fin-model"

    now = datetime.now(UTC)
    ws_read = WorkspaceRead(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name="Fin Model",
        slug="fin-model",
        created_at=now,
        updated_at=now,
    )
    assert ws_read.slug == "fin-model"


def test_membership_schema():
    """Unit test: verifies MembershipRead schema mapping."""
    now = datetime.now(UTC)
    mem_read = MembershipRead(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        created_at=now,
    )
    assert isinstance(mem_read.id, uuid.UUID)


def test_provision_organization_input_validation():
    """Unit test: verifies ProvisionOrganizationInput validates both slugs."""
    valid = ProvisionOrganizationInput(
        organization_name="Global Tech",
        organization_slug="global-tech",
        user_email="ceo@globaltech.com",
        user_display_name="Chief Executive",
        default_workspace_name="Core Operations",
        default_workspace_slug="operations",
    )
    assert valid.organization_slug == "global-tech"
    assert valid.default_workspace_slug == "operations"

    with pytest.raises(ValidationError):
        ProvisionOrganizationInput(
            organization_name="Global Tech",
            organization_slug="INVALID SLUG",
            user_email="ceo@globaltech.com",
            user_display_name="CEO",
        )
