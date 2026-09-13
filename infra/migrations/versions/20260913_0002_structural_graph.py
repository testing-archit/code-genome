"""Create immutable structural graph and provenance tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0002"
down_revision: str | None = "20260913_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repository_snapshots",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("repository_id", sa.String(length=32), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("tree_sha", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("analysis_version", sa.String(length=160), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "commit_sha", name="uq_repository_snapshot_commit"),
    )
    op.create_index("ix_repository_snapshots_commit_sha", "repository_snapshots", ["commit_sha"])
    op.create_index(
        "ix_repository_snapshots_repository_id", "repository_snapshots", ["repository_id"]
    )
    op.create_index(
        "ix_repository_snapshots_workspace_id", "repository_snapshots", ["workspace_id"]
    )
    op.create_table(
        "provenance",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("repository_id", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("repository_sha", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("start_column", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("end_column", sa.Integer(), nullable=True),
        sa.Column("extractor_version", sa.String(length=160), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["repository_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provenance_repository_id", "provenance", ["repository_id"])
    op.create_index("ix_provenance_repository_sha", "provenance", ["repository_sha"])
    op.create_index("ix_provenance_snapshot_id", "provenance", ["snapshot_id"])
    op.create_index("ix_provenance_workspace_id", "provenance", ["workspace_id"])
    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("natural_key", sa.String(length=1000), nullable=False),
        sa.Column("properties_json", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["repository_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "kind", "natural_key", name="uq_snapshot_node"),
    )
    op.create_index("ix_graph_nodes_kind", "graph_nodes", ["kind"])
    op.create_index("ix_graph_nodes_snapshot_id", "graph_nodes", ["snapshot_id"])
    op.create_index("ix_graph_nodes_workspace_id", "graph_nodes", ["workspace_id"])
    op.create_table(
        "graph_edges",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=32), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("from_node", sa.String(length=32), nullable=False),
        sa.Column("to_node", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provenance_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["from_node"], ["graph_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provenance_id"], ["provenance.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["repository_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_node"], ["graph_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_graph_edges_snapshot_id", "graph_edges", ["snapshot_id"])
    op.create_index("ix_graph_edges_type", "graph_edges", ["type"])
    op.create_index("ix_graph_edges_workspace_id", "graph_edges", ["workspace_id"])
    op.create_table(
        "parse_diagnostics",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("start_column", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("end_column", sa.Integer(), nullable=True),
        sa.Column("provenance_id", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["provenance_id"], ["provenance.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["repository_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_parse_diagnostics_code", "parse_diagnostics", ["code"])
    op.create_index("ix_parse_diagnostics_snapshot_id", "parse_diagnostics", ["snapshot_id"])
    op.create_index("ix_parse_diagnostics_workspace_id", "parse_diagnostics", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("parse_diagnostics")
    op.drop_table("graph_edges")
    op.drop_table("graph_nodes")
    op.drop_table("provenance")
    op.drop_table("repository_snapshots")
