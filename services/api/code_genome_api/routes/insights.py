from collections import Counter
from pathlib import PurePosixPath

from fastapi import APIRouter, Request, Response

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..errors import AppError
from ..models import utc_now
from ..schemas import (
    ContributorResponse,
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
)
from ..services import insights
from ..services.knowledge import classify
from .intelligence import _repository, _snapshot, get_risk

router = APIRouter(tags=["insights"])


def _facts(db: Database, repository_id: str, actor: Actor) -> insights.SnapshotFacts:
    repository = _repository(db, repository_id, actor)
    snapshot = _snapshot(db, repository_id, actor)
    risk = get_risk(repository_id, db, actor)
    return insights.load_facts(
        db,
        snapshot,
        repository.external_id,
        {
            item.path: insights.RiskFact(item.score, item.rationale, item.evidence_ids)
            for item in risk.scores
        },
        risk.scores[0].model_version if risk.scores else "none",
    )


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
            band=band,  # type: ignore[arg-type]
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
        ],
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


@router.get("/repositories/{repository_id}/docs", response_model=GeneratedDocumentsResponse)
def get_generated_docs(
    repository_id: str, db: Database, actor: Actor
) -> GeneratedDocumentsResponse:
    facts = _facts(db, repository_id, actor)
    generated_at = utc_now()
    documents = insights.generate_documents(facts, generated_at)
    return GeneratedDocumentsResponse(
        repository_id=repository_id,
        snapshot_sha=facts.snapshot.commit_sha,
        version=insights.DOCS_VERSION,
        generated_at=generated_at,
        documents=[
            GeneratedDocumentResponse(
                name=name, description=insights.DOCUMENTS[name], markdown=markdown
            )
            for name, markdown in documents.items()
        ],
    )


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
