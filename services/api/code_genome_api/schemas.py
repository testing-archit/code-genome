import re
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

BRANCH_PATTERN = re.compile(r"^(?!/|.*(?:\.\.|//|@\{|\\|\s))[^~^:?*\[]+(?<![/.])$")


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
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("clone_url must be a credential-free HTTPS GitHub URL")
        parts = [part for part in parsed.path.removesuffix(".git").split("/") if part]
        if len(parts) != 2 or any(part in {".", ".."} for part in parts):
            raise ValueError("clone_url must identify one GitHub owner and repository")
        return f"https://github.com/{parts[0]}/{parts[1]}.git"

    @field_validator("default_branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        if not BRANCH_PATTERN.fullmatch(value):
            raise ValueError("default_branch is not a valid Git ref name")
        return value


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
    refs: list[str] = Field(default_factory=list, max_length=20)
    simulate_failure: bool = False

    @field_validator("refs")
    @classmethod
    def validate_refs(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("refs must be unique")
        for value in values:
            if len(value) > 255 or not BRANCH_PATTERN.fullmatch(value):
                raise ValueError(f"invalid Git ref: {value}")
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
