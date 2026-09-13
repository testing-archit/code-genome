"""Add explainable risk scores and grounded answers."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0006"
down_revision: str | None = "20260913_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_scores",
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
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("snapshot_id", "path", name="uq_snapshot_risk_path"),
    )
    op.create_table(
        "grounded_answers",
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
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("retrieval_version", sa.String(80), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "answer_feedback",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(32),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "answer_id",
            sa.String(32),
            sa.ForeignKey("grounded_answers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("answer_id", "user_id", name="uq_answer_feedback_user"),
    )
    for table in ("risk_scores", "grounded_answers", "answer_feedback"):
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
    op.create_index("ix_risk_scores_repository_id", "risk_scores", ["repository_id"])
    op.create_index("ix_risk_scores_snapshot_id", "risk_scores", ["snapshot_id"])
    op.create_index("ix_risk_scores_score", "risk_scores", ["score"])
    op.create_index("ix_grounded_answers_repository_id", "grounded_answers", ["repository_id"])
    op.create_index("ix_answer_feedback_answer_id", "answer_feedback", ["answer_id"])


def downgrade() -> None:
    op.drop_table("answer_feedback")
    op.drop_table("grounded_answers")
    op.drop_table("risk_scores")
