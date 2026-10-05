"""Cited excerpts of docs, manifests, and source used for repository Q&A and voice."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0010"
down_revision: str | None = "20261006_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks",
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
        sa.Column(
            "snapshot_id",
            sa.String(32),
            sa.ForeignKey("repository_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provenance_id",
            sa.String(32),
            sa.ForeignKey("provenance.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("blob_sha", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(200), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("analysis_version", sa.String(80), nullable=False),
        sa.UniqueConstraint("snapshot_id", "path", "ordinal", name="uq_snapshot_knowledge_chunk"),
    )
    op.create_index("ix_knowledge_chunks_workspace_id", "knowledge_chunks", ["workspace_id"])
    op.create_index("ix_knowledge_chunks_repository_id", "knowledge_chunks", ["repository_id"])
    op.create_index("ix_knowledge_chunks_snapshot_id", "knowledge_chunks", ["snapshot_id"])
    op.create_index("ix_knowledge_chunks_kind", "knowledge_chunks", ["kind"])


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
