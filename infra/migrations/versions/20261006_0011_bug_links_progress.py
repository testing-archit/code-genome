"""SZZ-lite bug links per snapshot, plus live analysis stage and progress counts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0011"
down_revision: str | None = "20261006_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(sa.Column("stage", sa.String(48), nullable=True))
        batch.add_column(
            sa.Column("progress_counts", sa.JSON(), nullable=False, server_default="{}")
        )
    op.create_table(
        "bug_links",
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
        sa.Column("fix_sha", sa.String(64), nullable=False),
        sa.Column("introducing_sha", sa.String(64), nullable=False),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("lines", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("analysis_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "snapshot_id", "fix_sha", "introducing_sha", "path", name="uq_snapshot_bug_link"
        ),
    )
    op.create_index("ix_bug_links_workspace_id", "bug_links", ["workspace_id"])
    op.create_index("ix_bug_links_repository_id", "bug_links", ["repository_id"])
    op.create_index("ix_bug_links_snapshot_id", "bug_links", ["snapshot_id"])
    op.create_index("ix_bug_links_fix_sha", "bug_links", ["fix_sha"])
    op.create_index("ix_bug_links_introducing_sha", "bug_links", ["introducing_sha"])
    op.create_index("ix_bug_links_path", "bug_links", ["path"])


def downgrade() -> None:
    op.drop_table("bug_links")
    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_column("progress_counts")
        batch.drop_column("stage")
