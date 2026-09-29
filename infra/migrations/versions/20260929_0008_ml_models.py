"""Store trained repository models and their evaluations per snapshot."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260929_0008"
down_revision: str | None = "20260929_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ml_model_runs",
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
        sa.Column("snapshot_sha", sa.String(64), nullable=False),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("trained_by", sa.String(120), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("snapshot_id", "task", name="uq_snapshot_ml_task"),
    )
    op.create_index("ix_ml_model_runs_workspace_id", "ml_model_runs", ["workspace_id"])
    op.create_index("ix_ml_model_runs_repository_id", "ml_model_runs", ["repository_id"])
    op.create_index("ix_ml_model_runs_snapshot_id", "ml_model_runs", ["snapshot_id"])


def downgrade() -> None:
    op.drop_table("ml_model_runs")
