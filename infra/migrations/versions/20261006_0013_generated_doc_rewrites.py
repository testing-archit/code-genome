"""Model-written versions of generated documents, stored beside their deterministic source."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0013"
down_revision: str | None = "20261006_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk(target: str) -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete="CASCADE", deferrable=True, initially="DEFERRED")


def upgrade() -> None:
    op.create_table(
        "generated_doc_rewrites",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("workspace_id", sa.String(32), _fk("workspaces.id"), nullable=False),
        sa.Column("repository_id", sa.String(32), _fk("repositories.id"), nullable=False),
        sa.Column("snapshot_id", sa.String(32), _fk("repository_snapshots.id"), nullable=False),
        sa.Column("snapshot_sha", sa.String(64), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("docs_version", sa.String(80), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("rewrite_version", sa.String(80), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "snapshot_id", "name", "docs_version", "model", "source_sha256", name="uq_doc_rewrite"
        ),
    )
    for column in ("workspace_id", "repository_id", "snapshot_id"):
        op.create_index(f"ix_generated_doc_rewrites_{column}", "generated_doc_rewrites", [column])


def downgrade() -> None:
    op.drop_table("generated_doc_rewrites")
