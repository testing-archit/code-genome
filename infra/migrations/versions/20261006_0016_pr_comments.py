"""Opt-in pull-request impact comments per repository."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0016"
down_revision: str | None = "20261006_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("repositories") as batch:
        batch.add_column(
            sa.Column("pr_comments", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("repositories") as batch:
        batch.drop_column("pr_comments")
