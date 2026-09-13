from dataclasses import dataclass


@dataclass(frozen=True)
class RiskInput:
    path: str
    hotspot_score: float
    commit_count: int
    churn: int
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class RiskResult:
    path: str
    score: float
    features: dict[str, float]
    rationale: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ImpactRelation:
    source: str
    target: str
    kind: str
    weight: float
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ImpactResult:
    path: str
    score: float
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalDocument:
    id: str
    kind: str
    text: str


@dataclass(frozen=True)
class GroundedResult:
    answer: str
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class RankingEvaluation:
    model_brier: float
    constant_brier: float
    useful: bool
