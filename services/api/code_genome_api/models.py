from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Membership(Base):
    __tablename__ = "memberships"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    role: Mapped[str] = mapped_column(String(24), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", "external_id", name="uq_repository_source"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="github")
    external_id: Mapped[str] = mapped_column(String(255))
    clone_url: Mapped[str] = mapped_column(String(500))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    status: Mapped[str] = mapped_column(String(32), default="REGISTERED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    analyses: Mapped[list["AnalysisRun"]] = relationship(back_populates="repository")


class RepositoryConnection(Base):
    __tablename__ = "repository_connections"
    __table_args__ = (
        UniqueConstraint("repository_id", "provider", name="uq_repository_connection"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="github")
    token_kind: Mapped[str] = mapped_column(String(48))
    credential_ciphertext: Mapped[str] = mapped_column(Text)
    credential_nonce: Mapped[str] = mapped_column(String(64))
    key_version: Mapped[str] = mapped_column(String(80))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(120))
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BranchRef(Base):
    __tablename__ = "branch_refs"
    __table_args__ = (UniqueConstraint("repository_id", "name", name="uq_repository_branch"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    head_sha: Mapped[str] = mapped_column(String(64), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RepositoryCommit(Base):
    __tablename__ = "repository_commits"
    __table_args__ = (UniqueConstraint("repository_id", "sha", name="uq_repository_commit"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    sha: Mapped[str] = mapped_column(String(64), index=True)
    parent_shas: Mapped[list[str]] = mapped_column(JSON, default=list)
    author_name: Mapped[str] = mapped_column(String(500))
    author_email: Mapped[str] = mapped_column(String(500))
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    snapshot_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    version: Mapped[str] = mapped_column(String(80), default="structural-genome@0.1.0")
    state: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)
    diagnostics: Mapped[list[str]] = mapped_column(JSON, default=list)
    simulate_failure: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    repository: Mapped[Repository] = relationship(back_populates="analyses")


class RepositorySnapshot(Base):
    __tablename__ = "repository_snapshots"
    __table_args__ = (
        UniqueConstraint("repository_id", "commit_sha", name="uq_repository_snapshot_commit"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(64), index=True)
    tree_sha: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="RESTRICT"))
    analysis_version: Mapped[str] = mapped_column(String(160))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FileManifestEntry(Base):
    __tablename__ = "file_manifest_entries"
    __table_args__ = (UniqueConstraint("snapshot_id", "path", name="uq_snapshot_file_path"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("repository_snapshots.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(1000))
    blob_sha: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(12))
    size: Mapped[int] = mapped_column(Integer)
    analyzed: Mapped[bool] = mapped_column(default=False)


class Provenance(Base):
    __tablename__ = "provenance"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("repository_snapshots.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    repository_sha: Mapped[str] = mapped_column(String(64), index=True)
    file_path: Mapped[str] = mapped_column(String(1000))
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_column: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_column: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extractor_version: Mapped[str] = mapped_column(String(160))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GraphNode(Base):
    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "kind", "natural_key", name="uq_snapshot_node"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("repository_snapshots.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40), index=True)
    natural_key: Mapped[str] = mapped_column(String(1000))
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("repository_snapshots.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(40), index=True)
    from_node: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"))
    to_node: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"))
    confidence: Mapped[float] = mapped_column(Float)
    provenance_id: Mapped[str] = mapped_column(ForeignKey("provenance.id", ondelete="RESTRICT"))


class ParseDiagnostic(Base):
    __tablename__ = "parse_diagnostics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("repository_snapshots.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(80), index=True)
    message: Mapped[str] = mapped_column(String(500))
    file_path: Mapped[str] = mapped_column(String(1000))
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_column: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_column: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provenance_id: Mapped[str | None] = mapped_column(
        ForeignKey("provenance.id", ondelete="SET NULL"), nullable=True
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "endpoint", "key", name="uq_idempotency_scope"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(String(160))
    key: Mapped[str] = mapped_column(String(160))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(32))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(64), index=True)
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
