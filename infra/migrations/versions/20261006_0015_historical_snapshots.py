"""Analyse a past commit or date: the requested point on runs, and on the snapshots they make."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0015"
down_revision: str | None = "20261006_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(sa.Column("as_of", sa.String(64), nullable=True))
    with op.batch_alter_table("repository_snapshots") as batch:
        batch.add_column(sa.Column("as_of", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("repository_snapshots") as batch:
        batch.drop_column("as_of")
    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_column("as_of")
