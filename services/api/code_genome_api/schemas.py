from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from code_genome_git import normalize_github_url, validate_ref
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .services.diffs import DiffError, normalize_repository_path


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


class CredentialKind(StrEnum):
    FINE_GRAINED_PAT = "fine_grained_pat"
    INSTALLATION_TOKEN = "installation_token"


class RepositoryConnectionPut(BaseModel):
    token: SecretStr = Field(min_length=8, max_length=1024)
    token_kind: CredentialKind = CredentialKind.FINE_GRAINED_PAT
    scopes: list[str] = Field(default_factory=lambda: ["contents:read"], max_length=20)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip().lower() for value in values})
        if any(not value or len(value) > 80 for value in normalized):
            raise ValueError("Scopes must be non-empty and at most 80 characters")
        return normalized


class RepositoryConnectionResponse(BaseModel):
    connected: bool
    connection_id: str | None
    provider: str
    token_kind: CredentialKind | None
    scopes: list[str]
    key_version: str | None
    installed_at: datetime | None
    revoked_at: datetime | None


class BranchRefResponse(BaseModel):
    name: str
    head_sha: str
    observed_at: datetime


class CommitResponse(BaseModel):
    sha: str
    parent_shas: list[str]
    author_name: str
    authored_at: datetime
    message: str


class FileManifestResponse(BaseModel):
    path: str
    blob_sha: str
    mode: str
    size: int
    analyzed: bool


class RepositoryInventoryResponse(BaseModel):
    repository_id: str
    snapshot_sha: str | None
    refs: list[BranchRefResponse]
    commits: list[CommitResponse]
    files: list[FileManifestResponse]
    limitations: list[str]


class ArchitectureModuleResponse(BaseModel):
    id: str
    name: str
    file_paths: list[str]
    confidence: float
    description: str
    citations: list[str]
    inferred: bool


class ArchitectureHotspotResponse(BaseModel):
    path: str
    commit_count: int
    churn: int
    score: float
    citations: list[str]


class ArchitectureCoChangeResponse(BaseModel):
    left_path: str
    right_path: str
    commit_count: int
    confidence: float
    citations: list[str]


class ArchitectureResponse(BaseModel):
    repository_id: str
    snapshot_sha: str
    analysis_version: str
    modules: list[ArchitectureModuleResponse]
    hotspots: list[ArchitectureHotspotResponse]
    co_changes: list[ArchitectureCoChangeResponse]
    limitations: list[str]


class DeliveryScope(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime
    branches: list[str] = Field(default_factory=lambda: ["main"], min_length=1, max_length=10)
    include_prs: bool = False
    include_ci: bool = False
    include_deployments: bool = False

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("branches")
    @classmethod
    def validate_scope_branches(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("branches must be unique")
        return [validate_ref(value) for value in values]


class DeliveryReportCreate(BaseModel):
    repository_id: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=100_000)
    scope: DeliveryScope


class DeliveryAssessmentResponse(BaseModel):
    status: str
    confidence: float
    rationale: str
    evidence_ids: list[str]
    limitations: list[str]
    analysis_version: str
    assessed_at: datetime


class DeliveryClaimResponse(BaseModel):
    id: str
    ordinal: int
    original_text: str
    start_offset: int
    end_offset: int
    claim_type: str
    assessment: DeliveryAssessmentResponse | None


class UnreportedChangeResponse(BaseModel):
    path: str
    evidence_ids: list[str]
    materiality: float
    explanation: str


class DeliveryReportResponse(BaseModel):
    id: str
    repository_id: str
    raw_text: str
    scope: DeliveryScope
    submitted_by: str
    parser_version: str
    created_at: datetime
    claims: list[DeliveryClaimResponse]
    unreported_changes: list[UnreportedChangeResponse]
    limitations: list[str]


class RiskScoreResponse(BaseModel):
    path: str
    score: float
    features: dict[str, float]
    rationale: str
    evidence_ids: list[str]
    model_version: str


class RiskResponse(BaseModel):
    repository_id: str
    snapshot_sha: str
    scores: list[RiskScoreResponse]
    limitations: list[str]


class ImpactItemResponse(BaseModel):
    path: str
    score: float
    reasons: list[str]
    evidence_ids: list[str]


class ImpactResponse(BaseModel):
    repository_id: str
    snapshot_sha: str
    selected_path: str
    impacted: list[ImpactItemResponse]
    limitations: list[str]


AnswerLanguage = Literal["auto", "en", "hi", "hinglish"]


class GroundedAnswerCreate(BaseModel):
    repository_id: str = Field(min_length=1, max_length=32)
    question: str = Field(min_length=2, max_length=2000)
    channel: Literal["text", "voice"] = "text"
    language: AnswerLanguage = "auto"


class GroundedAnswerResponse(BaseModel):
    id: str
    repository_id: str
    question: str
    answer: str
    evidence_ids: list[str]
    scope: dict[str, Any]
    limitations: list[str]
    retrieval_version: str
    created_at: datetime


class AnswerFeedbackCreate(BaseModel):
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=2000)


class AnswerFeedbackResponse(BaseModel):
    answer_id: str
    rating: int
    recorded: bool


class RetentionRunCreate(BaseModel):
    dry_run: bool = True


class RetentionRunResponse(BaseModel):
    cutoff: datetime
    dry_run: bool
    delivery_reports: int
    grounded_answers: int


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)


class ConversationMessageCreate(BaseModel):
    content: str = Field(min_length=2, max_length=2000)
    channel: Literal["text", "voice"] = "text"
    language: AnswerLanguage = "auto"


class ConversationMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    channel: str
    content: str
    created_at: datetime
    answer: GroundedAnswerResponse | None = None


class ConversationSummaryResponse(BaseModel):
    id: str
    repository_id: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class ConversationResponse(ConversationSummaryResponse):
    messages: list[ConversationMessageResponse]


class ConversationTurnResponse(BaseModel):
    conversation: ConversationSummaryResponse
    user_message: ConversationMessageResponse
    assistant_message: ConversationMessageResponse


class VoiceSessionCreate(BaseModel):
    repository_id: str = Field(min_length=1, max_length=32)
    voice: Literal["Kore", "Puck", "Charon", "Aoede", "Fenrir", "Leda", "Orus", "Zephyr"] = "Kore"
    language: AnswerLanguage = "auto"


class VoiceSessionResponse(BaseModel):
    id: str
    repository_id: str
    snapshot_sha: str
    model: str
    voice: str
    language: str
    websocket_url: str
    token: str
    expires_at: datetime
    new_session_expires_at: datetime
    setup: dict[str, Any]
    limitations: list[str]


class MlTaskResponse(BaseModel):
    task: str
    model_version: str
    status: str
    trained_at: datetime
    result: dict[str, Any]


class MlOverviewResponse(BaseModel):
    repository_id: str
    snapshot_sha: str
    trained: bool
    tasks: dict[str, MlTaskResponse]
    limitations: list[str]


class SearchHitResponse(BaseModel):
    id: str
    kind: str
    title: str
    path: str | None
    score: float
    bm25: float
    semantic: float
    bm25_rank: int | None
    semantic_rank: int | None
    match: Literal["keyword and semantic", "keyword", "semantic (inferred)"]


class SearchResponse(BaseModel):
    query: str
    snapshot_sha: str
    model_version: str
    hits: list[SearchHitResponse]
    message: str | None
    limitations: list[str]


class ChangeImpactCreate(BaseModel):
    """A proposed change, given as a unified diff, a list of paths, or both."""

    diff: str | None = Field(default=None, max_length=900_000)
    paths: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, value: list[str]) -> list[str]:
        try:
            return [normalize_repository_path(item) for item in value]
        except DiffError as error:
            raise ValueError(str(error)) from error


class ChangedFileResponse(BaseModel):
    path: str
    change: Literal["added", "modified", "deleted", "renamed", "listed"]
    previous_path: str | None
    additions: int
    deletions: int
    in_snapshot: bool
    risk_score: float | None
    risk_rationale: str | None
    risk_model: str | None
    modules: list[str]
    evidence_ids: list[str]


class ChangeImpactItemResponse(BaseModel):
    path: str
    score: float
    reasons: list[str]
    evidence_ids: list[str]
    via: list[str]
    modules: list[str]


class ChangeModuleResponse(BaseModel):
    name: str
    changed_files: int
    impacted_files: int
    inferred: bool


class ChangeImpactSummary(BaseModel):
    changed_files: int
    changed_in_snapshot: int
    impacted_files: int
    modules_touched: int
    max_risk: float | None
    high_risk_files: int


class ChangeImpactResponse(BaseModel):
    repository_id: str
    snapshot_id: str
    snapshot_sha: str
    analysis_version: str
    generated_at: datetime
    summary: ChangeImpactSummary
    changed: list[ChangedFileResponse]
    impacted: list[ChangeImpactItemResponse]
    modules: list[ChangeModuleResponse]
    limitations: list[str]


class SnapshotSummaryResponse(BaseModel):
    id: str
    commit_sha: str
    tree_sha: str
    analysis_version: str
    run_id: str
    refs: list[str]
    published_at: datetime


class ComparedFileResponse(BaseModel):
    path: str
    base_blob_sha: str | None
    head_blob_sha: str | None
    size_delta: int


class ComparedImportResponse(BaseModel):
    source: str
    target: str
    evidence_id: str


class ComparedModuleResponse(BaseModel):
    name: str
    status: Literal["added", "removed", "changed"]
    added_files: list[str]
    removed_files: list[str]
    inferred: bool


class ComparedHotspotResponse(BaseModel):
    path: str
    base_score: float | None
    head_score: float | None
    delta: float


class ComparisonCounts(BaseModel):
    files_added: int
    files_removed: int
    files_modified: int
    imports_added: int
    imports_removed: int
    modules_changed: int


class SnapshotComparisonResponse(BaseModel):
    repository_id: str
    base: SnapshotSummaryResponse
    head: SnapshotSummaryResponse
    generated_at: datetime
    unavailable: list[Literal["files", "imports", "modules", "hotspots"]]
    counts: ComparisonCounts
    files_added: list[ComparedFileResponse]
    files_removed: list[ComparedFileResponse]
    files_modified: list[ComparedFileResponse]
    imports_added: list[ComparedImportResponse]
    imports_removed: list[ComparedImportResponse]
    modules: list[ComparedModuleResponse]
    hotspots: list[ComparedHotspotResponse]
    limitations: list[str]


class RepositoryAutomationPut(BaseModel):
    auto_analyze: bool


class RepositoryAutomationResponse(BaseModel):
    repository_id: str
    auto_analyze: bool
    branch: str
    webhook_configured: bool
    webhook_path: str
    events: list[str]


class WebhookResultResponse(BaseModel):
    delivery_id: str
    event: str
    outcome: Literal["queued", "coalesced", "ignored", "duplicate", "pong"]
    queued_run_ids: list[str]
    detail: str
