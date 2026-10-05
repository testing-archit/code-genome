from pathlib import PurePosixPath

from .javascript import ANALYZER_VERSION as JS_ANALYZER_VERSION
from .javascript import analyze_source as analyze_javascript
from .python import PYTHON_ANALYZER_VERSION, PYTHON_SUFFIXES, analyze_python
from .types import (
    CallFact,
    Diagnostic,
    ExportFact,
    FileAnalysis,
    FileMetrics,
    ImportFact,
    SourceSpan,
    SymbolFact,
)

# Version of the analyzer suite; part of every snapshot's analysis version, so changing any
# language analyzer rebuilds older snapshots on re-analysis. Files keep per-language versions.
ANALYZER_VERSION = "tree-sitter-js-ts-py@0.3.0"
SOURCE_SUFFIXES = frozenset(
    {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", *PYTHON_SUFFIXES}
)


def analyze_source(path: str, source: bytes | str) -> FileAnalysis:
    """Deterministic facts for a JS/TS or Python file, chosen by extension."""
    if PurePosixPath(path).suffix.lower() in PYTHON_SUFFIXES:
        return analyze_python(path, source)
    return analyze_javascript(path, source)


__all__ = [
    "ANALYZER_VERSION",
    "JS_ANALYZER_VERSION",
    "PYTHON_ANALYZER_VERSION",
    "SOURCE_SUFFIXES",
    "CallFact",
    "Diagnostic",
    "ExportFact",
    "FileAnalysis",
    "FileMetrics",
    "ImportFact",
    "SourceSpan",
    "SymbolFact",
    "analyze_source",
]
