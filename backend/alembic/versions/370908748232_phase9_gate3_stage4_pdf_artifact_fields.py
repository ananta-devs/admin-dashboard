"""phase9_gate3_stage4_pdf_artifact_fields

Revision ID: 370908748232
Revises: 370908748231
Create Date: 2026-11-03 22:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "370908748232"
down_revision: Union[str, None] = "370908748231"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("report_runs", sa.Column("pdf_artifact_path", sa.String(length=500), nullable=True))
    op.add_column("report_runs", sa.Column("pdf_checksum_sha256", sa.String(length=64), nullable=True))
    op.add_column("report_runs", sa.Column("pdf_file_size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("report_runs", sa.Column("pdf_page_count", sa.Integer(), nullable=True))
    op.add_column("report_runs", sa.Column("pdf_rendered_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_report_runs_pdf_metrics",
        "report_runs",
        "(pdf_file_size_bytes IS NULL OR pdf_file_size_bytes >= 0) AND (pdf_page_count IS NULL OR pdf_page_count > 0)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_report_runs_pdf_metrics", "report_runs", type_="check")
    op.drop_column("report_runs", "pdf_rendered_at")
    op.drop_column("report_runs", "pdf_page_count")
    op.drop_column("report_runs", "pdf_file_size_bytes")
    op.drop_column("report_runs", "pdf_checksum_sha256")
    op.drop_column("report_runs", "pdf_artifact_path")

