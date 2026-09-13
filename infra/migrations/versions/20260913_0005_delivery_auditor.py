"""Add delivery reports, atomic claims, assessments, and unreported changes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0005"
down_revision: str | None = "20260913_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_reports",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(32),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("submitted_by", sa.String(120), nullable=False),
        sa.Column("parser_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "delivery_claims",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(32),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "report_id",
            sa.String(32),
            sa.ForeignKey("delivery_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("claim_type", sa.String(48), nullable=False),
        sa.UniqueConstraint("report_id", "ordinal", name="uq_report_claim_ordinal"),
    )
    op.create_table(
        "delivery_assessments",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(32),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.String(32),
            sa.ForeignKey("delivery_claims.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(48), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("analysis_version", sa.String(80), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "unreported_changes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(32),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "report_id",
            sa.String(32),
            sa.ForeignKey("delivery_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("materiality", sa.Float(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.UniqueConstraint("report_id", "path", name="uq_report_unreported_path"),
    )
    for table in (
        "delivery_reports",
        "delivery_claims",
        "delivery_assessments",
        "unreported_changes",
    ):
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
    op.create_index("ix_delivery_reports_repository_id", "delivery_reports", ["repository_id"])
    op.create_index("ix_delivery_claims_report_id", "delivery_claims", ["report_id"])
    op.create_index("ix_delivery_assessments_claim_id", "delivery_assessments", ["claim_id"])
    op.create_index("ix_delivery_assessments_status", "delivery_assessments", ["status"])
    op.create_index("ix_unreported_changes_report_id", "unreported_changes", ["report_id"])


def downgrade() -> None:
    for table in (
        "unreported_changes",
        "delivery_assessments",
        "delivery_claims",
        "delivery_reports",
    ):
        op.drop_table(table)
