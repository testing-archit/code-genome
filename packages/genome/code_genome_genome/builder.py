import hashlib
import posixpath
from pathlib import PurePosixPath

from code_genome_analyzers import (
    ANALYZER_VERSION,
    CallFact,
    FileAnalysis,
    ImportFact,
    SourceSpan,
    SymbolFact,
)

from .types import EvidenceRef, GenomeDiagnostic, GenomeEdge, GenomeGraph, GenomeNode

GRAPH_BUILDER_VERSION = "structural-genome@0.2.0"
SOURCE_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".py", ".pyi"}
PythonIndex = dict[str, list[str]]
CALLABLE_KINDS = {"function", "class", "method"}
MAX_CALL_EDGES_PER_FILE = 300
# Call resolution confidence. No type checking happens, so every CALLS edge is a candidate:
# an import binding to an exported declaration is the strongest static signal; a bare name
# match within the same file is weaker (shadowing is not analysed).
CALL_CONFIDENCE_IMPORT_NAMED = 0.9
CALL_CONFIDENCE_IMPORT_INDIRECT = 0.85
CALL_CONFIDENCE_LOCAL_NAME = 0.7
CALL_CONFIDENCE_THIS_METHOD = 0.6
# Member-call names that usually indicate data-store reads or writes (ORMs, drivers, KV).
DATA_READ_VERBS = {
    "findMany", "findUnique", "findUniqueOrThrow", "findFirst", "findFirstOrThrow", "findOne",
    "findById", "findAll", "findAndCountAll", "aggregate", "countDocuments", "groupBy",
    "select", "selectFrom", "hget", "hgetall", "mget", "smembers", "lrange", "zrange",
    "getDoc", "getDocs", "onSnapshot", "scan", "getItem", "query",
}  # fmt: skip
DATA_WRITE_VERBS = {
    "create", "createMany", "insert", "insertOne", "insertMany", "insertInto", "update",
    "updateOne", "updateMany", "upsert", "delete", "deleteOne", "deleteMany", "deleteFrom",
    "destroy", "save", "bulkCreate", "hset", "hdel", "lpush", "rpush", "sadd", "zadd", "del",
    "setDoc", "addDoc", "updateDoc", "deleteDoc", "putItem", "updateItem", "deleteItem",
}  # fmt: skip


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def _file_evidence(repository_id: str, snapshot_sha: str, analysis: FileAnalysis) -> EvidenceRef:
    return EvidenceRef(
        id=_stable_id(
            "ev", repository_id, snapshot_sha, analysis.path, analysis.content_sha256, "file"
        ),
        kind="source_file",
        repository_sha=snapshot_sha,
        path=analysis.path,
        span=None,
        extractor_version=analysis.analyzer_version,
    )


def _range_evidence(
    repository_id: str,
    snapshot_sha: str,
    analysis: FileAnalysis,
    span: SourceSpan,
    fact_kind: str,
) -> EvidenceRef:
    return EvidenceRef(
        id=_stable_id("ev", repository_id, snapshot_sha, analysis.path, span, fact_kind),
        kind="source_range",
        repository_sha=snapshot_sha,
        path=analysis.path,
        span=span,
        extractor_version=analysis.analyzer_version,
    )


def _normalized_import_base(source_path: str, specifier: str) -> str | None:
    parent = PurePosixPath(source_path).parent
    normalized = posixpath.normpath(posixpath.join(str(parent), specifier))
    if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
        return None
    return normalized


def _import_candidates(source_path: str, specifier: str) -> tuple[str, ...]:
    normalized = _normalized_import_base(source_path, specifier)
    if normalized is None:
        return ()
    base = PurePosixPath(normalized)
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


def python_index(known_paths: set[str]) -> PythonIndex:
    """Every trailing path of each Python file ("a/b/c.py" -> "c.py", "b/c.py", "a/b/c.py"),
    so dotted imports resolve wherever the package root sits in the repository."""
    index: PythonIndex = {}
    for path in sorted(known_paths):
        if not path.endswith((".py", ".pyi")):
            continue
        parts = path.split("/")
        for start in range(len(parts)):
            index.setdefault("/".join(parts[start:]), []).append(path)
    return index


def _python_candidates(source_path: str, module: str) -> tuple[list[str], bool]:
    """(candidate repository paths or path suffixes, whether they are exact paths)."""
    dots = len(module) - len(module.lstrip("."))
    rest = [part for part in module[dots:].split(".") if part]
    if dots:
        base = PurePosixPath(source_path).parent
        for _ in range(dots - 1):
            if str(base) in {"", "."}:
                return [], True
            base = base.parent
        stem = "/".join([*([str(base)] if str(base) not in {"", "."} else []), *rest])
        if not rest:
            return [f"{stem}/__init__.py" if stem else "__init__.py"], True
        return [f"{stem}.py", f"{stem}.pyi", f"{stem}/__init__.py"], True
    stem = "/".join(rest)
    return [f"{stem}.py", f"{stem}.pyi", f"{stem}/__init__.py"], False


def _resolve_python(
    source_path: str, module: str, known_paths: set[str], index: PythonIndex
) -> str | None:
    candidates, exact = _python_candidates(source_path, module)
    for candidate in candidates:
        if exact:
            if candidate in known_paths:
                return candidate
            continue
        matches = index.get(candidate, [])
        if matches:
            # Prefer the match closest to the importing file, then the shortest path.
            source_parts = source_path.split("/")
            return min(
                matches,
                key=lambda path: (
                    -len(posixpath.commonprefix([path.split("/"), source_parts])),
                    path.count("/"),
                    path,
                ),
            )
    return None


def _resolve_import(
    source_path: str,
    item: ImportFact,
    known_paths: set[str],
    index: PythonIndex | None = None,
) -> str | None:
    if item.kind == "python":
        resolved = _resolve_python(source_path, item.module, known_paths, index or {})
        if resolved is None or resolved.endswith("__init__.py"):
            # `from pkg import module` names a submodule rather than a symbol.
            for name in item.names:
                if name != "*":
                    joined = (
                        f"{item.module}.{name}"
                        if not item.module.endswith(".")
                        else f"{item.module}{name}"
                    )
                    submodule = _resolve_python(source_path, joined, known_paths, index or {})
                    if submodule is not None:
                        return submodule
        return resolved
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


def _exported_symbol(
    target: FileAnalysis, name: str, symbol_facts: dict[str, list[tuple[SymbolFact, str]]]
) -> str | None:
    """Return the callable symbol a file exports under ``name`` ("default" for default)."""
    local_names = [item.local_name for item in target.exports if item.name == name]
    for local_name in local_names:
        for symbol, symbol_id in symbol_facts.get(target.path, []):
            if symbol.name == local_name and symbol.kind in CALLABLE_KINDS:
                return symbol_id
    return None


def _enclosing_symbol(
    span: SourceSpan, declared: list[tuple[SymbolFact, str]]
) -> tuple[SymbolFact, str] | None:
    """Innermost callable declaration whose source range contains ``span``."""
    best: tuple[SymbolFact, str] | None = None
    for symbol, symbol_id in declared:
        if symbol.kind not in CALLABLE_KINDS:
            continue
        inside = (symbol.span.start_line, symbol.span.start_column) <= (
            span.start_line,
            span.start_column,
        ) and (span.end_line, span.end_column) <= (symbol.span.end_line, symbol.span.end_column)
        if inside and (best is None or best[0].span <= symbol.span):
            best = (symbol, symbol_id)
    return best


def _import_bindings(
    analysis: FileAnalysis,
    by_path: dict[str, FileAnalysis],
    known_paths: set[str],
    symbol_facts: dict[str, list[tuple[SymbolFact, str]]],
    index: PythonIndex | None = None,
) -> tuple[dict[str, tuple[str, float]], dict[str, FileAnalysis]]:
    """Map local identifiers bound by relative ESM imports or resolved Python imports to
    exported callable symbols."""
    bindings: dict[str, tuple[str, float]] = {}
    namespaces: dict[str, FileAnalysis] = {}
    for imported in analysis.imports:
        if imported.kind not in {"esm", "python"}:
            continue
        resolved = _resolve_import(analysis.path, imported, known_paths, index)
        if resolved is None:
            continue
        target = by_path[resolved]
        for name in imported.names:
            if imported.kind == "python" and (
                PurePosixPath(resolved).stem == name or resolved.endswith(f"/{name}/__init__.py")
            ):
                # `from pkg import module`: calls look like `module.function()`.
                namespaces[name] = target
                continue
            if name.startswith("* as "):
                namespaces[name.removeprefix("* as ")] = target
                continue
            original, _, alias = name.partition(" as ")
            local = alias or original
            symbol_id = _exported_symbol(target, original, symbol_facts)
            if symbol_id is not None:
                bindings[local] = (symbol_id, CALL_CONFIDENCE_IMPORT_NAMED)
                continue
            if not alias:
                # ``import X from`` and ``import { X } from`` share a shape in the extractor,
                # so fall back to the default export when no named export matches.
                default_id = _exported_symbol(target, "default", symbol_facts)
                if default_id is not None:
                    bindings[local] = (default_id, CALL_CONFIDENCE_IMPORT_INDIRECT)
    return bindings, namespaces


def _resolve_call(
    call: CallFact,
    bindings: dict[str, tuple[str, float]],
    namespaces: dict[str, FileAnalysis],
    local_functions: dict[str, str],
    methods: dict[str, str],
    symbol_facts: dict[str, list[tuple[SymbolFact, str]]],
) -> tuple[str, float] | None:
    if call.receiver is None:
        if call.callee in bindings:
            return bindings[call.callee]
        if call.callee in local_functions:
            return local_functions[call.callee], CALL_CONFIDENCE_LOCAL_NAME
        return None
    if call.receiver == "this":
        if call.callee in methods:
            return methods[call.callee], CALL_CONFIDENCE_THIS_METHOD
        return None
    namespace = namespaces.get(call.receiver)
    if namespace is not None:
        symbol_id = _exported_symbol(namespace, call.callee, symbol_facts)
        if symbol_id is not None:
            return symbol_id, CALL_CONFIDENCE_IMPORT_INDIRECT
    return None


def _add_call_edges(
    repository_id: str,
    snapshot_sha: str,
    analysis: FileAnalysis,
    by_path: dict[str, FileAnalysis],
    known_paths: set[str],
    file_node_ids: dict[str, str],
    symbol_facts: dict[str, list[tuple[SymbolFact, str]]],
    edges: dict[str, GenomeEdge],
    evidence: dict[str, EvidenceRef],
    index: PythonIndex | None = None,
) -> None:
    """Emit candidate CALLS edges (caller symbol or file -> callee symbol) with provenance."""
    bindings, namespaces = _import_bindings(analysis, by_path, known_paths, symbol_facts, index)
    declared = symbol_facts.get(analysis.path, [])
    local_functions = {
        symbol.name: symbol_id
        for symbol, symbol_id in declared
        if symbol.kind in {"function", "class"}
    }
    methods = {symbol.name: symbol_id for symbol, symbol_id in declared if symbol.kind == "method"}
    added = 0
    for call in analysis.calls:
        if added >= MAX_CALL_EDGES_PER_FILE:
            break
        target = _resolve_call(call, bindings, namespaces, local_functions, methods, symbol_facts)
        if target is None:
            continue
        target_id, confidence = target
        enclosing = _enclosing_symbol(call.span, declared)
        source_id = enclosing[1] if enclosing else file_node_ids[analysis.path]
        if source_id == target_id:
            continue
        edge_id = _stable_id("edge", repository_id, snapshot_sha, "CALLS", source_id, target_id)
        if edge_id in edges:
            continue
        call_evidence = _range_evidence(repository_id, snapshot_sha, analysis, call.span, "call")
        evidence[call_evidence.id] = call_evidence
        edges[edge_id] = GenomeEdge(
            id=edge_id,
            kind="CALLS",
            from_node=source_id,
            to_node=target_id,
            confidence=confidence,
            evidence_id=call_evidence.id,
        )
        added += 1


def build_structural_graph(
    repository_id: str, snapshot_sha: str, files: list[FileAnalysis]
) -> GenomeGraph:
    """Build a deterministic, snapshot-scoped graph from extracted source facts."""
    if not snapshot_sha or any(
        character not in "0123456789abcdef" for character in snapshot_sha.lower()
    ):
        raise ValueError("snapshot_sha must be a hexadecimal Git object ID")
    ordered_files = sorted(files, key=lambda item: item.path)
    if len({item.path for item in ordered_files}) != len(ordered_files):
        raise ValueError("File analyses must have unique repository paths")

    known_paths = {item.path for item in ordered_files}
    py_index = python_index(known_paths)
    nodes: dict[str, GenomeNode] = {}
    edges: dict[str, GenomeEdge] = {}
    evidence: dict[str, EvidenceRef] = {}
    diagnostics: list[GenomeDiagnostic] = []
    file_node_ids: dict[str, str] = {}
    symbols_by_file: dict[str, dict[str, list[str]]] = {}
    symbol_facts: dict[str, list[tuple[SymbolFact, str]]] = {}

    for analysis in ordered_files:
        file_evidence = _file_evidence(repository_id, snapshot_sha, analysis)
        evidence[file_evidence.id] = file_evidence
        file_id = _stable_id("node", repository_id, snapshot_sha, "FILE", analysis.path)
        file_node_ids[analysis.path] = file_id
        nodes[file_id] = GenomeNode(
            id=file_id,
            kind="FILE",
            natural_key=analysis.path,
            properties={
                "language": analysis.language,
                "content_sha256": analysis.content_sha256,
                "parse_status": "PARTIAL" if analysis.diagnostics else "COMPLETE",
                "loc": analysis.metrics.loc,
                "complexity": analysis.metrics.complexity,
                "functions": analysis.metrics.functions,
                "data_reads": sum(
                    1 for call in analysis.calls if call.receiver and call.callee in DATA_READ_VERBS
                ),
                "data_writes": sum(
                    1
                    for call in analysis.calls
                    if call.receiver and call.callee in DATA_WRITE_VERBS
                ),
                "external_hosts": sorted(
                    {call.url_host for call in analysis.calls if call.url_host}
                )[:10],
            },
            evidence_ids=(file_evidence.id,),
        )

        symbols_by_name: dict[str, list[str]] = {}
        for symbol in analysis.symbols:
            symbol_evidence = _range_evidence(
                repository_id, snapshot_sha, analysis, symbol.span, "symbol"
            )
            evidence[symbol_evidence.id] = symbol_evidence
            natural_key = (
                f"{analysis.path}#{symbol.kind}:{symbol.name}:"
                f"{symbol.span.start_line}:{symbol.span.start_column}"
            )
            symbol_id = _stable_id("node", repository_id, snapshot_sha, "SYMBOL", natural_key)
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
            edge_id = _stable_id(
                "edge", repository_id, snapshot_sha, "DECLARES", file_id, symbol_id
            )
            edges[edge_id] = GenomeEdge(
                id=edge_id,
                kind="DECLARES",
                from_node=file_id,
                to_node=symbol_id,
                confidence=1.0,
                evidence_id=symbol_evidence.id,
            )
            symbols_by_name.setdefault(symbol.name, []).append(symbol_id)
            symbol_facts.setdefault(analysis.path, []).append((symbol, symbol_id))
        symbols_by_file[analysis.path] = symbols_by_name

        for diagnostic in analysis.diagnostics:
            diagnostic_evidence = _range_evidence(
                repository_id, snapshot_sha, analysis, diagnostic.span, diagnostic.code
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
                exported_evidence = _range_evidence(
                    repository_id, snapshot_sha, analysis, exported.span, "export"
                )
                evidence[exported_evidence.id] = exported_evidence
                edge_id = _stable_id(
                    "edge", repository_id, snapshot_sha, "EXPORTS", file_id, symbol_id
                )
                edges[edge_id] = GenomeEdge(
                    id=edge_id,
                    kind="EXPORTS",
                    from_node=file_id,
                    to_node=symbol_id,
                    confidence=1.0,
                    evidence_id=exported_evidence.id,
                )

        for imported in analysis.imports:
            imported_evidence = _range_evidence(
                repository_id, snapshot_sha, analysis, imported.span, "import"
            )
            evidence[imported_evidence.id] = imported_evidence
            resolved = _resolve_import(analysis.path, imported, known_paths, py_index)
            if resolved:
                target_id = file_node_ids[resolved]
            elif (
                imported.kind != "python"  # "..pkg.mod" is a module path, not a file extension
                and imported.module.startswith(".")
                and PurePosixPath(imported.module).suffix.lower()
                and PurePosixPath(imported.module).suffix.lower() not in SOURCE_SUFFIXES
            ):
                normalized_asset = _normalized_import_base(analysis.path, imported.module)
                module_key = f"asset:{normalized_asset or imported.module}"
                target_id = _stable_id("node", repository_id, snapshot_sha, "MODULE", module_key)
                existing = nodes.get(target_id)
                evidence_ids = tuple(
                    sorted({*(existing.evidence_ids if existing else ()), imported_evidence.id})
                )
                nodes[target_id] = GenomeNode(
                    id=target_id,
                    kind="MODULE",
                    natural_key=module_key,
                    properties={
                        "specifier": imported.module,
                        "external": False,
                        "asset": True,
                    },
                    evidence_ids=evidence_ids,
                )
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
                # Python packages group by their top-level name (fastapi.routing -> fastapi).
                package = (
                    imported.module.split(".")[0] if imported.kind == "python" else imported.module
                )
                module_key = f"external:{package}"
                target_id = _stable_id("node", repository_id, snapshot_sha, "MODULE", module_key)
                existing = nodes.get(target_id)
                evidence_ids = tuple(
                    sorted({*(existing.evidence_ids if existing else ()), imported_evidence.id})
                )
                nodes[target_id] = GenomeNode(
                    id=target_id,
                    kind="MODULE",
                    natural_key=module_key,
                    properties={"specifier": package, "external": True},
                    evidence_ids=evidence_ids,
                )
            edge_id = _stable_id("edge", repository_id, snapshot_sha, "IMPORTS", file_id, target_id)
            edges[edge_id] = GenomeEdge(
                id=edge_id,
                kind="IMPORTS",
                from_node=file_id,
                to_node=target_id,
                confidence=1.0,
                evidence_id=imported_evidence.id,
            )

    by_path = {item.path: item for item in ordered_files}
    for analysis in ordered_files:
        _add_call_edges(
            repository_id,
            snapshot_sha,
            analysis,
            by_path,
            known_paths,
            file_node_ids,
            symbol_facts,
            edges,
            evidence,
            py_index,
        )

    # The suite version covers every language analyzer, so a mixed JS/Python snapshot and a
    # JS-only one compare against the same current version.
    extractor_version = ANALYZER_VERSION if ordered_files else "none"
    return GenomeGraph(
        repository_id=repository_id,
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
