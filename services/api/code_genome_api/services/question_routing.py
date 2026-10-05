"""Route questions that general text search answers badly to deterministic evidence.

* Overview questions ("what does this project do?", "ye project kya karta hai?") are answered
  from the repository's own README, docs, and package manifest, plus inferred modules.
* Change-impact questions ("if I change app.tsx what can break?") resolve the named file
  against the snapshot manifest and use the impact engine. A file that does not exist is
  reported as such, with the closest real paths, instead of guessing.
* Flow questions ("how does retry work?", "fetch kaise kaam karta hai?") resolve the named
  file, symbol, or component and walk the genome graph's calls, imports, and data-store edges.

Every routed result cites stored evidence; a model may only rephrase it.
"""

import difflib
import re
from collections import Counter
from pathlib import PurePosixPath

from code_genome_intelligence import GroundedResult, RetrievalDocument
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CoChangeEdge,
    FileHotspot,
    FileManifestEntry,
    GraphNode,
    KnowledgeChunkRecord,
    ModuleCandidate,
    RepositoryCommit,
    RepositorySnapshot,
)
from . import genome, insights
from .flow import FLOW_VERSION, CodeFlow, FlowStep, label, trace_flow, verb
from .impact import impact_for_paths
from .knowledge import document_text
from .ml import trained_result

REFUSAL = "No supporting evidence was identified in the selected scope."
PROJECT_MANIFESTS = {
    "package.json", "pyproject.toml", "cargo.toml", "go.mod", "composer.json", "setup.cfg",
}  # fmt: skip
_FILE_TOKEN = re.compile(
    r"(?<![\w@/.-])((?:[\w@.-]+/)*[\w@-][\w@.-]*\.(?:tsx|ts|jsx|js|mjs|cjs|mts|cts|py|sol|json|"
    r"css|scss|md|go|rs|java|kt|rb|php|vue|svelte|html|yml|yaml|toml|sh|sql))(?![\w])",
    re.IGNORECASE,
)
_IMPACT_WORDS = re.compile(
    r"\b(change|changes|changed|changing|modify|modifying|edit|editing|refactor|delete|remove|"
    r"rename|break|breaks|broken|affect|affects|affected|impact|impacts|depend|depends|"
    r"dependents|uses|imports|blast|badal|badla|badlu|badlun|badalne|badalta|toot|tootega|"
    r"tutega|asar|effect)\b|बदल|टूट|असर",
    re.IGNORECASE,
)
_OVERVIEW_PHRASES = re.compile(
    r"what (does|do|is|are) (this|the|it|that)\b|what'?s (this|the)\b|purpose|overview|"
    r"summar|introduc|tell me about|explain (this|the)|describe (this|the)|about (this|the) "
    r"(project|repo|repository|app|codebase)|kya (karta|karti|karte|hai|kaam)|kis (liye|kaam)|"
    r"kya hai|क्या (करता|करती|है)|किस (लिए|काम)|बारे में",
    re.IGNORECASE,
)
_PROJECT_WORDS = re.compile(
    r"\b(project|repo|repository|app|application|codebase|library|package|system|tool|it)\b|"
    r"प्रोजेक्ट|प्रोजेक्ट|रिपॉजिटरी|ऐप",
    re.IGNORECASE,
)
_WHAT = re.compile(r"\bwhat(?:'s|\s+is|\s+are|\s+does|\s+do)\b", re.IGNORECASE)
_RECENCY = re.compile(r"\b(latest|newest|recent|recently|haal|abhi)\b|हाल", re.IGNORECASE)


def is_overview_question(question: str, repository_name: str = "") -> bool:
    if _RECENCY.search(question) or _FILE_TOKEN.search(question):
        return False
    names = {part.lower() for part in re.split(r"[/_.-]", repository_name) if len(part) > 2}
    words = set(re.findall(r"[\w]+", question.lower()))
    names_repository = bool(names & words)
    if names_repository and _WHAT.search(question):
        return True
    mentions_project = bool(_PROJECT_WORDS.search(question)) or names_repository
    return bool(_OVERVIEW_PHRASES.search(question)) and mentions_project


def mentioned_files(question: str) -> list[str]:
    if not _IMPACT_WORDS.search(question):
        return []
    return list(
        dict.fromkeys(match.group(1).strip("./") for match in _FILE_TOKEN.finditer(question))
    )


def _chunk_document(chunk: KnowledgeChunkRecord) -> RetrievalDocument:
    return RetrievalDocument(
        f"evidence:{chunk.provenance_id}",
        "doc",
        document_text(
            chunk.path, chunk.kind, chunk.heading, chunk.start_line, chunk.end_line, chunk.text
        ),
    )


def overview_documents(db: Session, snapshot: RepositorySnapshot) -> list[RetrievalDocument]:
    """The repository describing itself: README first, then manifest, docs, and modules."""
    chunks = list(
        db.scalars(
            select(KnowledgeChunkRecord).where(
                KnowledgeChunkRecord.snapshot_id == snapshot.id,
                KnowledgeChunkRecord.workspace_id == snapshot.workspace_id,
                KnowledgeChunkRecord.kind.in_(("readme", "manifest", "doc")),
            )
        )
    )

    def rank(chunk: KnowledgeChunkRecord) -> tuple[int, int, int, str]:
        kind_order = {"readme": 0, "manifest": 1, "doc": 2}[chunk.kind]
        return (chunk.path.count("/"), kind_order, chunk.ordinal, chunk.path)

    readmes = [item for item in sorted(chunks, key=rank) if item.kind == "readme"][:4]
    manifests = [
        item
        for item in sorted(chunks, key=rank)
        if item.kind == "manifest" and PurePosixPath(item.path).name.lower() in PROJECT_MANIFESTS
    ][:1]
    docs = [
        item
        for item in sorted(chunks, key=rank)
        if item.kind == "doc" and item.ordinal == 0 and item.path.count("/") <= 1
    ][:2]
    documents = [_chunk_document(item) for item in [*readmes, *manifests, *docs]]
    modules = sorted(
        db.scalars(
            select(ModuleCandidate).where(
                ModuleCandidate.snapshot_id == snapshot.id,
                ModuleCandidate.workspace_id == snapshot.workspace_id,
            )
        ),
        key=lambda module: (-len(module.file_paths), module.natural_key),
    )[:3]
    documents.extend(
        RetrievalDocument(
            f"module:{module.id}",
            "module",
            f"Inferred module {module.natural_key} ({len(module.file_paths)} files): "
            f"{module.description}",
        )
        for module in modules
    )
    return documents


def _overview(
    db: Session, snapshot: RepositorySnapshot, pool: dict[str, RetrievalDocument]
) -> GroundedResult | None:
    documents = overview_documents(db, snapshot)
    if not any(document.kind == "doc" for document in documents):
        return None
    for document in documents:
        pool[document.id] = document
    excerpt = next(document.text for document in documents if document.kind == "doc")
    return GroundedResult(
        "Here is how the repository describes itself:\n"
        + "\n".join(f"- {document.text[:300].strip()}" for document in documents[:5]),
        tuple(document.id for document in documents),
        (
            "Overview drawn from the repository's own README, docs, and package manifest; "
            "these describe intent and may be out of date with the code.",
            f"Primary source: {excerpt[:80]}…",
        ),
    )


def resolve_path(paths: list[str], token: str) -> tuple[list[str], list[str]]:
    """Return (matches, suggestions) for a file name a person typed."""
    lowered = token.lower()
    exact = [path for path in paths if path == token]
    if exact:
        return exact, []
    by_suffix = [
        path for path in paths if path.lower() == lowered or path.lower().endswith("/" + lowered)
    ]
    if by_suffix:
        return sorted(by_suffix, key=lambda path: (path.count("/"), path))[:3], []
    names = {path: PurePosixPath(path).name.lower() for path in paths}
    stem = PurePosixPath(lowered).stem
    same_stem = [path for path, name in names.items() if PurePosixPath(name).stem == stem]
    close_names = set(
        difflib.get_close_matches(PurePosixPath(lowered).name, set(names.values()), n=5)
    )
    close = [path for path, name in names.items() if name in close_names]
    suggestions = list(dict.fromkeys(sorted(same_stem) + sorted(close, key=len)))[:5]
    return [], suggestions


def _impact(
    db: Session,
    snapshot: RepositorySnapshot,
    tokens: list[str],
    pool: dict[str, RetrievalDocument],
) -> GroundedResult | None:
    paths = list(
        db.scalars(
            select(FileManifestEntry.path).where(
                FileManifestEntry.snapshot_id == snapshot.id,
                FileManifestEntry.workspace_id == snapshot.workspace_id,
            )
        )
    )
    if not paths:
        return None
    resolved: list[str] = []
    missing: list[tuple[str, list[str]]] = []
    for token in tokens[:3]:
        matches, suggestions = resolve_path(paths, token)
        if matches:
            resolved.extend(matches)
        else:
            missing.append((token, suggestions))
    sha = snapshot.commit_sha[:12]
    if not resolved:
        token, suggestions = missing[0]
        if suggestions:
            hint = " Closest paths in this repository: " + ", ".join(suggestions) + "."
        else:
            suffix = PurePosixPath(token).suffix.lower()
            same_type = [path for path in paths if path.lower().endswith(suffix)]
            folders = Counter(
                path.split("/", 1)[0] + "/" for path in (same_type or paths) if "/" in path
            )
            hint = (
                (f" There are no {suffix} files. " if not same_type else " ")
                + "Code in this repository lives mostly in: "
                + ", ".join(f"{folder} ({count} files)" for folder, count in folders.most_common(3))
                + "."
            )
        return GroundedResult(
            f"{REFUSAL} No file named {token} exists in snapshot {sha}.{hint}",
            (),
            ("File names are matched against the snapshot's file manifest.",),
        )

    resolved = list(dict.fromkeys(resolved))[:3]
    impact, impact_limits = impact_for_paths(db, snapshot, resolved)
    file_nodes = {
        node.natural_key: node
        for node in db.scalars(
            select(GraphNode).where(
                GraphNode.snapshot_id == snapshot.id,
                GraphNode.workspace_id == snapshot.workspace_id,
                GraphNode.kind == "FILE",
                GraphNode.natural_key.in_(resolved),
            )
        )
    }
    hotspots = {
        row.path: row
        for row in db.scalars(
            select(FileHotspot).where(
                FileHotspot.snapshot_id == snapshot.id,
                FileHotspot.workspace_id == snapshot.workspace_id,
                FileHotspot.path.in_(resolved),
            )
        )
    }
    texts: dict[str, list[str]] = {}
    lines: list[str] = []
    for path in resolved:
        node = file_nodes.get(path)
        if node is not None and node.evidence_ids:
            texts.setdefault(f"evidence:{node.evidence_ids[0]}", []).append(
                f"File {path} exists in snapshot {sha}."
            )
        hotspot = hotspots.get(path)
        if hotspot is not None:
            texts.setdefault(f"hotspot:{path}", []).append(
                f"File {path} changed in {hotspot.commit_count} commits (relative hotspot score "
                f"{hotspot.score:.2f})."
            )
        neighbours = [
            item
            for item in impact.get(path, [])
            if not item.path.startswith("external:") and item.path not in resolved
        ][:10]
        inferred = [item for item in neighbours if not item.evidence_ids]
        observed = [item for item in neighbours if item.evidence_ids]
        if not observed and not inferred:
            lines.append(
                f"No observed imports or repeated co-change link other files to {path} in "
                f"snapshot {sha}."
            )
            continue
        lines.append(f"Changing {path} may affect:")
        for item in observed:
            reasons = "; ".join(item.reasons)
            texts.setdefault(item.evidence_ids[0], []).append(
                f"If {path} changes, {item.path} may be affected ({reasons})."
            )
            lines.append(f"- {item.path}: {reasons}")
        if inferred:
            note = ", ".join(item.path for item in inferred)
            lines.append(
                f"- Also predicted by the co-change model (inferred, no direct evidence): {note}"
            )
            if texts:
                first = next(iter(texts))
                texts[first].append(
                    f"A trained co-change model also predicts these may change with {path} "
                    f"(inferred, no direct evidence): {note}."
                )
    if not texts:
        return None
    for evidence_id, parts in texts.items():
        pool[evidence_id] = RetrievalDocument(evidence_id, "impact", " ".join(parts))
    limitations = [
        *impact_limits,
        *(f"No file named {token} exists in snapshot {sha}." for token, _ in missing),
    ]
    lines.append("Absence from this list does not rule out runtime impact.")
    return GroundedResult("\n".join(lines), tuple(texts), tuple(limitations))


_WHY = re.compile(r"\b(why|kyun|kyon|kyu|reason|explain)\b|क्यों", re.IGNORECASE)
_RISKY = re.compile(
    r"\b(risk|risky|riskiest|dangerous|fragile|bug[- ]?prone|unstable|hotspot)\b|जोखिम|रिस्क",
    re.IGNORECASE,
)


def _asks_why_risky(question: str) -> bool:
    # Word order differs between English ("why is X risky") and Hinglish ("X risky kyun").
    return bool(_WHY.search(question) and _RISKY.search(question))


_NAME_TOKEN = re.compile(r"[A-Za-z_][\w.-]{3,}")


def risk_question_targets(question: str, paths: list[str]) -> list[str]:
    """Files named in a "why is X risky?" question, by path, file name, or bare stem."""
    if not _asks_why_risky(question):
        return []
    named = [match.group(1).strip("./") for match in _FILE_TOKEN.finditer(question)]
    stems: dict[str, list[str]] = {}
    for path in paths:
        stems.setdefault(PurePosixPath(path).stem.lower(), []).append(path)
    for token in _NAME_TOKEN.findall(question):
        lowered = token.lower().rstrip(".?")
        if lowered in stems and lowered not in {"index", "main", "utils", "types", "test"}:
            named.extend(stems[lowered][:2])
    resolved: list[str] = []
    for token in named:
        matches, _ = resolve_path(paths, token)
        resolved.extend(matches)
    return list(dict.fromkeys(resolved))[:3]


_FIX_WORDS = re.compile(r"\b(fix|fixes|fixed|bug|regression|hotfix|crash|error)\b", re.IGNORECASE)


def _why_risky(
    db: Session,
    snapshot: RepositorySnapshot,
    targets: list[str],
    pool: dict[str, RetrievalDocument],
) -> GroundedResult | None:
    """Explain a file's risk from the model's factors, its fix history, and co-change."""
    learned = trained_result(db, snapshot, "defect_risk")
    predictions = {item["path"]: item for item in (learned or {}).get("predictions", [])}
    labels: dict[str, str] = (learned or {}).get("feature_labels", {})
    hotspots = {
        row.path: row
        for row in db.scalars(
            select(FileHotspot).where(
                FileHotspot.snapshot_id == snapshot.id,
                FileHotspot.workspace_id == snapshot.workspace_id,
                FileHotspot.path.in_(targets),
            )
        )
    }
    texts: dict[str, list[str]] = {}
    lines: list[str] = []
    for path in targets:
        prediction = predictions.get(path)
        hotspot = hotspots.get(path)
        shas = list((prediction or {}).get("evidence_shas", []))
        if hotspot is not None:
            shas.extend(hotspot.evidence_shas[:5])
        commits = {
            row.sha: row
            for row in db.scalars(
                select(RepositoryCommit).where(
                    RepositoryCommit.repository_id == snapshot.repository_id,
                    RepositoryCommit.workspace_id == snapshot.workspace_id,
                    RepositoryCommit.sha.in_(list(dict.fromkeys(shas))),
                )
            )
        }
        if prediction is not None:
            factors = ", ".join(
                f"{labels.get(name, name)} ({'+' if value >= 0 else ''}{value:.2f})"
                for name, value in prediction.get("contributions", [])[:4]
            )
            version = learned["model_version"] if learned else "risk model"
            line = (
                f"{path} has {prediction['band']} predicted defect-proneness "
                f"(p={prediction['probability']:.2f}, {version}). "
                f"Strongest factors: {factors}."
            )
            lines.append(line)
            anchor = f"commit:{shas[0]}" if shas else None
            if anchor:
                texts.setdefault(anchor, []).append(line)
        if hotspot is not None:
            line = (
                f"{path} changed in {hotspot.commit_count} commits with {hotspot.churn} lines of "
                f"churn (relative hotspot score {hotspot.score:.2f})."
            )
            lines.append(line)
            texts.setdefault(f"hotspot:{path}", []).append(line)
        fixes = [commit for commit in commits.values() if _FIX_WORDS.search(commit.message)]
        for commit in fixes[:4]:
            subject = commit.message.splitlines()[0][:160]
            texts.setdefault(f"commit:{commit.sha}", []).append(
                f"Bug-fix commit touching {path}: {subject}"
            )
        if fixes:
            lines.append(f"- {len(fixes)} of its recent commits are bug fixes.")
        partners = db.scalars(
            select(CoChangeEdge)
            .where(
                CoChangeEdge.snapshot_id == snapshot.id,
                CoChangeEdge.workspace_id == snapshot.workspace_id,
                (CoChangeEdge.left_path == path) | (CoChangeEdge.right_path == path),
            )
            .order_by(CoChangeEdge.commit_count.desc())
            .limit(3)
        )
        for edge in partners:
            other = edge.right_path if edge.left_path == path else edge.left_path
            if edge.evidence_shas:
                texts.setdefault(f"commit:{edge.evidence_shas[0]}", []).append(
                    f"{path} changed together with {other} in {edge.commit_count} commits."
                )
    if not texts:
        return None
    for evidence_id, parts in texts.items():
        pool[evidence_id] = RetrievalDocument(evidence_id, "risk", " ".join(parts))
    return GroundedResult(
        "\n".join(lines) or "Risk evidence for the named file is listed below.",
        tuple(texts),
        (
            "Risk is a learned or relative estimate of defect-proneness, not proof of a defect.",
            "Bug-fix commits are identified from commit messages.",
        ),
    )


_FLOW = re.compile(
    r"\bhow (?:does|do|is|are|did)\b.{1,80}?\b(?:work|works|working|handled|implemented|"
    r"done|flow|flows|run|runs|happen|happens)\b|\bwhat happens (?:when|if|in)\b|"
    r"\b(?:flow|control flow|call flow|call chain) (?:of|for|in|through)\b|\bwalk me through\b|"
    r"\btrace (?:the )?(?:flow|calls?)\b|\bkaise (?:kaam|kam|chalta|chalti|work|hota|hoti)\b|"
    r"कैसे (?:काम|चलता|होता)",
    re.IGNORECASE,
)
_GENERIC = {
    "index", "main", "utils", "types", "test", "this", "that", "does", "work", "works", "what",
    "when", "happens", "project", "repo", "repository", "code", "flow", "kaise", "kaam", "karta",
    "karti", "hota", "through", "with", "from", "handled", "implemented", "function", "class",
}  # fmt: skip


_TEST_PARTS = {"test", "tests", "__tests__", "test-d", "spec", "specs", "e2e", "fixtures"}


def _is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(_TEST_PARTS & set(pure.parts[:-1])) or any(
        marker in pure.name for marker in (".test.", ".spec.", "_test.")
    )


def _source_first(path: str) -> tuple[bool, int, int, str]:
    return (_is_test_path(path), path.count("/"), len(path), path)


def is_flow_question(question: str) -> bool:
    return bool(_FLOW.search(question))


def flow_target(
    question: str, paths: list[str], symbols: dict[str, str], components: dict[str, list[str]]
) -> str | None:
    """The file a flow question is about: a named path, a symbol's file, a file stem, or the
    most connected-looking file of a named component (shortest path first)."""
    for match in _FILE_TOKEN.finditer(question):
        matches, _ = resolve_path(paths, match.group(1).strip("./"))
        if matches:
            return matches[0]
    about_tests = bool(re.search(r"\btests?\b|\bspec\b", question, re.IGNORECASE))
    lowered_components = {name.lower(): name for name in components}
    stems: dict[str, list[str]] = {}
    for path in paths:
        stems.setdefault(PurePosixPath(path).stem.lower(), []).append(path)
    for token in _NAME_TOKEN.findall(question):
        word = token.lower().rstrip(".?()")
        if word in _GENERIC:
            continue
        # A file named after the word beats a symbol of that name. Test files only count when
        # the question is about tests; otherwise ordinary retrieval answers instead.
        candidates = [
            path for path in stems.get(word, []) if about_tests or not _is_test_path(path)
        ]
        if candidates:
            return min(candidates, key=_source_first)
        if word in symbols and (about_tests or not _is_test_path(symbols[word])):
            return symbols[word]
        if word in lowered_components:
            return min(components[lowered_components[word]], key=_source_first)
    for name, members in components.items():
        if len(name) > 3 and name.lower() in question.lower() and members:
            return min(members, key=_source_first)
    return None


def _flow_sentence(graph: genome.GenomeGraphBuilder, step: FlowStep) -> str:
    note = " (inferred)" if step.inferred else ""
    return f"{label(graph, step.source)} {verb(step)} {label(graph, step.target)}{note}."


def _anchor(step: FlowStep, fallback: str | None) -> str | None:
    return step.evidence_ids[0] if step.evidence_ids else fallback


def _flow(
    db: Session,
    snapshot: RepositorySnapshot,
    question: str,
    pool: dict[str, RetrievalDocument],
    repository_name: str,
) -> GroundedResult | None:
    facts = insights.load_facts(db, snapshot, repository_name, {}, "none")
    if not facts.component_of:
        return None
    symbols: dict[str, str] = {}
    for path in sorted(facts.symbols, key=_source_first):
        for name in facts.symbols[path]:
            if len(name) >= 4:
                symbols.setdefault(name.lower(), path)
    components: dict[str, list[str]] = {}
    for path, name in facts.component_of.items():
        components.setdefault(name, []).append(path)
    target = flow_target(question, sorted(facts.component_of), symbols, components)
    if target is None:
        return None
    graph = genome.build_full_genome(db, facts, include_symbols_for={target})
    flow = trace_flow(graph, target)
    if flow.empty:
        return None
    return _flow_result(graph, flow, target, snapshot, pool)


def _flow_result(
    graph: genome.GenomeGraphBuilder,
    flow: CodeFlow,
    target: str,
    snapshot: RepositorySnapshot,
    pool: dict[str, RetrievalDocument],
) -> GroundedResult | None:
    sha = snapshot.commit_sha[:12]
    file_evidence = next(iter(graph.nodes[flow.start].evidence_ids), None)
    texts: dict[str, list[str]] = {}

    def cite(step: FlowStep) -> None:
        anchor = _anchor(step, file_evidence)
        if anchor is not None:
            texts.setdefault(anchor, []).append(_flow_sentence(graph, step))

    lines = [f"How {target} works, following the code graph of snapshot {sha}:"]
    if flow.declared:
        lines.append(f"It declares {', '.join(flow.declared)}.")
        if file_evidence:
            texts.setdefault(file_evidence, []).append(
                f"{target} declares {', '.join(flow.declared)}."
            )
    if flow.callers:
        lines.append(
            "Called or imported by: "
            + ", ".join(f"{label(graph, step.source)} ({verb(step)})" for step in flow.callers)
            + "."
        )
        for step in flow.callers:
            cite(step)
    for step in flow.symbol_calls:
        cite(step)
    if flow.symbol_calls:
        lines.append("Inside the file:")
        lines.extend(f"- {_flow_sentence(graph, step)}" for step in flow.symbol_calls)
    for number, chain in enumerate(flow.chains, start=1):
        parts = [label(graph, chain[0].source)]
        for step in chain:
            marker = " (inferred)" if step.inferred else ""
            parts.append(f"{verb(step)}{marker} → {label(graph, step.target)}")
            cite(step)
        lines.append(f"Flow {number}: " + " ".join(parts))
    if not texts:
        return None
    for evidence_id, parts in texts.items():
        pool[evidence_id] = RetrievalDocument(evidence_id, "flow", " ".join(dict.fromkeys(parts)))
    return GroundedResult(
        "\n".join(lines),
        tuple(texts),
        (
            f"Flow traced by {FLOW_VERSION} over static calls and imports, up to three hops; "
            "runtime dispatch, dependency injection, and dynamic imports are not followed.",
            "CALLS edges are resolved statically without type checking, and data-store and API "
            "use is detected from client libraries and literal URLs; those steps are inferred.",
        ),
    )


def route_question(
    db: Session,
    snapshot: RepositorySnapshot,
    question: str,
    pool: dict[str, RetrievalDocument],
    repository_name: str = "",
) -> GroundedResult | None:
    """A deterministic answer for overview, risk, flow, and change-impact questions, or None."""
    if _asks_why_risky(question):
        paths = list(
            db.scalars(
                select(FileManifestEntry.path).where(
                    FileManifestEntry.snapshot_id == snapshot.id,
                    FileManifestEntry.workspace_id == snapshot.workspace_id,
                )
            )
        )
        targets = risk_question_targets(question, paths)
        if targets:
            explained = _why_risky(db, snapshot, targets, pool)
            if explained is not None:
                return explained
    tokens = mentioned_files(question)
    if is_flow_question(question):
        traced = _flow(db, snapshot, question, pool, repository_name)
        if traced is not None:
            return traced
    if tokens:
        return _impact(db, snapshot, tokens, pool)
    if is_overview_question(question, repository_name):
        return _overview(db, snapshot, pool)
    return None
