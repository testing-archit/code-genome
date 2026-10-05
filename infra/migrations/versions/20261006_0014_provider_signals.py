"""CI and deployment evidence read from GitHub, with raw payloads, per repository commit."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0014"
down_revision: str | None = "20261006_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk(target: str) -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete="CASCADE", deferrable=True, initially="DEFERRED")


def upgrade() -> None:
    op.create_table(
        "provider_signals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("workspace_id", sa.String(32), _fk("workspaces.id"), nullable=False),
        sa.Column("repository_id", sa.String(32), _fk("repositories.id"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("environment", sa.String(100), nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.Column("analysis_version", sa.String(80), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "external_id", name="uq_repository_provider_signal"),
    )
    for column in ("workspace_id", "repository_id", "commit_sha"):
        op.create_index(f"ix_provider_signals_{column}", "provider_signals", [column])


def downgrade() -> None:
    op.drop_table("provider_signals")
