"""Opt-in push-triggered re-analysis and GitHub webhook delivery replay records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0009"
down_revision: str | None = "20260929_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("repositories") as batch:
        batch.add_column(
            sa.Column("auto_analyze", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("delivery_id", sa.String(80), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("queued_runs", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "delivery_id", name="uq_webhook_delivery"),
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    with op.batch_alter_table("repositories") as batch:
        batch.drop_column("auto_analyze")
