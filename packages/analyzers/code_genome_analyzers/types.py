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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
