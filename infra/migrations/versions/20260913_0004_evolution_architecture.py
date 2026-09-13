"""Add evolutionary intelligence and inferred architecture records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0004"
down_revision: str | None = "20260913_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope() -> tuple[sa.Column, ...]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "file_changes",
        *_scope(),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("churn", sa.Integer(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "commit_sha", "path", name="uq_snapshot_commit_file"),
    )
    op.create_table(
        "co_change_edges",
        *_scope(),
        sa.Column("left_path", sa.String(1000), nullable=False),
        sa.Column("right_path", sa.String(1000), nullable=False),
        sa.Column("commit_count", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_shas", sa.JSON(), nullable=False),
        sa.Column("analysis_version", sa.String(160), nullable=False),
        sa.UniqueConstraint("snapshot_id", "left_path", "right_path", name="uq_snapshot_cochange"),
    )
    op.create_table(
        "file_hotspots",
        *_scope(),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("commit_count", sa.Integer(), nullable=False),
        sa.Column("churn", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("evidence_shas", sa.JSON(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "path", name="uq_snapshot_hotspot"),
    )
    op.create_table(
        "module_candidates",
        *_scope(),
        sa.Column("natural_key", sa.String(1000), nullable=False),
        sa.Column("file_paths", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_shas", sa.JSON(), nullable=False),
        sa.Column("inferred", sa.Boolean(), nullable=False),
        sa.Column("analysis_version", sa.String(160), nullable=False),
        sa.UniqueConstraint("snapshot_id", "natural_key", name="uq_snapshot_module"),
    )
    for table in ("file_changes", "co_change_edges", "file_hotspots", "module_candidates"):
        for column in ("workspace_id", "repository_id", "snapshot_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index("ix_file_changes_commit_sha", "file_changes", ["commit_sha"])
    op.create_index("ix_file_changes_path", "file_changes", ["path"])
    op.create_index("ix_file_hotspots_score", "file_hotspots", ["score"])


def downgrade() -> None:
    for table in ("module_candidates", "file_hotspots", "co_change_edges", "file_changes"):
        op.drop_table(table)
