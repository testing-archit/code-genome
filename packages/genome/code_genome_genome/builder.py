import hashlib
from pathlib import PurePosixPath

from code_genome_analyzers import FileAnalysis, ImportFact, SourceSpan

from .types import EvidenceRef, GenomeDiagnostic, GenomeEdge, GenomeGraph, GenomeNode

GRAPH_BUILDER_VERSION = "structural-genome@0.1.0"


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def _file_evidence(snapshot_sha: str, analysis: FileAnalysis) -> EvidenceRef:
    return EvidenceRef(
        id=_stable_id("ev", snapshot_sha, analysis.path, analysis.content_sha256, "file"),
        kind="source_file",
        repository_sha=snapshot_sha,
        path=analysis.path,
        span=None,
        extractor_version=analysis.analyzer_version,
    )


def _range_evidence(
    snapshot_sha: str, analysis: FileAnalysis, span: SourceSpan, fact_kind: str
) -> EvidenceRef:
    return EvidenceRef(
        id=_stable_id("ev", snapshot_sha, analysis.path, span, fact_kind),
        kind="source_range",
        repository_sha=snapshot_sha,
        path=analysis.path,
        span=span,
        extractor_version=analysis.analyzer_version,
    )


def _import_candidates(source_path: str, specifier: str) -> tuple[str, ...]:
    parent = PurePosixPath(source_path).parent
    base = parent.joinpath(specifier)
    if any(part == ".." for part in base.parts) or str(base).startswith("/"):
        return ()
    suffix = base.suffix.lower()
    candidates: list[PurePosixPath] = [base]
    if suffix in {".js", ".jsx"}:
        candidates.extend([base.with_suffix(".ts"), base.with_suffix(".tsx")])
    elif not suffix:
        candidates.extend(
            base.with_suffix(extension)
            for extension in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
        )
        candidates.extend(
            base / f"index{extension}" for extension in (".ts", ".tsx", ".js", ".jsx")
        )
    return tuple(dict.fromkeys(str(candidate) for candidate in candidates))


def _resolve_import(source_path: str, item: ImportFact, known_paths: set[str]) -> str | None:
    if not item.module.startswith("."):
        return None
    return next(
        (
            candidate
            for candidate in _import_candidates(source_path, item.module)
            if candidate in known_paths
        ),
        None,
    )


def build_structural_graph(snapshot_sha: str, files: list[FileAnalysis]) -> GenomeGraph:
    """Build a deterministic, snapshot-scoped graph from extracted source facts."""
    if not snapshot_sha or any(
        character not in "0123456789abcdef" for character in snapshot_sha.lower()
    ):
        raise ValueError("snapshot_sha must be a hexadecimal Git object ID")
    ordered_files = sorted(files, key=lambda item: item.path)
    if len({item.path for item in ordered_files}) != len(ordered_files):
        raise ValueError("File analyses must have unique repository paths")

    known_paths = {item.path for item in ordered_files}
    nodes: dict[str, GenomeNode] = {}
    edges: dict[str, GenomeEdge] = {}
    evidence: dict[str, EvidenceRef] = {}
    diagnostics: list[GenomeDiagnostic] = []
    file_node_ids: dict[str, str] = {}
    symbols_by_file: dict[str, dict[str, list[str]]] = {}

    for analysis in ordered_files:
        file_evidence = _file_evidence(snapshot_sha, analysis)
        evidence[file_evidence.id] = file_evidence
        file_id = _stable_id("node", snapshot_sha, "FILE", analysis.path)
        file_node_ids[analysis.path] = file_id
        nodes[file_id] = GenomeNode(
            id=file_id,
            kind="FILE",
            natural_key=analysis.path,
            properties={
                "language": analysis.language,
                "content_sha256": analysis.content_sha256,
                "parse_status": "PARTIAL" if analysis.diagnostics else "COMPLETE",
            },
            evidence_ids=(file_evidence.id,),
        )

        symbols_by_name: dict[str, list[str]] = {}
        for symbol in analysis.symbols:
            symbol_evidence = _range_evidence(snapshot_sha, analysis, symbol.span, "symbol")
            evidence[symbol_evidence.id] = symbol_evidence
            natural_key = (
                f"{analysis.path}#{symbol.kind}:{symbol.name}:"
                f"{symbol.span.start_line}:{symbol.span.start_column}"
            )
            symbol_id = _stable_id("node", snapshot_sha, "SYMBOL", natural_key)
            nodes[symbol_id] = GenomeNode(
                id=symbol_id,
                kind="SYMBOL",
                natural_key=natural_key,
                properties={
                    "name": symbol.name,
                    "symbol_kind": symbol.kind,
                    "exported": symbol.exported,
                    "path": analysis.path,
                    "span": {
                        "start_line": symbol.span.start_line,
                        "start_column": symbol.span.start_column,
                        "end_line": symbol.span.end_line,
                        "end_column": symbol.span.end_column,
                    },
                },
                evidence_ids=(symbol_evidence.id,),
            )
            edge_id = _stable_id("edge", snapshot_sha, "DECLARES", file_id, symbol_id)
            edges[edge_id] = GenomeEdge(
                id=edge_id,
                kind="DECLARES",
                from_node=file_id,
                to_node=symbol_id,
                confidence=1.0,
                evidence_id=symbol_evidence.id,
            )
            symbols_by_name.setdefault(symbol.name, []).append(symbol_id)
        symbols_by_file[analysis.path] = symbols_by_name

        for diagnostic in analysis.diagnostics:
            diagnostic_evidence = _range_evidence(
                snapshot_sha, analysis, diagnostic.span, diagnostic.code
            )
            evidence[diagnostic_evidence.id] = diagnostic_evidence
            diagnostics.append(
                GenomeDiagnostic(
                    code=diagnostic.code,
                    message=diagnostic.message,
                    path=analysis.path,
                    span=diagnostic.span,
                    evidence_id=diagnostic_evidence.id,
                )
            )

    for analysis in ordered_files:
        file_id = file_node_ids[analysis.path]
        for exported in analysis.exports:
            if exported.local_name is None:
                continue
            for symbol_id in symbols_by_file[analysis.path].get(exported.local_name, []):
                exported_evidence = _range_evidence(snapshot_sha, analysis, exported.span, "export")
                evidence[exported_evidence.id] = exported_evidence
                edge_id = _stable_id("edge", snapshot_sha, "EXPORTS", file_id, symbol_id)
                edges[edge_id] = GenomeEdge(
                    id=edge_id,
                    kind="EXPORTS",
                    from_node=file_id,
                    to_node=symbol_id,
                    confidence=1.0,
                    evidence_id=exported_evidence.id,
                )

        for imported in analysis.imports:
            imported_evidence = _range_evidence(snapshot_sha, analysis, imported.span, "import")
            evidence[imported_evidence.id] = imported_evidence
            resolved = _resolve_import(analysis.path, imported, known_paths)
            if resolved:
                target_id = file_node_ids[resolved]
            elif imported.module.startswith("."):
                diagnostics.append(
                    GenomeDiagnostic(
                        code="UNRESOLVED_IMPORT",
                        message=f"Relative import could not be resolved: {imported.module}",
                        path=analysis.path,
                        span=imported.span,
                        evidence_id=imported_evidence.id,
                    )
                )
                continue
            else:
                module_key = f"external:{imported.module}"
                target_id = _stable_id("node", snapshot_sha, "MODULE", module_key)
                existing = nodes.get(target_id)
                evidence_ids = tuple(
                    sorted({*(existing.evidence_ids if existing else ()), imported_evidence.id})
                )
                nodes[target_id] = GenomeNode(
                    id=target_id,
                    kind="MODULE",
                    natural_key=module_key,
                    properties={"specifier": imported.module, "external": True},
                    evidence_ids=evidence_ids,
                )
            edge_id = _stable_id("edge", snapshot_sha, "IMPORTS", file_id, target_id)
            edges[edge_id] = GenomeEdge(
                id=edge_id,
                kind="IMPORTS",
                from_node=file_id,
                to_node=target_id,
                confidence=1.0,
                evidence_id=imported_evidence.id,
            )

    extractor_version = ordered_files[0].analyzer_version if ordered_files else "none"
    return GenomeGraph(
        repository_sha=snapshot_sha.lower(),
        analysis_version=f"{GRAPH_BUILDER_VERSION}+{extractor_version}",
        nodes=tuple(sorted(nodes.values(), key=lambda item: (item.kind, item.natural_key))),
        edges=tuple(
            sorted(edges.values(), key=lambda item: (item.kind, item.from_node, item.to_node))
        ),
        evidence=tuple(sorted(evidence.values(), key=lambda item: item.id)),
        diagnostics=tuple(
            sorted(
                diagnostics,
                key=lambda item: (item.path, item.span or SourceSpan(0, 0, 0, 0), item.code),
            )
        ),
    )
