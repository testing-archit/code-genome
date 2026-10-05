"""Check foreign keys at commit on Postgres so one flush can insert parents and children.

The ORM declares few relationships, so a single flush may send a child row before its parent.
SQLite does not enforce foreign keys by default; Postgres does, per statement. Deferring the
check to commit keeps every constraint while making insert order within a transaction
irrelevant. SQLite is left unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0012"
down_revision: str | None = "20261006_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _recreate_foreign_keys(*, deferred: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        for fk in inspector.get_foreign_keys(table):
            name = fk["name"]
            if not name:
                continue
            ondelete = (fk.get("options") or {}).get("ondelete")
            op.drop_constraint(name, table, type_="foreignkey")
            op.create_foreign_key(
                name,
                table,
                fk["referred_table"],
                fk["constrained_columns"],
                fk["referred_columns"],
                ondelete=ondelete,
                deferrable=True if deferred else None,
                initially="DEFERRED" if deferred else None,
            )


def upgrade() -> None:
    _recreate_foreign_keys(deferred=True)


def downgrade() -> None:
    _recreate_foreign_keys(deferred=False)
