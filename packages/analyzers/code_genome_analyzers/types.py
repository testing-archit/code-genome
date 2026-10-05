from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, order=True)
class SourceSpan:
    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class ImportFact:
    module: str
    names: tuple[str, ...]
    kind: Literal["esm", "commonjs", "dynamic"]
    span: SourceSpan


@dataclass(frozen=True)
class ExportFact:
    name: str
    local_name: str | None
    is_default: bool
    span: SourceSpan


@dataclass(frozen=True)
class SymbolFact:
    name: str
    kind: Literal["function", "class", "method", "interface", "type", "enum"]
    exported: bool
    span: SourceSpan


@dataclass(frozen=True)
class CallFact:
    """A syntactic call site. ``callee`` is the called name; for ``a.b()`` the ``receiver`` is
    ``a`` and ``callee`` is ``b``. Resolution to a declaration happens in the genome builder."""

    callee: str
    receiver: str | None
    span: SourceSpan
    url_host: str | None = None


@dataclass(frozen=True)
class FileMetrics:
    """Deterministic size and complexity estimates for one file.

    ``loc`` counts non-blank lines holding at least one non-comment token. ``complexity`` is a
    cyclomatic estimate: 1 + decision points (if, loops, case, catch, ternary, &&, ||, ??).
    """

    loc: int = 0
    complexity: int = 1
    functions: int = 0


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    span: SourceSpan


@dataclass(frozen=True)
class FileAnalysis:
    path: str
    language: Literal["javascript", "typescript", "tsx"]
    content_sha256: str
    analyzer_version: str
    imports: tuple[ImportFact, ...] = field(default_factory=tuple)
    exports: tuple[ExportFact, ...] = field(default_factory=tuple)
    symbols: tuple[SymbolFact, ...] = field(default_factory=tuple)
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)
    calls: tuple[CallFact, ...] = field(default_factory=tuple)
    metrics: FileMetrics = field(default_factory=FileMetrics)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
