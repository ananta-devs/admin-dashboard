from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permission import Permission


class PermissionRepository:
    """Repository handling read operations for application permissions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> Sequence[Permission]:
        """List all defined system permissions ordered by category and code."""
        stmt = select(Permission).order_by(Permission.category, Permission.code)
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def get_by_code(self, code: str) -> Permission | None:
        """Fetch permission by exact code."""
        stmt = select(Permission).where(Permission.code == code)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_by_codes(self, codes: list[str]) -> Sequence[Permission]:
        """Fetch multiple permissions by their codes."""
        if not codes:
            return []
        stmt = select(Permission).where(Permission.code.in_(codes))
        res = await self.session.execute(stmt)
        return res.scalars().all()
