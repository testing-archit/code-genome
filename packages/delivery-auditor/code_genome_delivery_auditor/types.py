from dataclasses import dataclass


@dataclass(frozen=True)
class ClaimDraft:
    ordinal: int
    text: str
    start_offset: int
    end_offset: int
    claim_type: str


@dataclass(frozen=True)
class EvidenceCandidate:
    id: str
    kind: str
    text: str
    # Provider evidence only: CI conclusion or deployment state, and deployment environment.
    outcome: str | None = None
    environment: str | None = None


@dataclass(frozen=True)
class ClaimAssessment:
    status: str
    confidence: float
    evidence_ids: tuple[str, ...]
    rationale: str
    limitations: tuple[str, ...]
