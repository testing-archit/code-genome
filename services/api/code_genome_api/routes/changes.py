from fastapi import APIRouter
from sqlalchemy import select

from ..auth import Actor, Database
from ..errors import AppError
from ..models import FileManifestEntry, ModuleCandidate, utc_now
from ..schemas import (
    ChangedFileResponse,
    ChangeImpactCreate,
    ChangeImpactItemResponse,
    ChangeImpactResponse,
    ChangeImpactSummary,
    ChangeModuleResponse,
)
from ..services.diffs import DiffError, FileDiff, parse_unified_diff
from ..services.impact import impact_for_paths
from .intelligence import _repository, _snapshot, get_risk

router = APIRouter(tags=["intelligence"])

HIGH_RISK_THRESHOLD = 0.6
MAX_IMPACTED = 50


def _proposed_changes(payload: ChangeImpactCreate) -> list[FileDiff]:
    changes: dict[str, FileDiff] = {}
    if payload.diff and payload.diff.strip():
        try:
            for item in parse_unified_diff(payload.diff):
                changes[item.path] = item
        except DiffError as error:
            raise AppError(400, "INVALID_DIFF", "Diff could not be read", str(error)) from error
    for path in payload.paths:
        changes.setdefault(path, FileDiff(path=path, change="listed"))
    if not changes:
        raise AppError(
            400, "INVALID_SCOPE", "Nothing to check", "Provide a unified diff or at least one path."
        )
    if len(changes) > 200:
        raise AppError(400, "INVALID_SCOPE", "Change too large", "Checks are limited to 200 files.")
    return list(changes.values())


@router.post("/repositories/{repository_id}/impact/change", response_model=ChangeImpactResponse)
def check_change_impact(
    repository_id: str, payload: ChangeImpactCreate, db: Database, actor: Actor
) -> ChangeImpactResponse:
    """Rank the files a proposed change may affect, against the latest published snapshot."""
    _repository(db, repository_id, actor)
    snapshot = _snapshot(db, repository_id, actor)
    changes = _proposed_changes(payload)

    manifest = set(
        db.scalars(
            select(FileManifestEntry.path).where(
                FileManifestEntry.snapshot_id == snapshot.id,
                FileManifestEntry.workspace_id == actor.workspace_id,
            )
        )
    )
    # A renamed file's history and graph edges live under its previous path.
    lookup = {
        item.path: (
            item.previous_path
            if item.path not in manifest and item.previous_path in manifest
            else item.path
        )
        for item in changes
    }
    module_rows = list(
        db.scalars(
            select(ModuleCandidate).where(
                ModuleCandidate.snapshot_id == snapshot.id,
                ModuleCandidate.workspace_id == actor.workspace_id,
            )
        )
    )
    modules_by_path: dict[str, list[str]] = {}
    for module in module_rows:
        for path in module.file_paths:
            modules_by_path.setdefault(path, []).append(module.natural_key)
    inferred_modules = {module.natural_key: module.inferred for module in module_rows}

    risk = get_risk(repository_id, db, actor)
    risk_by_path = {item.path: item for item in risk.scores}
    impact, limitations = impact_for_paths(db, snapshot, list(dict.fromkeys(lookup.values())))

    changed_lookups = set(lookup.values()) | {item.path for item in changes}
    aggregated: dict[str, ChangeImpactItemResponse] = {}
    for item in changes:
        for neighbour in impact.get(lookup[item.path], []):
            # Package nodes such as `external:react` are dependencies, not repository files.
            if neighbour.path in changed_lookups or neighbour.path.startswith("external:"):
                continue
            existing = aggregated.get(neighbour.path)
            if existing is None:
                aggregated[neighbour.path] = ChangeImpactItemResponse(
                    path=neighbour.path,
                    score=neighbour.score,
                    reasons=list(neighbour.reasons),
                    evidence_ids=list(neighbour.evidence_ids),
                    via=[item.path],
                    modules=modules_by_path.get(neighbour.path, []),
                    signals=neighbour.signals,
                    weighted_score=neighbour.weighted_score,
                )
                continue
            # Keep the "why" from whichever changed file links to it most strongly.
            if (neighbour.weighted_score or 0) > (existing.weighted_score or 0):
                existing.signals = neighbour.signals
                existing.weighted_score = neighbour.weighted_score
            existing.score = max(existing.score, neighbour.score)
            existing.via.append(item.path)
            existing.reasons.extend(r for r in neighbour.reasons if r not in existing.reasons)
            existing.evidence_ids.extend(
                e for e in neighbour.evidence_ids if e not in existing.evidence_ids
            )
    impacted = sorted(aggregated.values(), key=lambda entry: (-entry.score, entry.path))

    changed: list[ChangedFileResponse] = []
    for item in changes:
        key = lookup[item.path]
        score = risk_by_path.get(key)
        changed.append(
            ChangedFileResponse(
                path=item.path,
                change=item.change,
                previous_path=item.previous_path,
                additions=item.additions,
                deletions=item.deletions,
                in_snapshot=key in manifest,
                risk_score=score.score if score else None,
                risk_rationale=score.rationale if score else None,
                risk_model=score.model_version if score else None,
                modules=modules_by_path.get(key, []),
                evidence_ids=score.evidence_ids[:10] if score else [],
            )
        )
    changed.sort(key=lambda entry: (-(entry.risk_score or 0), entry.path))

    module_counts: dict[str, list[int]] = {}
    for entry in changed:
        for name in entry.modules:
            module_counts.setdefault(name, [0, 0])[0] += 1
    for impacted_entry in impacted:
        for name in impacted_entry.modules:
            module_counts.setdefault(name, [0, 0])[1] += 1
    modules = sorted(
        (
            ChangeModuleResponse(
                name=name,
                changed_files=counts[0],
                impacted_files=counts[1],
                inferred=inferred_modules.get(name, True),
            )
            for name, counts in module_counts.items()
        ),
        key=lambda entry: (-entry.changed_files, -entry.impacted_files, entry.name),
    )

    risk_values = [entry.risk_score for entry in changed if entry.risk_score is not None]
    missing = [entry.path for entry in changed if not entry.in_snapshot]
    limitations = [
        *limitations,
        "Risk and impact describe the files as they exist in the analysed snapshot, "
        "not the proposed new content.",
        *risk.limitations[:1],
    ]
    if missing:
        limitations.append(
            f"{len(missing)} changed file(s) are not in snapshot {snapshot.commit_sha[:12]}; "
            "they have no history or graph evidence here."
        )
    if len(impacted) > MAX_IMPACTED:
        limitations.append(f"Impacted files are limited to the top {MAX_IMPACTED}.")
    return ChangeImpactResponse(
        repository_id=repository_id,
        snapshot_id=snapshot.id,
        snapshot_sha=snapshot.commit_sha,
        analysis_version=snapshot.analysis_version,
        generated_at=utc_now(),
        summary=ChangeImpactSummary(
            changed_files=len(changed),
            changed_in_snapshot=len(changed) - len(missing),
            impacted_files=len(impacted),
            modules_touched=len(modules),
            max_risk=max(risk_values) if risk_values else None,
            high_risk_files=sum(1 for value in risk_values if value >= HIGH_RISK_THRESHOLD),
        ),
        changed=changed,
        impacted=impacted[:MAX_IMPACTED],
        modules=modules,
        limitations=limitations,
    )
