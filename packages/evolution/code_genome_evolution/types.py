from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CommitChange:
    sha: str
    authored_at: datetime
    files: tuple[str, ...]
    churn: int


@dataclass(frozen=True)
class CoChange:
    left_path: str
    right_path: str
    commit_count: int
    confidence: float
    evidence_shas: tuple[str, ...]


@dataclass(frozen=True)
class Hotspot:
    path: str
    commit_count: int
    churn: int
    score: float
    evidence_shas: tuple[str, ...]


@dataclass(frozen=True)
class ModuleCandidate:
    key: str
    files: tuple[str, ...]
    confidence: float
    description: str
    evidence_shas: tuple[str, ...]
    inferred: bool = True


@dataclass(frozen=True)
class EvolutionResult:
    commits: tuple[CommitChange, ...]
    co_changes: tuple[CoChange, ...]
    hotspots: tuple[Hotspot, ...]
    modules: tuple[ModuleCandidate, ...]
    analysis_version: str = "evolution@0.1.0"
