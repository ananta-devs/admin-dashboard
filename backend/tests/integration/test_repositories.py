import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.user import User
from app.models.workspace import Workspace
from app.repositories.membership import MembershipRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.user import UserRepository
from app.repositories.workspace import WorkspaceRepository


@pytest.mark.asyncio
async def test_organization_repository_crud_and_uniqueness(
    db_session: AsyncSession, unique_id: str
):
    """Integration test: verifies Organization persistence, retrieval, and unique slug constraint."""
    repo = OrganizationRepository(db_session)
    slug = f"org-{unique_id}"

    org = Organization(name="Test Org", slug=slug)
    await repo.create(org)
    assert org.id is not None
    assert org.created_at is not None

    by_id = await repo.get_by_id(org.id)
    assert by_id is not None
    assert by_id.slug == slug

    by_slug = await repo.get_by_slug(slug)
    assert by_slug is not None
    assert by_slug.id == org.id

    # Test database constraint enforcement: duplicate slug must fail
    duplicate_org = Organization(name="Duplicate Org", slug=slug)
    with pytest.raises(IntegrityError):
        await repo.create(duplicate_org)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_user_repository_crud_and_uniqueness(db_session: AsyncSession, unique_id: str):
    """Integration test: verifies User persistence and unique normalized email constraint."""
    repo = UserRepository(db_session)
    email = f"user_{unique_id}@example.com"

    user = User(
        email=email,
        normalized_email=email.lower(),
        display_name=f"User {unique_id}",
    )
    await repo.create(user)
    assert user.id is not None

    by_email = await repo.get_by_normalized_email(email.lower())
    assert by_email is not None
    assert by_email.id == user.id

    # Test duplicate normalized email violates DB unique constraint
    dup_user = User(
        email=email.upper(),
        normalized_email=email.lower(),
        display_name="Duplicate",
    )
    with pytest.raises(IntegrityError):
        await repo.create(dup_user)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_membership_repository_composite_uniqueness(db_session: AsyncSession, unique_id: str):
    """Integration test: verifies OrganizationMembership composite uniqueness constraint."""
    org_repo = OrganizationRepository(db_session)
    user_repo = UserRepository(db_session)
    mem_repo = MembershipRepository(db_session)

    org = Organization(name="Membership Org", slug=f"mem-org-{unique_id}")
    await org_repo.create(org)

    user = User(
        email=f"mem_{unique_id}@example.com",
        normalized_email=f"mem_{unique_id}@example.com",
        display_name="Mem User",
    )
    await user_repo.create(user)

    membership = OrganizationMembership(organization_id=org.id, user_id=user.id)
    await mem_repo.create(membership)
    assert membership.id is not None

    # Duplicate membership link must violate composite unique constraint
    duplicate_mem = OrganizationMembership(organization_id=org.id, user_id=user.id)
    with pytest.raises(IntegrityError):
        await mem_repo.create(duplicate_mem)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_workspace_repository_tenant_scoping_and_uniqueness(
    db_session: AsyncSession, unique_id: str
):
    """Integration test: verifies Workspace scoping to organization and per-org slug uniqueness."""
    org_repo = OrganizationRepository(db_session)
    ws_repo = WorkspaceRepository(db_session)

    org1 = Organization(name="Tenant 1", slug=f"t1-{unique_id}")
    org2 = Organization(name="Tenant 2", slug=f"t2-{unique_id}")
    await org_repo.create(org1)
    await org_repo.create(org2)

    # Org1 creates workspace 'analytics'
    ws1 = Workspace(organization_id=org1.id, name="Analytics", slug="analytics")
    await ws_repo.create(ws1)

    # Same slug under Org2 is ALLOWED because constraint is (organization_id, slug)
    ws2 = Workspace(organization_id=org2.id, name="Analytics Org 2", slug="analytics")
    await ws_repo.create(ws2)
    assert ws1.id != ws2.id

    # Duplicate slug within the SAME organization must fail
    ws_dup = Workspace(organization_id=org1.id, name="Another Analytics", slug="analytics")
    with pytest.raises(IntegrityError):
        await ws_repo.create(ws_dup)
    await db_session.rollback()

    # Verify strict tenant scoping: Org2 cannot retrieve ws1
    cross_lookup = await ws_repo.get_by_id(ws1.id, organization_id=org2.id)
    assert cross_lookup is None


@pytest.mark.asyncio
async def test_foreign_key_cascade_deletes(db_session: AsyncSession, unique_id: str):
    """Integration test: verifies database cascade delete cleans up child memberships and workspaces."""
    org_repo = OrganizationRepository(db_session)
    user_repo = UserRepository(db_session)
    mem_repo = MembershipRepository(db_session)
    ws_repo = WorkspaceRepository(db_session)

    org = Organization(name="Cascade Org", slug=f"cascade-{unique_id}")
    await org_repo.create(org)

    user = User(
        email=f"cascade_{unique_id}@example.com",
        normalized_email=f"cascade_{unique_id}@example.com",
        display_name="Cascade User",
    )
    await user_repo.create(user)

    membership = OrganizationMembership(organization_id=org.id, user_id=user.id)
    await mem_repo.create(membership)

    workspace = Workspace(organization_id=org.id, name="WS", slug="ws")
    await ws_repo.create(workspace)

    # Delete parent organization
    await org_repo.delete(org)

    # Verify child records were automatically cascade deleted by PostgreSQL
    assert await ws_repo.get_by_id(workspace.id, org.id) is None
    assert await mem_repo.get(org.id, user.id) is None
    # User itself remains intact
    assert await user_repo.get_by_id(user.id) is not None
