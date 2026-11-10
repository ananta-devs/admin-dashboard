"""init_baseline

Revision ID: f50478da1aca
Revises:
Create Date: 2026-11-01 09:43:38.253610

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "f50478da1aca"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
