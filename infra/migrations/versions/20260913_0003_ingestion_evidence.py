"""Add private connections and repository ingestion evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0003"
down_revision: str | None = "20260913_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("repository_id", sa.String(length=32), nullable=False),
    ]


def _tenant_constraints() -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
    ]


def upgrade() -> None:
    op.create_table(
        "repository_connections",
        *_tenant_columns(),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("token_kind", sa.String(length=48), nullable=False),
        sa.Column("credential_ciphertext", sa.Text(), nullable=False),
        sa.Column("credential_nonce", sa.String(length=64), nullable=False),
        sa.Column("key_version", sa.String(length=80), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant_constraints(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "provider", name="uq_repository_connection"),
    )
    op.create_table(
        "branch_refs",
        *_tenant_columns(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("head_sha", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "name", name="uq_repository_branch"),
    )
    op.create_table(
        "repository_commits",
        *_tenant_columns(),
        sa.Column("sha", sa.String(length=64), nullable=False),
        sa.Column("parent_shas", sa.JSON(), nullable=False),
        sa.Column("author_name", sa.String(length=500), nullable=False),
        sa.Column("author_email", sa.String(length=500), nullable=False),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "sha", name="uq_repository_commit"),
    )
    op.create_table(
        "file_manifest_entries",
        *_tenant_columns(),
        sa.Column("snapshot_id", sa.String(length=32), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("blob_sha", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("analyzed", sa.Boolean(), nullable=False),
        *_tenant_constraints(),
        sa.ForeignKeyConstraint(["snapshot_id"], ["repository_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "path", name="uq_snapshot_file_path"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("before_hash", sa.String(length=64), nullable=True),
        sa.Column("after_hash", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for table, columns in {
        "repository_connections": ("workspace_id", "repository_id"),
        "branch_refs": ("workspace_id", "repository_id", "head_sha"),
        "repository_commits": ("workspace_id", "repository_id", "sha"),
        "file_manifest_entries": ("workspace_id", "repository_id", "snapshot_id", "blob_sha"),
        "audit_events": ("workspace_id", "actor_id", "action", "resource_id", "request_id"),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in (
        "audit_events",
        "file_manifest_entries",
        "repository_commits",
        "branch_refs",
        "repository_connections",
    ):
        op.drop_table(table)
