from datetime import datetime
from enum import StrEnum
from typing import Any

from code_genome_git import normalize_github_url, validate_ref
from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime


class RepositoryCreate(BaseModel):
    clone_url: str = Field(max_length=500)
    default_branch: str = Field(default="main", min_length=1, max_length=255)

    @field_validator("clone_url")
    @classmethod
    def validate_clone_url(cls, value: str) -> str:
        return normalize_github_url(value)

    @field_validator("default_branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        return validate_ref(value)


class RepositoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    external_id: str
    clone_url: str
    default_branch: str
    status: str
    created_at: datetime


class AnalysisState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AnalysisCreate(BaseModel):
    refs: list[str] = Field(default_factory=list, max_length=1)
    simulate_failure: bool = False

    @field_validator("refs")
    @classmethod
    def validate_refs(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("refs must be unique")
        for value in values:
            validate_ref(value)
        return values


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    repository_id: str
    snapshot_sha: str | None
    requested_refs: list[str]
    version: str
    state: AnalysisState
    progress: float
    diagnostics: list[str]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_detail: str | None


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class GraphScope(BaseModel):
    repository_id: str
    snapshot_id: str
    snapshot_sha: str
    analysis_version: str
    sources: list[str]


class GraphNodeResponse(BaseModel):
    id: str
    kind: str
    natural_key: str
    properties: dict[str, Any]
    evidence_ids: list[str]


class GraphEdgeResponse(BaseModel):
    id: str
    type: str
    from_node: str
    to_node: str
    confidence: float
    evidence_id: str


class DiagnosticResponse(BaseModel):
    id: str
    code: str
    message: str
    path: str
    start_line: int | None
    start_column: int | None
    end_line: int | None
    end_column: int | None
    evidence_id: str | None


class GraphProjectionResponse(BaseModel):
    scope: GraphScope
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    diagnostics: list[DiagnosticResponse]
    next_cursor: str | None
    limitations: list[str]


class EvidenceResponse(BaseModel):
    id: str
    kind: str
    repository_sha: str
    path: str
    start_line: int | None
    start_column: int | None
    end_line: int | None
    end_column: int | None
    extractor_version: str
    observed_at: datetime
