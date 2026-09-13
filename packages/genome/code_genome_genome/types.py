from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from code_genome_analyzers import SourceSpan

NodeKind = Literal["FILE", "SYMBOL", "MODULE"]
EdgeKind = Literal["DECLARES", "EXPORTS", "IMPORTS"]


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    kind: Literal["source_file", "source_range"]
    repository_sha: str
    path: str
    span: SourceSpan | None
    extractor_version: str


@dataclass(frozen=True)
class GenomeNode:
    id: str
    kind: NodeKind
    natural_key: str
    properties: dict[str, Any]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class GenomeEdge:
    id: str
    kind: EdgeKind
    from_node: str
    to_node: str
    confidence: float
    evidence_id: str


@dataclass(frozen=True)
class GenomeDiagnostic:
    code: str
    message: str
    path: str
    span: SourceSpan | None
    evidence_id: str | None


@dataclass(frozen=True)
class GenomeGraph:
    repository_sha: str
    analysis_version: str
    nodes: tuple[GenomeNode, ...] = field(default_factory=tuple)
    edges: tuple[GenomeEdge, ...] = field(default_factory=tuple)
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    diagnostics: tuple[GenomeDiagnostic, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
