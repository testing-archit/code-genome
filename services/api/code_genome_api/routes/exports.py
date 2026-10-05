import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel

from ..audit import record_audit_event
from ..auth import Actor, Database
from ..errors import AppError
from ..models import Repository, utc_now
from ..schemas import ArchitectureResponse, RiskResponse, SnapshotComparisonResponse
from .architecture import get_architecture
from .intelligence import _repository, get_risk
from .snapshots import compare_snapshots

router = APIRouter(tags=["exports"])

ExportKind = Literal["architecture", "risk", "comparison"]
ExportFormat = Literal["md", "json"]


def _code(value: str) -> str:
    return "`" + value.replace("`", "'").replace("\n", " ") + "`"


def _text(value: str) -> str:
    return " ".join(value.split())


def _header(
    title: str, repository: Repository, generated_at: datetime, scope: list[str]
) -> list[str]:
    return [
        f"# {title}",
        "",
        f"- Repository: {_code(repository.external_id)} ({_code(repository.id)})",
        *[f"- {line}" for line in scope],
        f"- Generated: {generated_at.isoformat()}",
        "",
    ]


def _limitations(items: list[str]) -> list[str]:
    return ["## Limitations", "", *[f"- {_text(item)}" for item in items], ""]


def _architecture_md(
    repository: Repository, data: ArchitectureResponse, generated_at: datetime
) -> str:
    lines = _header(
        "Architecture report",
        repository,
        generated_at,
        [
            f"Snapshot: {_code(data.snapshot_sha)}",
            f"Analysis version: {_code(data.analysis_version)}",
        ],
    )
    lines += ["## Modules (inferred)", ""]
    for module in data.modules:
        lines += [
            f"### {_code(module.name)}",
            "",
            _text(module.description),
            "",
            f"- Confidence: {module.confidence:.2f}",
            f"- Files ({len(module.file_paths)}): "
            + ", ".join(_code(path) for path in module.file_paths[:40])
            + (" …" if len(module.file_paths) > 40 else ""),
            "- Evidence: " + (", ".join(module.citations[:10]) or "none"),
            "",
        ]
    lines += [
        "## Hotspots",
        "",
        "| File | Commits | Churn | Score | Evidence |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {_code(item.path)} | {item.commit_count} | {item.churn} | {item.score:.2f} | "
        f"{', '.join(item.citations[:3]) or 'none'} |"
        for item in data.hotspots
    ]
    lines += ["", "## Co-change", "", "| Files | Commits | Confidence |", "|---|---|---|"]
    lines += [
        f"| {_code(item.left_path)} ↔ {_code(item.right_path)} | {item.commit_count} | "
        f"{item.confidence:.2f} |"
        for item in data.co_changes
    ]
    lines.append("")
    return "\n".join(lines + _limitations(data.limitations))


def _risk_md(repository: Repository, data: RiskResponse, generated_at: datetime) -> str:
    model = data.scores[0].model_version if data.scores else "none"
    lines = _header(
        "Risk report",
        repository,
        generated_at,
        [f"Snapshot: {_code(data.snapshot_sha)}", f"Model: {_code(model)}"],
    )
    if not data.scores:
        lines += ["No supporting evidence was identified in the selected scope.", ""]
    else:
        lines += ["| # | File | Score | Rationale | Evidence |", "|---|---|---|---|---|"]
        lines += [
            f"| {index} | {_code(item.path)} | {item.score:.2f} | "
            f"{_text(item.rationale).replace('|', '/')} | "
            f"{', '.join(item.evidence_ids[:3]) or 'none'} |"
            for index, item in enumerate(data.scores, start=1)
        ]
        lines.append("")
    return "\n".join(lines + _limitations(data.limitations))


def _comparison_md(
    repository: Repository, data: SnapshotComparisonResponse, generated_at: datetime
) -> str:
    counts = data.counts
    lines = _header(
        "Snapshot comparison",
        repository,
        generated_at,
        [
            f"Base: {_code(data.base.commit_sha)} ({', '.join(data.base.refs) or 'no ref'}, "
            f"{data.base.analysis_version})",
            f"Head: {_code(data.head.commit_sha)} ({', '.join(data.head.refs) or 'no ref'}, "
            f"{data.head.analysis_version})",
        ],
    )
    if data.unavailable:
        lines += [f"Not compared: {', '.join(data.unavailable)} (see limitations).", ""]
    lines += [
        "## Summary",
        "",
        f"- Files: {counts.files_added} added, {counts.files_removed} removed, "
        f"{counts.files_modified} modified",
        f"- Imports: {counts.imports_added} added, {counts.imports_removed} removed",
        f"- Modules changed (inferred): {counts.modules_changed}",
        "",
    ]
    for title, files in (
        ("Added files", data.files_added),
        ("Removed files", data.files_removed),
        ("Modified files", data.files_modified),
    ):
        lines += [f"## {title}", ""]
        lines += [f"- {_code(item.path)} ({item.size_delta:+d} bytes)" for item in files] or [
            "- none"
        ]
        lines.append("")
    for title, imports in (
        ("Added imports", data.imports_added),
        ("Removed imports", data.imports_removed),
    ):
        lines += [f"## {title}", ""]
        lines += [
            f"- {_code(item.source)} → {_code(item.target)} ({item.evidence_id})"
            for item in imports
        ] or ["- none"]
        lines.append("")
    lines += ["## Module changes (inferred)", ""]
    lines += [
        f"- {_code(item.name)}: {item.status}, +{len(item.added_files)} / "
        f"-{len(item.removed_files)} files"
        for item in data.modules
    ] or ["- none"]
    lines += ["", "## Hotspot movement", ""]
    lines += [
        f"- {_code(item.path)}: {item.base_score if item.base_score is not None else '—'} → "
        f"{item.head_score if item.head_score is not None else '—'} ({item.delta:+.2f})"
        for item in data.hotspots
    ] or ["- none"]
    lines.append("")
    return "\n".join(lines + _limitations(data.limitations))


@router.get("/repositories/{repository_id}/exports/{kind}")
def export_report(
    repository_id: str,
    kind: ExportKind,
    request: Request,
    db: Database,
    actor: Actor,
    format: Annotated[ExportFormat, Query()] = "md",
    base: Annotated[str | None, Query(min_length=7, max_length=64)] = None,
    head: Annotated[str | None, Query(min_length=7, max_length=64)] = None,
) -> Response:
    """Download a cited report. Every export is recorded as an audit event."""
    repository = _repository(db, repository_id, actor)
    generated_at = utc_now()
    data: BaseModel
    if kind == "architecture":
        data = get_architecture(repository_id, db, actor, relationship_limit=500)
        snapshot_sha = data.snapshot_sha
        markdown = _architecture_md(repository, data, generated_at) if format == "md" else ""
    elif kind == "risk":
        data = get_risk(repository_id, db, actor)
        snapshot_sha = data.snapshot_sha
        markdown = _risk_md(repository, data, generated_at) if format == "md" else ""
    else:
        if not base or not head:
            raise AppError(
                400, "INVALID_SCOPE", "Snapshots required", "Comparison exports need base and head."
            )
        data = compare_snapshots(db, repository_id, actor, base, head)
        snapshot_sha = f"{data.base.commit_sha[:12]}..{data.head.commit_sha[:12]}"
        markdown = _comparison_md(repository, data, generated_at) if format == "md" else ""

    if format == "json":
        envelope: dict[str, Any] = {
            "export": {
                "kind": kind,
                "repository_id": repository.id,
                "repository": repository.external_id,
                "snapshot": snapshot_sha,
                "generated_at": generated_at.isoformat(),
            },
            "data": data.model_dump(mode="json", by_alias=True),
        }
        body = json.dumps(envelope, indent=2, ensure_ascii=False)
        media_type = "application/json"
    else:
        body = markdown
        media_type = "text/markdown; charset=utf-8"

    digest = hashlib.sha256(body.encode()).hexdigest()
    record_audit_event(
        db,
        workspace_id=actor.workspace_id,
        actor_id=actor.user_id,
        action=f"repository.export.{kind}",
        resource_type="repository",
        resource_id=repository.id,
        after_hash=digest,
        request_id=request.state.request_id,
    )
    db.commit()
    name = repository.external_id.replace("/", "-")
    label = "-".join(part[:12] for part in snapshot_sha.split(".."))
    filename = f"{name}-{kind}-{label}.{format}"
    return Response(
        body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-SHA256": digest,
        },
    )
