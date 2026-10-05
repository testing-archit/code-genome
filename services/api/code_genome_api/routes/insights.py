import hashlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import PurePosixPath

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..config import get_settings
from ..errors import AppError
from ..ids import new_id
from ..models import GeneratedDocRewrite, RepositorySnapshot, utc_now
from ..schemas import (
    ContributorResponse,
    DocRewriteResponse,
    GeneratedDocumentResponse,
    GeneratedDocumentsResponse,
    HealthComponentResponse,
    ModuleGraphResponse,
    ModuleLinkResponse,
    ModuleNodeResponse,
    ModuleRiskResponse,
    OverviewCounts,
    OverviewFileRisk,
    RepositoryHealthResponse,
    RepositoryOverviewResponse,
    UnstableComponentResponse,
)
from ..services import doc_rewrite, insights, ml
from ..services.gemini import GeminiProviderError, rewrite_document_markdown
from ..services.knowledge import classify
from .intelligence import _repository, _snapshot, get_risk

router = APIRouter(tags=["insights"])


def _facts(db: Database, repository_id: str, actor: Actor) -> insights.SnapshotFacts:
    repository = _repository(db, repository_id, actor)
    snapshot = _snapshot(db, repository_id, actor)
    risk = get_risk(repository_id, db, actor)
    facts = insights.load_facts(
        db,
        snapshot,
        repository.external_id,
        {
            item.path: insights.RiskFact(item.score, item.rationale, item.evidence_ids)
            for item in risk.scores
        },
        risk.scores[0].model_version if risk.scores else "none",
    )
    facts.instability, facts.instability_model = insights.instability_facts(
        ml.trained_result(db, snapshot, "instability")
    )
    return facts


@router.get("/repositories/{repository_id}/overview", response_model=RepositoryOverviewResponse)
def get_overview(repository_id: str, db: Database, actor: Actor) -> RepositoryOverviewResponse:
    facts = _facts(db, repository_id, actor)
    score, band, components = insights.health(facts)
    nodes, _ = insights.module_graph(facts)
    intro = insights._readme_intro(facts)
    authors: Counter[str] = Counter()
    names: dict[str, str] = {}
    for commit in facts.commits:
        key = commit.author_email.lower()
        authors[key] += 1
        names.setdefault(key, commit.author_name)
    packages = {
        target.removeprefix("external:").split("/")[0] for _, target, _ in facts.external_imports
    }
    documents = {
        item.path for item in facts.chunks if PurePosixPath(item.path).suffix.lower() == ".md"
    }
    # The overview links each file to "what will break"; that question is about code, so
    # README, lock, and config files that merely change often are left to the Risk view.
    ranked = sorted(
        (item for item in facts.risk.items() if classify(item[0]) == "source"),
        key=lambda item: -item[1].score,
    )[:6]
    return RepositoryOverviewResponse(
        repository_id=repository_id,
        snapshot_sha=facts.snapshot.commit_sha,
        analysis_version=facts.snapshot.analysis_version,
        summary=intro[0] if intro else None,
        summary_evidence_id=intro[1] if intro else None,
        health=RepositoryHealthResponse(
            score=score,
            band=band,
            version=insights.HEALTH_VERSION,
            components=[HealthComponentResponse(**vars(item)) for item in components],
        ),
        counts=OverviewCounts(
            files=len(facts.paths),
            source_files=len(facts.source_paths),
            commits=len(facts.commits),
            contributors=len(authors),
            modules=len(facts.modules),
            internal_imports=len(facts.internal_imports),
            external_packages=len(packages),
            documents=len(documents),
        ),
        high_risk_modules=[
            ModuleRiskResponse(
                name=node.name,
                risk=node.risk,
                files=node.files,
                riskiest=node.riskiest,
                inferred=node.inferred,
            )
            for node in sorted(nodes, key=lambda item: -(item.risk or 0))
            if node.risk is not None
        ][:5],
        riskiest_files=[
            OverviewFileRisk(
                path=path,
                score=fact.score,
                rationale=fact.rationale,
                evidence_ids=fact.evidence_ids[:4],
            )
            for path, fact in ranked
        ],
        contributors=[
            ContributorResponse(name=names[key], commits=count)
            for key, count in authors.most_common(5)
        ],
        risk_model=facts.risk_model,
        limitations=[
            f"The health score is a transparent heuristic ({insights.HEALTH_VERSION}); each "
            "component shows its input. It is not a measure of correctness.",
            f"Contributors and commits cover the {len(facts.commits)} most recent analysed "
            "commits, not the full history.",
            "Module risk is the mean risk of each inferred module's three riskiest files.",
            *(
                [
                    f"Unstable components are an inferred forecast ({facts.instability_model}): "
                    "the probability that a bug-fix commit touches the component next period, "
                    "learned from its recent activity. Not proof of a defect."
                ]
                if facts.instability
                else []
            ),
        ],
        unstable_components=[
            UnstableComponentResponse(
                name=item.name,
                probability=item.probability,
                band=item.band,
                files=item.files,
                fixed_last_period=item.fixed_last_period,
                evidence_ids=item.evidence_ids[:5],
                model_version=facts.instability_model or "unknown",
            )
            for item in sorted(facts.instability, key=lambda entry: -entry.probability)[:8]
        ]
        if facts.instability
        else None,
    )


@router.get("/repositories/{repository_id}/module-graph", response_model=ModuleGraphResponse)
def get_module_graph(repository_id: str, db: Database, actor: Actor) -> ModuleGraphResponse:
    facts = _facts(db, repository_id, actor)
    nodes, links = insights.module_graph(facts)
    return ModuleGraphResponse(
        repository_id=repository_id,
        snapshot_sha=facts.snapshot.commit_sha,
        nodes=[ModuleNodeResponse(**vars(node)) for node in nodes],
        links=[ModuleLinkResponse(**vars(link)) for link in links],
        limitations=[
            "Modules are inferred from directory structure and co-change history.",
            "Links aggregate observed JS/TS imports and repeated co-change between modules.",
        ],
    )


def _gemini() -> tuple[str, str] | None:
    settings = get_settings()
    key = settings.gemini_api_key
    if key is None or not key.get_secret_value():
        return None
    return key.get_secret_value(), settings.gemini_model


def _stored_rewrites(
    db: Database, snapshot: RepositorySnapshot, documents: dict[str, str]
) -> dict[str, GeneratedDocRewrite]:
    """The newest stored rewrite per document whose source text still matches."""
    rows = db.scalars(
        select(GeneratedDocRewrite)
        .where(
            GeneratedDocRewrite.snapshot_id == snapshot.id,
            GeneratedDocRewrite.workspace_id == snapshot.workspace_id,
            GeneratedDocRewrite.docs_version == insights.DOCS_VERSION,
            GeneratedDocRewrite.rewrite_version == doc_rewrite.REWRITE_VERSION,
        )
        .order_by(GeneratedDocRewrite.created_at.desc())
    )
    current = {name: doc_rewrite.source_hash(markdown) for name, markdown in documents.items()}
    latest: dict[str, GeneratedDocRewrite] = {}
    for row in rows:
        if current.get(row.name) == row.source_sha256 and row.name not in latest:
            latest[row.name] = row
    return latest


def _documents_response(
    repository_id: str,
    snapshot: RepositorySnapshot,
    generated_at: datetime,
    documents: dict[str, str],
    rewrites: dict[str, GeneratedDocRewrite],
) -> GeneratedDocumentsResponse:
    items: list[GeneratedDocumentResponse] = []
    for name, markdown in documents.items():
        row = rewrites.get(name)
        items.append(
            GeneratedDocumentResponse(
                name=name,
                description=insights.DOCUMENTS[name],
                markdown=markdown,
                rewritten_markdown=row.markdown if row and row.status == "accepted" else None,
                rewrite=DocRewriteResponse(
                    model=row.model,
                    rewrite_version=row.rewrite_version,
                    status=row.status,
                    reason=row.reason,
                    created_at=row.created_at,
                )
                if row
                else None,
            )
        )
    return GeneratedDocumentsResponse(
        repository_id=repository_id,
        snapshot_sha=snapshot.commit_sha,
        version=insights.DOCS_VERSION,
        generated_at=generated_at,
        documents=items,
        rewrite_available=_gemini() is not None,
    )


@router.get("/repositories/{repository_id}/docs", response_model=GeneratedDocumentsResponse)
def get_generated_docs(
    repository_id: str, db: Database, actor: Actor
) -> GeneratedDocumentsResponse:
    facts = _facts(db, repository_id, actor)
    generated_at = utc_now()
    documents = insights.generate_documents(facts, generated_at)
    rewrites = _stored_rewrites(db, facts.snapshot, documents)
    return _documents_response(repository_id, facts.snapshot, generated_at, documents, rewrites)


def _rewrite_one(api_key: str, model: str, markdown: str) -> tuple[str, str | None, str | None]:
    """(status, reason, rewritten markdown or None); never raises."""
    header, body = doc_rewrite.split_header(markdown)
    if len(body) > doc_rewrite.MAX_SOURCE_CHARS:
        return "skipped", "The document is too long to rewrite in one request.", None
    try:
        rewritten = rewrite_document_markdown(
            api_key=api_key, model=model, markdown=body, timeout_seconds=60
        )
    except GeminiProviderError as error:
        return "rejected", f"Gemini did not return a usable rewrite ({error}).", None
    check = doc_rewrite.check_rewrite(body, rewritten)
    if not check.accepted:
        return "rejected", check.reason, None
    return "accepted", None, f"{header}\n\n{rewritten}\n" if header else rewritten


@router.post(
    "/repositories/{repository_id}/docs/rewrite", response_model=GeneratedDocumentsResponse
)
def rewrite_generated_docs(
    repository_id: str, request: Request, db: Database, actor: Actor
) -> GeneratedDocumentsResponse:
    """Ask Gemini for readable versions; each is kept only if it preserves every citation."""
    gemini = _gemini()
    if gemini is None:
        raise AppError(
            409,
            "GEMINI_NOT_CONFIGURED",
            "Gemini is not configured",
            "Set GEMINI_API_KEY on the API to write readable versions of the generated docs.",
        )
    api_key, model = gemini
    facts = _facts(db, repository_id, actor)
    snapshot = facts.snapshot
    generated_at = utc_now()
    documents = insights.generate_documents(facts, generated_at)
    existing = _stored_rewrites(db, snapshot, documents)
    pending = {
        name: markdown
        for name, markdown in documents.items()
        if name not in existing or existing[name].model != model
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = dict(
            zip(
                pending,
                pool.map(lambda markdown: _rewrite_one(api_key, model, markdown), pending.values()),
                strict=True,
            )
        )
    for name, (status, reason, markdown) in outcomes.items():
        row = GeneratedDocRewrite(
            id=new_id("drw"),
            workspace_id=actor.workspace_id,
            repository_id=repository_id,
            snapshot_id=snapshot.id,
            snapshot_sha=snapshot.commit_sha,
            name=name,
            docs_version=insights.DOCS_VERSION,
            model=model,
            rewrite_version=doc_rewrite.REWRITE_VERSION,
            source_sha256=doc_rewrite.source_hash(documents[name]),
            status=status,
            reason=reason,
            markdown=markdown,
            created_by=actor.user_id,
            created_at=utc_now(),
        )
        db.add(row)
        existing[name] = row
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="repository.docs.rewritten",
        resource_type="repository",
        resource_id=repository_id,
        after_hash=hashlib.sha256(
            ",".join(f"{name}:{outcome[0]}" for name, outcome in sorted(outcomes.items())).encode()
        ).hexdigest(),
        request_id=request.state.request_id,
    )
    db.commit()
    return _documents_response(repository_id, snapshot, generated_at, documents, existing)


@router.get("/repositories/{repository_id}/docs/{name}/download")
def download_generated_doc(
    repository_id: str, name: str, request: Request, db: Database, actor: Actor
) -> Response:
    if name not in insights.DOCUMENTS:
        raise AppError(404, "NOT_FOUND", "Document not found", "Unknown generated document.")
    facts = _facts(db, repository_id, actor)
    markdown = insights.generate_documents(facts, utc_now())[name]
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action="repository.docs.downloaded",
        resource_type="repository",
        resource_id=repository_id,
        after_hash=name,
        request_id=request.state.request_id,
    )
    db.commit()
    return Response(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
