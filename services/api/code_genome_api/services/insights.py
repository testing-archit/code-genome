"""Repository-level insights derived only from stored snapshot evidence.

* A transparent health score whose every component, weight, and input is returned.
* A module-level dependency graph (imports and co-change aggregated across inferred modules).
* Generated documentation (architecture, modules, data flow, dependencies, business logic,
  risk) assembled from cited facts. Nothing here is model-generated, and each document
  states what it cannot show (for example runtime data flow).
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

import networkx as nx
from code_genome_git import is_fix_message
from code_genome_ml import group_components
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    BugLink,
    CoChangeEdge,
    FileChange,
    FileHotspot,
    FileManifestEntry,
    GraphEdge,
    GraphNode,
    KnowledgeChunkRecord,
    ModuleCandidate,
    RepositoryCommit,
    RepositorySnapshot,
)
from .knowledge import SKIPPED_DIRECTORIES

HEALTH_VERSION = "health-heuristic@2"
HIGH_RISK = 0.6
DOCS_VERSION = "generated-docs@1"
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|test-d)(/|$)|\.(test|spec)\.[a-z]+$", re.I)
_CODE_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}


@dataclass
class RiskFact:
    score: float
    rationale: str
    evidence_ids: list[str]


@dataclass(frozen=True)
class InstabilityFact:
    """One component forecast from the trained instability model (inferred, not a fact)."""

    name: str
    probability: float
    band: str
    files: int
    fixed_last_period: bool
    evidence_ids: list[str]


def instability_facts(result: dict[str, Any] | None) -> tuple[list[InstabilityFact], str | None]:
    """Read a stored ``instability`` model result; empty when untrained or abstained."""
    if not result or result.get("status") != "trained":
        return [], None
    facts = []
    for item in result.get("predictions", []):
        try:
            facts.append(
                InstabilityFact(
                    name=str(item["component"]),
                    probability=float(item["probability"]),
                    band=item["band"] if item.get("band") in {"high", "medium"} else "low",
                    files=int(item.get("files", 0)),
                    fixed_last_period=bool(item.get("fixed_last_period", False)),
                    evidence_ids=[f"commit:{sha}" for sha in item.get("evidence_shas", [])],
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return facts, str(result.get("model_version", "unknown"))


@dataclass
class SnapshotFacts:
    repository_name: str
    snapshot: RepositorySnapshot
    paths: list[str]
    source_paths: list[str]
    imports: list[tuple[str, str, str]]  # (source file, target key, evidence id)
    modules: list[ModuleCandidate]
    module_of: dict[str, str]
    commits: list[RepositoryCommit]
    risk: dict[str, RiskFact]
    risk_model: str
    hotspots: list[FileHotspot]
    co_changes: list[CoChangeEdge]
    symbols: dict[str, list[str]]
    chunks: list[KnowledgeChunkRecord]
    file_evidence: dict[str, str] = field(default_factory=dict)
    component_of: dict[str, str] = field(default_factory=dict)
    instability: list[InstabilityFact] = field(default_factory=list)
    instability_model: str | None = None
    file_properties: dict[str, dict[str, Any]] = field(default_factory=dict)
    file_changes: list[FileChange] = field(default_factory=list)
    bug_links: list[BugLink] = field(default_factory=list)

    @property
    def internal_imports(self) -> list[tuple[str, str, str]]:
        known = set(self.paths)
        return [item for item in self.imports if item[1] in known]

    @property
    def external_imports(self) -> list[tuple[str, str, str]]:
        return [item for item in self.imports if item[1].startswith("external:")]


def load_facts(
    db: Session,
    snapshot: RepositorySnapshot,
    repository_name: str,
    risk: dict[str, RiskFact],
    risk_model: str,
) -> SnapshotFacts:
    workspace = snapshot.workspace_id
    paths = sorted(
        db.scalars(
            select(FileManifestEntry.path).where(
                FileManifestEntry.snapshot_id == snapshot.id,
                FileManifestEntry.workspace_id == workspace,
            )
        )
    )
    nodes = {
        node.id: node
        for node in db.scalars(
            select(GraphNode).where(
                GraphNode.snapshot_id == snapshot.id, GraphNode.workspace_id == workspace
            )
        )
    }
    imports: list[tuple[str, str, str]] = []
    for edge in db.scalars(
        select(GraphEdge).where(
            GraphEdge.snapshot_id == snapshot.id,
            GraphEdge.workspace_id == workspace,
            GraphEdge.type == "IMPORTS",
        )
    ):
        source, target = nodes.get(edge.from_node), nodes.get(edge.to_node)
        if source is not None and target is not None:
            imports.append((source.natural_key, target.natural_key, edge.provenance_id))
    symbols: dict[str, list[str]] = defaultdict(list)
    file_evidence: dict[str, str] = {}
    file_properties: dict[str, dict[str, Any]] = {}
    for node in nodes.values():
        if node.kind == "SYMBOL":
            name = node.properties_json.get("name")
            path = str(node.properties_json.get("path") or node.natural_key.split("#", 1)[0])
            if isinstance(name, str):
                symbols[path].append(name)
        elif node.kind == "FILE":
            file_properties[node.natural_key] = node.properties_json or {}
            if node.evidence_ids:
                file_evidence[node.natural_key] = node.evidence_ids[0]
    modules = sorted(
        db.scalars(
            select(ModuleCandidate).where(
                ModuleCandidate.snapshot_id == snapshot.id,
                ModuleCandidate.workspace_id == workspace,
            )
        ),
        key=lambda module: module.natural_key,
    )
    module_of: dict[str, str] = {}
    for module in modules:
        for path in module.file_paths:
            module_of.setdefault(path, module.natural_key)
    commits = list(
        db.scalars(
            select(RepositoryCommit)
            .where(
                RepositoryCommit.repository_id == snapshot.repository_id,
                RepositoryCommit.workspace_id == workspace,
            )
            .order_by(RepositoryCommit.authored_at.desc())
        )
    )
    hotspots = list(
        db.scalars(
            select(FileHotspot)
            .where(FileHotspot.snapshot_id == snapshot.id, FileHotspot.workspace_id == workspace)
            .order_by(FileHotspot.score.desc())
        )
    )
    co_changes = list(
        db.scalars(
            select(CoChangeEdge)
            .where(CoChangeEdge.snapshot_id == snapshot.id, CoChangeEdge.workspace_id == workspace)
            .order_by(CoChangeEdge.commit_count.desc())
        )
    )
    chunks = list(
        db.scalars(
            select(KnowledgeChunkRecord)
            .where(
                KnowledgeChunkRecord.snapshot_id == snapshot.id,
                KnowledgeChunkRecord.workspace_id == workspace,
                KnowledgeChunkRecord.kind.in_(("readme", "doc", "manifest")),
            )
            .order_by(KnowledgeChunkRecord.path, KnowledgeChunkRecord.ordinal)
        )
    )
    source_paths = [
        path
        for path in paths
        if PurePosixPath(path).suffix.lower() in _CODE_SUFFIXES
        and not any(part.lower() in SKIPPED_DIRECTORIES for part in PurePosixPath(path).parts)
    ]
    file_changes = list(
        db.scalars(
            select(FileChange).where(
                FileChange.snapshot_id == snapshot.id, FileChange.workspace_id == workspace
            )
        )
    )
    bug_links = list(
        db.scalars(
            select(BugLink)
            .where(BugLink.snapshot_id == snapshot.id, BugLink.workspace_id == workspace)
            .order_by(BugLink.fix_sha, BugLink.path, BugLink.introducing_sha)
        )
    )
    return SnapshotFacts(
        file_properties=file_properties,
        file_changes=file_changes,
        bug_links=bug_links,
        component_of=components(source_paths),
        repository_name=repository_name,
        snapshot=snapshot,
        paths=paths,
        source_paths=source_paths,
        imports=imports,
        modules=modules,
        module_of=module_of,
        commits=commits,
        risk=risk,
        risk_model=risk_model,
        hotspots=hotspots,
        co_changes=co_changes,
        symbols=dict(symbols),
        chunks=chunks,
        file_evidence=file_evidence,
    )


def components(paths: list[str], minimum: int = 5, maximum: int = 18) -> dict[str, str]:
    """Group source files by directory (see ``code_genome_ml.group_components``)."""
    return group_components(paths, minimum, maximum)


# ---------------------------------------------------------------- health


@dataclass(frozen=True)
class HealthComponent:
    key: str
    label: str
    score: float
    maximum: float
    value: str
    detail: str


def _cycle_files(facts: SnapshotFacts) -> set[str]:
    graph = nx.DiGraph()
    graph.add_edges_from((source, target) for source, target, _ in facts.internal_imports)
    return {
        node
        for component in nx.strongly_connected_components(graph)
        if len(component) > 1
        for node in component
    }


def health(facts: SnapshotFacts) -> tuple[int, str, list[HealthComponent]]:
    parts: list[HealthComponent] = []

    population = max(1, len(facts.source_paths) or len(facts.risk))
    risky = sum(1 for item in facts.risk.values() if item.score >= HIGH_RISK)
    share_risky = risky / population
    parts.append(
        HealthComponent(
            "risk",
            "Change risk",
            round(30 * max(0.0, 1 - share_risky / 0.3), 1),
            30,
            f"{risky} of {population} files score ≥ {HIGH_RISK:.0%} ({facts.risk_model})",
            "Full marks when no file is high-risk; zero when 30% or more are. Scores come "
            "from the defect model when trained, otherwise the relative hotspot baseline.",
        )
    )

    in_cycles = _cycle_files(facts)
    linked = {path for source, target, _ in facts.internal_imports for path in (source, target)}
    share = len(in_cycles) / len(linked) if linked else 0.0
    parts.append(
        HealthComponent(
            "coupling",
            "Coupling",
            round(20 * (1 - share), 1),
            20,
            f"{len(in_cycles)} of {len(linked)} linked files in import cycles",
            "Files inside circular import groups are harder to change independently.",
        )
    )

    authors = Counter(commit.author_email.lower() for commit in facts.commits)
    top_share = authors.most_common(1)[0][1] / len(facts.commits) if facts.commits else 1.0
    parts.append(
        HealthComponent(
            "ownership",
            "Knowledge spread",
            round(20 * min(1.0, (1 - top_share) / 0.5), 1),
            20,
            f"{len(authors)} contributors; top author {top_share:.0%} of commits",
            "Full marks when no single author made more than half of the analysed commits.",
        )
    )

    readme = any(item.kind == "readme" for item in facts.chunks)
    doc_files = {item.path for item in facts.chunks if item.kind == "doc"}
    parts.append(
        HealthComponent(
            "docs",
            "Documentation",
            round((8 if readme else 0) + 7 * min(1.0, len(doc_files) / 3), 1),
            15,
            f"README {'present' if readme else 'missing'}; {len(doc_files)} other docs",
            "A README plus at least three further documentation files earn full marks.",
        )
    )

    tests = [path for path in facts.paths if _TEST_PATH.search(path)]
    ratio = len(tests) / len(facts.source_paths) if facts.source_paths else 0.0
    parts.append(
        HealthComponent(
            "tests",
            "Test presence",
            round(15 * min(1.0, ratio / 0.3), 1),
            15,
            f"{len(tests)} test files for {len(facts.source_paths)} source files",
            "Counts test files only; it says nothing about whether the tests pass.",
        )
    )
    total = round(sum(item.score for item in parts))
    band = "Healthy" if total >= 80 else "Moderate" if total >= 60 else "At risk"
    return total, band, parts


# ---------------------------------------------------------------- modules


@dataclass
class ModuleNode:
    name: str
    files: int
    risk: float | None
    fan_in: int
    fan_out: int
    externals: list[str]
    inferred: bool
    description: str
    riskiest: list[str]
    paths: list[str] = field(default_factory=list)
    role: str = "unknown"
    role_signal: str = ""
    datastores: list[dict[str, Any]] = field(default_factory=list)
    integrations: list[dict[str, Any]] = field(default_factory=list)
    contributors: list[dict[str, Any]] = field(default_factory=list)
    commits: int = 0
    bug_fixes: int = 0
    last_changed: datetime | None = None


@dataclass
class ModuleLink:
    source: str
    target: str
    imports: int
    co_changes: int
    evidence_ids: list[str]


def group_risk(facts: SnapshotFacts, paths: list[str]) -> tuple[float | None, list[str]]:
    """Mean risk of a group's three riskiest scored files."""
    scored = sorted(
        ((facts.risk[path].score, path) for path in paths if path in facts.risk),
        reverse=True,
    )
    if not scored:
        return None, []
    top = scored[:3]
    return round(sum(score for score, _ in top) / len(top), 4), [path for _, path in top]


# ---------------------------------------------------------------- architecture roles

ROLES_VERSION = "roles-heuristic@1"
ROLE_ORDER = (
    "api",
    "service",
    "data",
    "contract",
    "ui",
    "util",
    "config",
    "infra/scripts",
    "test",
    "unknown",
)
_ROLE_SEGMENTS: dict[str, str] = {
    **dict.fromkeys(
        ("tests", "test", "__tests__", "spec", "specs", "e2e", "cypress", "playwright"), "test"
    ),
    **dict.fromkeys(("contracts", "contract", "solidity"), "contract"),
    **dict.fromkeys(
        (
            "routes", "route", "controllers", "controller", "handlers", "handler", "api",
            "pages", "app", "endpoints", "server", "resolvers", "graphql", "middleware",
            "middlewares", "webhooks",
        ),
        "api",
    ),
    **dict.fromkeys(
        (
            "models", "model", "schema", "schemas", "db", "database", "repositories",
            "repository", "migrations", "entities", "entity", "prisma", "dao", "orm", "drizzle",
        ),
        "data",
    ),
    **dict.fromkeys(
        (
            "services", "service", "domain", "usecases", "use-cases", "core", "workers",
            "worker", "jobs", "queues", "features", "modules",
        ),
        "service",
    ),
    **dict.fromkeys(
        (
            "components", "component", "views", "view", "ui", "layouts", "layout", "widgets",
            "screens", "hooks", "styles", "templates",
        ),
        "ui",
    ),
    **dict.fromkeys(("config", "configs", "settings", "env", "constants"), "config"),
    **dict.fromkeys(
        ("scripts", "script", "infra", "deploy", "tools", "bin", "ci", ".github", "docker"),
        "infra/scripts",
    ),
    **dict.fromkeys(("utils", "util", "helpers", "helper", "lib", "common", "shared"), "util"),
}  # fmt: skip
_API_FRAMEWORKS = {
    "express", "fastify", "koa", "hono", "@nestjs/common", "@hapi/hapi", "next", "restify",
    "@trpc/server", "apollo-server", "@apollo/server", "graphql-yoga",
}  # fmt: skip
_UI_PACKAGES = {"react", "react-dom", "vue", "svelte", "solid-js", "preact", "@angular/core"}

# Static import → data store. Detected from imports only; reads/writes are inferred.
DATASTORE_PACKAGES: dict[str, str] = {
    "prisma": "Prisma (SQL database)",
    "@prisma/client": "Prisma (SQL database)",
    "pg": "PostgreSQL",
    "postgres": "PostgreSQL",
    "@neondatabase/serverless": "PostgreSQL",
    "@vercel/postgres": "PostgreSQL",
    "mysql": "MySQL",
    "mysql2": "MySQL",
    "mongoose": "MongoDB",
    "mongodb": "MongoDB",
    "sequelize": "SQL database (Sequelize)",
    "typeorm": "SQL database (TypeORM)",
    "knex": "SQL database (Knex)",
    "drizzle-orm": "SQL database (Drizzle)",
    "kysely": "SQL database (Kysely)",
    "redis": "Redis",
    "ioredis": "Redis",
    "@upstash/redis": "Redis",
    "sqlite": "SQLite",
    "sqlite3": "SQLite",
    "better-sqlite3": "SQLite",
    "@supabase/supabase-js": "Supabase",
    "firebase": "Firebase",
    "firebase-admin": "Firebase",
    "@aws-sdk/client-dynamodb": "DynamoDB",
    "@aws-sdk/lib-dynamodb": "DynamoDB",
    "@aws-sdk/client-s3": "Amazon S3",
    "@google-cloud/firestore": "Firestore",
    "@elastic/elasticsearch": "Elasticsearch",
}
INTEGRATION_PACKAGES: dict[str, str] = {
    "stripe": "Stripe",
    "@stripe/stripe-js": "Stripe",
    "openai": "OpenAI",
    "@anthropic-ai/sdk": "Anthropic",
    "@google/genai": "Google Gemini",
    "@google/generative-ai": "Google Gemini",
    "ethers": "Ethereum (ethers)",
    "web3": "Ethereum (web3)",
    "viem": "Ethereum (viem)",
    "wagmi": "Ethereum (wagmi)",
    "twilio": "Twilio",
    "aws-sdk": "AWS",
    "nodemailer": "SMTP email",
    "@sendgrid/mail": "SendGrid",
    "resend": "Resend",
    "@octokit/rest": "GitHub API",
    "@octokit/core": "GitHub API",
    "octokit": "GitHub API",
    "@slack/web-api": "Slack",
    "discord.js": "Discord",
    "@sentry/node": "Sentry",
    "@sentry/nextjs": "Sentry",
    "@sentry/react": "Sentry",
    "posthog-js": "PostHog",
    "posthog-node": "PostHog",
    "algoliasearch": "Algolia",
    "pusher": "Pusher",
    "cloudinary": "Cloudinary",
    "@clerk/nextjs": "Clerk",
    "@auth0/nextjs-auth0": "Auth0",
    "@vercel/blob": "Vercel Blob",
    "replicate": "Replicate",
    "langchain": "LangChain",
    "@pinecone-database/pinecone": "Pinecone",
}


def package_name(specifier: str) -> str | None:
    """Normalize an import specifier to its npm package name (None for node builtins)."""
    value = specifier.removeprefix("external:")
    if value.startswith("node:") or not value:
        return None
    parts = value.split("/")
    return "/".join(parts[:2]) if value.startswith("@") and len(parts) > 1 else parts[0]


def _integration_name(package: str) -> str | None:
    if package in INTEGRATION_PACKAGES:
        return INTEGRATION_PACKAGES[package]
    if package.startswith("@aws-sdk/") and package not in DATASTORE_PACKAGES:
        return "AWS"
    return None


@dataclass
class ArchitectureFacts:
    """Per-component roles, data stores, and integrations, each with its deciding signal."""

    roles: dict[str, tuple[str, str]]
    datastores: dict[str, list[dict[str, Any]]]
    integrations: dict[str, list[dict[str, Any]]]


def _role_for(
    name: str,
    paths: list[str],
    packages: Counter[str],
    touches_datastore: bool,
) -> tuple[str, str]:
    segments = [part.lower() for part in PurePosixPath(name).parts if not part.startswith("(")]
    for segment in reversed(segments):
        role = _ROLE_SEGMENTS.get(segment)
        if role:
            return role, f"path segment '{segment}'"
    if paths and sum(1 for path in paths if _TEST_PATH.search(path)) / len(paths) > 0.5:
        return "test", "most files are test files"
    frameworks = sorted(set(packages) & _API_FRAMEWORKS)
    if frameworks:
        return "api", f"imports {frameworks[0]}"
    if touches_datastore:
        return "data", "imports a data-store client"
    jsx = sum(1 for path in paths if PurePosixPath(path).suffix.lower() in {".tsx", ".jsx"})
    if paths and jsx / len(paths) > 0.5:
        return "ui", f"{jsx} of {len(paths)} files are JSX/TSX"
    ui = sorted(set(packages) & _UI_PACKAGES)
    if ui:
        return "ui", f"imports {ui[0]}"
    return "unknown", "no deterministic path or import signal"


def architecture_facts(facts: SnapshotFacts) -> ArchitectureFacts:
    """Classify each component and detect data stores and integrations from static imports."""
    members: dict[str, list[str]] = defaultdict(list)
    for path, name in facts.component_of.items():
        members[name].append(path)
    imported_packages: dict[str, dict[str, str]] = defaultdict(dict)  # path -> package -> ev
    for source, target, evidence in facts.external_imports:
        package = package_name(target)
        if package:
            imported_packages[source].setdefault(package, evidence)
    # Files that use a data store directly, or through one internal import hop.
    direct: dict[str, dict[str, str]] = defaultdict(dict)  # path -> store -> evidence
    for path, file_packages in imported_packages.items():
        for package, evidence in file_packages.items():
            if package in DATASTORE_PACKAGES:
                direct[path].setdefault(DATASTORE_PACKAGES[package], evidence)
    through: dict[str, dict[str, str]] = defaultdict(dict)
    for source, target, evidence in facts.internal_imports:
        for store in direct.get(target, {}):
            through[source].setdefault(store, evidence)

    roles: dict[str, tuple[str, str]] = {}
    datastores: dict[str, list[dict[str, Any]]] = {}
    integrations: dict[str, list[dict[str, Any]]] = {}
    for name, paths in members.items():
        packages: Counter[str] = Counter()
        for path in paths:
            packages.update(imported_packages.get(path, {}).keys())
        stores: dict[str, dict[str, Any]] = {}
        for path in sorted(paths):
            for kind, mapping in (("direct", direct), ("via import", through)):
                for store, evidence in mapping.get(path, {}).items():
                    entry = stores.setdefault(
                        store,
                        {
                            "name": store,
                            "packages": sorted(
                                {
                                    package
                                    for package, label in DATASTORE_PACKAGES.items()
                                    if label == store and package in packages
                                }
                            ),
                            "files": [],
                            "reads": 0,
                            "writes": 0,
                            "via": kind,
                            "evidence_ids": [],
                        },
                    )
                    if path not in entry["files"]:
                        entry["files"].append(path)
                        properties = facts.file_properties.get(path, {})
                        entry["reads"] += int(properties.get("data_reads") or 0)
                        entry["writes"] += int(properties.get("data_writes") or 0)
                    if kind == "direct":
                        entry["via"] = "direct"
                    if len(entry["evidence_ids"]) < 5:
                        entry["evidence_ids"].append(f"evidence:{evidence}")
        for entry in stores.values():
            entry["access"] = (
                "read_write"
                if entry["reads"] and entry["writes"]
                else "read"
                if entry["reads"]
                else "write"
                if entry["writes"]
                else "unknown"
            )
            entry["files"] = entry["files"][:20]
            entry["inferred"] = True
        datastores[name] = sorted(stores.values(), key=lambda item: item["name"])

        found: dict[str, dict[str, Any]] = {}
        for path in sorted(paths):
            for package, evidence in imported_packages.get(path, {}).items():
                label = _integration_name(package)
                if label is None:
                    continue
                entry = found.setdefault(
                    label,
                    {"name": label, "package": package, "host": None, "files": [],
                     "evidence_ids": []},
                )  # fmt: skip
                if path not in entry["files"]:
                    entry["files"].append(path)
                if len(entry["evidence_ids"]) < 5:
                    entry["evidence_ids"].append(f"evidence:{evidence}")
            hosts = facts.file_properties.get(path, {}).get("external_hosts") or []
            for host in hosts:
                if not isinstance(host, str) or host in {"localhost", "127.0.0.1"}:
                    continue
                entry = found.setdefault(
                    host,
                    {"name": host, "package": None, "host": host, "files": [],
                     "evidence_ids": []},
                )  # fmt: skip
                if path not in entry["files"]:
                    entry["files"].append(path)
                if path in facts.file_evidence and len(entry["evidence_ids"]) < 5:
                    entry["evidence_ids"].append(f"evidence:{facts.file_evidence[path]}")
        for entry in found.values():
            entry["files"] = entry["files"][:20]
        integrations[name] = sorted(found.values(), key=lambda item: item["name"])
        roles[name] = _role_for(name, paths, packages, bool(stores))
    return ArchitectureFacts(roles=roles, datastores=datastores, integrations=integrations)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def component_history(facts: SnapshotFacts) -> dict[str, dict[str, Any]]:
    """Contributors, commit and bug-fix counts, and last change per component."""
    commit_by_sha = {commit.sha: commit for commit in facts.commits}
    shas_by_component: dict[str, set[str]] = defaultdict(set)
    for change in facts.file_changes:
        name = facts.component_of.get(change.path)
        if name is not None:
            shas_by_component[name].add(change.commit_sha)
    history: dict[str, dict[str, Any]] = {}
    for name, shas in shas_by_component.items():
        known = [commit_by_sha[sha] for sha in shas if sha in commit_by_sha]
        authors: Counter[str] = Counter(commit.author_email.lower() for commit in known)
        names = {commit.author_email.lower(): commit.author_name for commit in known}
        total = max(1, len(known))
        history[name] = {
            "contributors": [
                {"name": names[email], "commits": count, "share": round(count / total, 4)}
                for email, count in authors.most_common(5)
            ],
            "commits": len(shas),
            "bug_fixes": sum(1 for commit in known if is_fix_message(commit.message)),
            "last_changed": max((_as_utc(commit.authored_at) for commit in known), default=None),
        }
    return history


def module_graph(facts: SnapshotFacts) -> tuple[list[ModuleNode], list[ModuleLink]]:
    links: dict[tuple[str, str], ModuleLink] = {}
    externals: dict[str, Counter[str]] = defaultdict(Counter)
    group_of = facts.component_of
    for source, target, evidence in facts.imports:
        source_module = group_of.get(source)
        if source_module is None:
            continue
        if target.startswith("external:"):
            externals[source_module][target.removeprefix("external:")] += 1
            continue
        target_module = group_of.get(target)
        if target_module is None or target_module == source_module:
            continue
        link = links.setdefault(
            (source_module, target_module), ModuleLink(source_module, target_module, 0, 0, [])
        )
        link.imports += 1
        if len(link.evidence_ids) < 5:
            link.evidence_ids.append(f"evidence:{evidence}")
    for edge in facts.co_changes:
        left, right = group_of.get(edge.left_path), group_of.get(edge.right_path)
        if left is None or right is None or left == right:
            continue
        key = (
            (left, right) if (left, right) in links or (right, left) not in links else (right, left)
        )
        link = links.setdefault(key, ModuleLink(key[0], key[1], 0, 0, []))
        link.co_changes += edge.commit_count
        if len(link.evidence_ids) < 5 and edge.evidence_shas:
            link.evidence_ids.append(f"commit:{edge.evidence_shas[0]}")
    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    for link in links.values():
        if link.imports:
            fan_out[link.source] += 1
            fan_in[link.target] += 1
    members: dict[str, list[str]] = defaultdict(list)
    for path, name in group_of.items():
        members[name].append(path)
    architecture = architecture_facts(facts)
    history = component_history(facts)
    nodes = []
    for name, paths in sorted(members.items()):
        risk, riskiest = group_risk(facts, paths)
        symbols = Counter(symbol for path in paths for symbol in facts.symbols.get(path, []))
        role, role_signal = architecture.roles.get(name, ("unknown", ""))
        past = history.get(name, {})
        nodes.append(
            ModuleNode(
                name=name,
                files=len(paths),
                risk=risk,
                fan_in=fan_in[name],
                fan_out=fan_out[name],
                externals=[package for package, _ in externals[name].most_common(8)],
                inferred=True,
                description=(
                    f"{len(paths)} source files under {name}/"
                    + (
                        "; declares " + ", ".join(sym for sym, _ in symbols.most_common(6))
                        if symbols
                        else ""
                    )
                    + "."
                ),
                riskiest=riskiest,
                paths=sorted(paths)[:200],
                role=role,
                role_signal=role_signal,
                datastores=architecture.datastores.get(name, []),
                integrations=architecture.integrations.get(name, []),
                contributors=past.get("contributors", []),
                commits=past.get("commits", 0),
                bug_fixes=past.get("bug_fixes", 0),
                last_changed=past.get("last_changed"),
            )
        )
    return nodes, sorted(links.values(), key=lambda item: (-item.imports, -item.co_changes))


# ---------------------------------------------------------------- docs


DOCUMENTS = {
    "ARCHITECTURE.md": "System overview, modules, and how they depend on each other",
    "MODULES.md": "Each inferred module: purpose, files, key symbols, dependencies",
    "DATA_FLOW.md": "Entry points and static import flow between files and modules",
    "DEPENDENCIES.md": "External packages and internal module dependencies",
    "BUSINESS_LOGIC.md": "What the project says it does, its features, and domain vocabulary",
    "RISK_REPORT.md": "Health score, risky files and modules, and change hotspots",
}


def _code(value: str) -> str:
    return "`" + value.replace("`", "'") + "`"


def _plain(text: str) -> str:
    return " ".join(text.replace("|", "/").split())


def _header(title: str, facts: SnapshotFacts, generated_at: datetime, sources: str) -> list[str]:
    snapshot = facts.snapshot
    return [
        f"# {title}",
        "",
        f"> Generated by CODE GENOME ({DOCS_VERSION}) for {_code(facts.repository_name)} at "
        f"commit {_code(snapshot.commit_sha)} ({snapshot.analysis_version}), "
        f"{generated_at.isoformat()}. Sources: {sources}. Every statement links to stored "
        "evidence; module boundaries are inferred.",
        "",
    ]


def _readme_intro(facts: SnapshotFacts) -> tuple[str, str] | None:
    readmes = sorted(
        (item for item in facts.chunks if item.kind == "readme"),
        key=lambda item: (item.path.count("/"), len(item.path), item.ordinal),
    )
    for chunk in readmes:
        lines = [
            line.strip()
            for line in chunk.text.splitlines()
            if line.strip()
            and not line.lstrip().startswith(("#", "!", "<", "[!", "|", "```"))
            and not re.fullmatch(r"\s*([-*_]\s*){3,}", line)
        ]
        if lines:
            # README lines are often separate sentences without trailing punctuation.
            text = " ".join(
                line if re.search(r"[.!?:;,]$", line) or index == len(lines) - 1 else f"{line}."
                for index, line in enumerate(lines)
            )
            return text[:700], f"evidence:{chunk.provenance_id}"
    return None


def _entry_points(facts: SnapshotFacts) -> list[str]:
    imported = {target for _, target, _ in facts.internal_imports}
    importers = Counter(source for source, _, _ in facts.internal_imports)
    candidates = [
        path
        for path in facts.source_paths
        if path not in imported and importers[path] > 0 and not _TEST_PATH.search(path)
    ]
    return sorted(candidates, key=lambda path: (-importers[path], path))[:8]


_ROLE_TITLES = {
    "api": "API and entry layer",
    "service": "Services and domain logic",
    "data": "Data access",
    "contract": "Smart contracts",
    "ui": "User interface",
    "util": "Shared utilities",
    "config": "Configuration",
    "infra/scripts": "Infrastructure and scripts",
    "test": "Tests",
    "unknown": "Unclassified",
}


def _architecture_by_role(nodes: list[ModuleNode]) -> list[str]:
    lines = [
        "## Architecture by role",
        "",
        f"Roles are assigned deterministically ({ROLES_VERSION}) from directory names and "
        "imports; each entry shows the signal used. Data stores and integrations are detected "
        "from static imports and literal URLs only.",
        "",
    ]
    by_role: dict[str, list[ModuleNode]] = defaultdict(list)
    for node in nodes:
        by_role[node.role].append(node)
    for role in ROLE_ORDER:
        members = sorted(by_role.get(role, []), key=lambda item: -item.files)
        if not members:
            continue
        lines += [f"### {_ROLE_TITLES[role]}", ""]
        lines += [
            f"- {_code(node.name)} ({node.files} files; {node.role_signal})" for node in members
        ]
        lines.append("")
    stores: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    services: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for node in nodes:
        for store in node.datastores:
            stores[store["name"]].append((node.name, store))
        for item in node.integrations:
            services[item["name"]].append((node.name, item))
    lines += ["### Data stores (inferred from imports)", ""]
    lines += [
        f"- **{_plain(name)}** used by "
        + ", ".join(
            f"{_code(component)} ({entry['access'].replace('_', '/')}, {entry['via']}; "
            f"{', '.join(entry['evidence_ids'][:2])})"
            for component, entry in users
        )
        for name, users in sorted(stores.items())
    ] or ["- No data-store client imports were detected."]
    lines += ["", "### External integrations (inferred from imports and literal URLs)", ""]
    lines += [
        f"- **{_plain(name)}** used by "
        + ", ".join(
            f"{_code(component)} ({', '.join(entry['evidence_ids'][:2]) or 'no evidence id'})"
            for component, entry in users
        )
        for name, users in sorted(services.items())
    ] or ["- No external integrations were detected."]
    lines.append("")
    return lines


def _architecture(
    facts: SnapshotFacts, nodes: list[ModuleNode], links: list[ModuleLink]
) -> list[str]:
    lines = ["## What it is", ""]
    intro = _readme_intro(facts)
    lines += [f"{intro[0]} ({intro[1]})" if intro else "No README description was found.", ""]
    lines += [
        "## At a glance",
        "",
        f"- {len(facts.paths)} files, {len(facts.source_paths)} JS/TS source files",
        f"- {len(facts.modules)} inferred modules, {len(facts.internal_imports)} internal imports",
        f"- {len({c.author_email.lower() for c in facts.commits})} contributors across "
        f"{len(facts.commits)} analysed commits",
        "",
        *_architecture_by_role(nodes),
        "## Modules",
        "",
        "| Module | Files | Depends on | Used by | Risk |",
        "|---|---|---|---|---|",
    ]
    for node in sorted(nodes, key=lambda item: -item.files):
        risk = f"{node.risk:.0%}" if node.risk is not None else "—"
        lines.append(
            f"| {_code(node.name)} | {node.files} | {node.fan_out} | {node.fan_in} | {risk} |"
        )
    lines += ["", "## Module dependencies", ""]
    lines += [
        f"- {_code(link.source)} → {_code(link.target)}: {link.imports} imports"
        + (f", co-changed {link.co_changes}×" if link.co_changes else "")
        + f" ({', '.join(link.evidence_ids[:2])})"
        for link in links[:30]
    ] or ["- No cross-module imports were observed."]
    lines += ["", "## Entry points", ""]
    lines += [
        f"- {_code(path)} imports {sum(1 for s, _, _ in facts.internal_imports if s == path)} "
        "files and is imported by none"
        + (f" (evidence:{facts.file_evidence[path]})" if path in facts.file_evidence else "")
        for path in _entry_points(facts)
    ] or ["- No entry points were detected from imports."]
    return lines


def _modules_doc(
    facts: SnapshotFacts, nodes: list[ModuleNode], links: list[ModuleLink]
) -> list[str]:
    members: dict[str, list[str]] = defaultdict(list)
    for path, name in facts.component_of.items():
        members[name].append(path)
    lines = [
        "Components group source files by directory; inferred modules (end of document) come "
        "from directory structure and co-change history.",
        "",
    ]
    for node in sorted(nodes, key=lambda item: -item.files):
        paths = sorted(members[node.name])
        uses = [link.target for link in links if link.source == node.name and link.imports]
        used_by = [link.source for link in links if link.target == node.name and link.imports]
        symbols = Counter(name for path in paths for name in facts.symbols.get(path, []))
        lines += [
            f"## {_code(node.name)}",
            "",
            f"- Files ({node.files}): "
            + ", ".join(
                _code(path)
                + (
                    f" (evidence:{facts.file_evidence[path]})"
                    if path in facts.file_evidence
                    else ""
                )
                for path in paths[:20]
            )
            + (" …" if node.files > 20 else ""),
            "- Key symbols: "
            + (", ".join(_code(name) for name, _ in symbols.most_common(15)) or "none"),
            "- Depends on: " + (", ".join(_code(name) for name in uses) or "no other component"),
            "- Used by: " + (", ".join(_code(name) for name in used_by) or "no other component"),
            "- External packages: " + (", ".join(_code(name) for name in node.externals) or "none"),
            "- Riskiest files: "
            + (
                ", ".join(f"{_code(path)} ({facts.risk[path].score:.0%})" for path in node.riskiest)
                or "no risk scores"
            ),
            "",
        ]
    if not nodes:
        lines.append("No JS/TS source components were found.")
    lines += ["## Inferred modules", ""]
    lines += [
        f"- {_code(module.natural_key)} ({len(module.file_paths)} files, confidence "
        f"{module.confidence:.2f}): {_plain(module.description)}"
        + (f" (commit:{module.evidence_shas[0]})" if module.evidence_shas else "")
        for module in facts.modules
    ] or ["- No modules were inferred."]
    return lines


def _data_flow(facts: SnapshotFacts, nodes: list[ModuleNode], links: list[ModuleLink]) -> list[str]:
    lines = [
        "This document follows static imports only. Runtime data flow (network calls, events, "
        "database reads) is not traced; data stores and external services are recognised from "
        "the client libraries a component imports.",
        "",
        "## Module flow",
        "",
    ]
    lines += [
        f"- {_code(link.source)} → {_code(link.target)} ({link.imports} imports)"
        for link in links
        if link.imports
    ][:30] or ["- No cross-module imports were observed."]
    graph: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for source, target, evidence in facts.internal_imports:
        graph[source].append((target, evidence))
    lines += ["", "## From each entry point", ""]
    for entry in _entry_points(facts)[:5]:
        lines.append(f"### {_code(entry)}")
        lines.append("")
        seen = {entry}
        frontier = [(entry, 0)]
        while frontier:
            current, depth = frontier.pop(0)
            if depth >= 3:
                continue
            for target, evidence in sorted(graph.get(current, []))[:8]:
                if target in seen:
                    continue
                seen.add(target)
                lines.append(f"{'  ' * depth}- {_code(target)} (evidence:{evidence})")
                frontier.append((target, depth + 1))
        lines.append("")
    if not _entry_points(facts):
        lines.append("No entry points were detected from imports.")
    lines += ["", *_sink_flows(nodes, links)]
    return lines


def _sink_flows(nodes: list[ModuleNode], links: list[ModuleLink]) -> list[str]:
    """Static paths from API/UI components to components that reach a store or integration."""
    lines = [
        "## Flows into data stores and integrations",
        "",
        "Inferred: each line follows static imports between components from an API or UI "
        "component to a component that imports a data-store client or external service. "
        "It shows a possible path, not an observed request.",
        "",
    ]
    graph = nx.DiGraph()
    graph.add_nodes_from(node.name for node in nodes)
    graph.add_edges_from((link.source, link.target) for link in links if link.imports)
    roles = {node.name: node.role for node in nodes}
    sources = [node.name for node in nodes if node.role in {"api", "ui"}]
    flows: list[str] = []
    for sink in sorted(nodes, key=lambda item: item.name):
        targets = [
            *(
                f"{store['name']} ({store['access'].replace('_', '/')})"
                for store in sink.datastores
            ),
            *(item["name"] for item in sink.integrations),
        ]
        if not targets:
            continue
        evidence = [*(e for s in sink.datastores for e in s["evidence_ids"][:1])][:1] + [
            *(e for s in sink.integrations for e in s["evidence_ids"][:1])
        ][:1]
        starts = [name for name in sources if name != sink.name] or [sink.name]
        paths = []
        for start in starts:
            if start == sink.name:
                paths.append([sink.name])
                continue
            try:
                paths.append(nx.shortest_path(graph, start, sink.name))
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
        if not paths:
            paths = [[sink.name]]
        for path in sorted(paths, key=len)[:3]:
            chain = " → ".join(f"{_code(name)} [{roles.get(name, 'unknown')}]" for name in path)
            flows.append(
                f"- {chain} → {', '.join(_plain(item) for item in targets)}"
                + (f" ({', '.join(evidence)})" if evidence else "")
            )
    lines += flows[:20] or ["- No data-store or integration imports were detected."]
    return lines


def _dependencies(facts: SnapshotFacts, links: list[ModuleLink]) -> list[str]:
    usage: dict[str, set[str]] = defaultdict(set)
    evidence: dict[str, str] = {}
    for source, target, provenance in facts.external_imports:
        name = target.removeprefix("external:")
        package = "/".join(name.split("/")[:2]) if name.startswith("@") else name.split("/")[0]
        usage[package].add(source)
        evidence.setdefault(package, provenance)
    lines = [
        "## External packages (by importing files)",
        "",
        "| Package | Files | Evidence |",
        "|---|---|---|",
    ]
    lines += [
        f"| {_code(name)} | {len(files)} | evidence:{evidence[name]} |"
        for name, files in sorted(usage.items(), key=lambda item: (-len(item[1]), item[0]))[:60]
    ]
    manifests = [item for item in facts.chunks if PurePosixPath(item.path).name == "package.json"]
    if manifests:
        lines += ["", "## Declared in manifests", ""]
        lines += [
            f"- {_code(item.path)}: {_plain(item.text)[:600]} (evidence:{item.provenance_id})"
            for item in manifests[:6]
        ]
    lines += ["", "## Internal module dependencies", ""]
    lines += [
        f"- {_code(link.source)} depends on {_code(link.target)} ({link.imports} imports)"
        for link in links
        if link.imports
    ][:40] or ["- No cross-module imports were observed."]
    return lines


_GENERIC_WORDS = {
    "get", "set", "use", "create", "update", "delete", "handle", "on", "is", "has", "to",
    "from", "with", "for", "the", "of", "and", "init", "props", "type", "types", "default",
    "data", "value", "item", "items", "list", "new", "make", "build", "render", "test",
    "options", "config", "error", "request", "response", "state", "context", "provider",
}  # fmt: skip


def _business(facts: SnapshotFacts, nodes: list[ModuleNode]) -> list[str]:
    lines = [
        "Assembled from the project's own README and docs plus code vocabulary. It records "
        "what the project says it does; it does not verify that the code does it.",
        "",
        "## Purpose",
        "",
    ]
    intro = _readme_intro(facts)
    lines += [f"{intro[0]} ({intro[1]})" if intro else "No README description was found.", ""]
    lines += ["## Documented features and topics", ""]
    sections = [
        item
        for item in facts.chunks
        if item.kind in {"readme", "doc"} and item.heading and item.path.count("/") <= 1
    ]
    for item in sections[:40]:
        body = " ".join(
            line.strip()
            for line in item.text.splitlines()[1:]
            if line.strip() and not line.strip().startswith(("```", "|", "<", "!"))
        )
        lines.append(
            f"- **{_plain(item.heading)}** ({_code(item.path)} lines {item.start_line}-"
            f"{item.end_line}, evidence:{item.provenance_id}): {_plain(body)[:240]}"
        )
    if not sections:
        lines.append("- No documented sections were found.")
    words: Counter[str] = Counter()
    for names in facts.symbols.values():
        for name in names:
            for word in re.findall(r"[A-Z]?[a-z]{3,}", name):
                lowered = word.lower()
                if lowered not in _GENERIC_WORDS:
                    words[lowered] += 1
    lines += [
        "",
        "## Domain vocabulary (most frequent words in declared symbol names)",
        "",
        ", ".join(f"{word} ({count})" for word, count in words.most_common(30)) or "none",
        "",
        "## Where the logic lives (inferred modules)",
        "",
    ]
    lines += [f"- {_code(node.name)}: {_plain(node.description)}" for node in nodes] or ["- none"]
    return lines


def _risk_report(
    facts: SnapshotFacts,
    nodes: list[ModuleNode],
    health_result: tuple[int, str, list[HealthComponent]],
) -> list[str]:
    score, band, components = health_result
    lines = [
        f"## Health score: {score}/100 ({band})",
        "",
        f"Heuristic ({HEALTH_VERSION}); a prompt for review, not a quality guarantee.",
        "",
        "| Component | Score | Input |",
        "|---|---|---|",
    ]
    lines += [
        f"| {item.label} | {item.score:g}/{item.maximum:g} | {item.value} |" for item in components
    ]
    lines += [
        "",
        f"## Riskiest files ({facts.risk_model})",
        "",
        "| File | Risk | Why | Evidence |",
        "|---|---|---|---|",
    ]
    ranked = sorted(facts.risk.items(), key=lambda item: -item[1].score)[:25]
    lines += [
        f"| {_code(path)} | {fact.score:.0%} | {_plain(fact.rationale)[:160]} | "
        f"{', '.join(fact.evidence_ids[:2]) or 'none'} |"
        for path, fact in ranked
    ] or ["No risk scores are available."]
    lines += ["", "## Riskiest modules", ""]
    lines += [
        f"- {_code(node.name)}: {node.risk:.0%} (mean of its riskiest files: "
        + ", ".join(_code(path) for path in node.riskiest)
        + ")"
        for node in sorted(nodes, key=lambda item: -(item.risk or 0))
        if node.risk is not None
    ][:10] or ["- No module risk could be computed."]
    if facts.instability:
        lines += [
            "",
            f"## Components likely to become unstable next period ({facts.instability_model})",
            "",
            "Inferred forecast: probability that a bug-fix commit touches the component in the "
            "next period, from its recent activity sequence. A prompt for review, not a finding.",
            "",
        ]
        lines += [
            f"- {_code(item.name)}: {item.probability:.0%} ({item.band}; {item.files} files"
            + ("; fixed in the latest period" if item.fixed_last_period else "")
            + f"; evidence {', '.join(item.evidence_ids[:2]) or 'none'})"
            for item in sorted(facts.instability, key=lambda entry: -entry.probability)[:8]
        ]
    lines += ["", "## Change hotspots", ""]
    lines += [
        f"- {_code(item.path)}: {item.commit_count} commits, churn {item.churn} "
        f"(commit:{item.evidence_shas[0] if item.evidence_shas else 'none'})"
        for item in facts.hotspots[:15]
    ] or ["- No hotspots were recorded."]
    return lines


def generate_documents(facts: SnapshotFacts, generated_at: datetime) -> dict[str, str]:
    nodes, links = module_graph(facts)
    health_result = health(facts)
    sources = "file manifest, import graph, inferred modules, commit history"
    bodies = {
        "ARCHITECTURE.md": (
            "Architecture",
            sources + ", README",
            _architecture(facts, nodes, links),
        ),
        "MODULES.md": ("Modules", sources, _modules_doc(facts, nodes, links)),
        "DATA_FLOW.md": (
            "Data flow",
            "import graph, data-store and integration imports",
            _data_flow(facts, nodes, links),
        ),
        "DEPENDENCIES.md": (
            "Dependencies",
            "import graph, package manifests",
            _dependencies(facts, links),
        ),
        "BUSINESS_LOGIC.md": (
            "Business logic",
            "README and docs, declared symbols, inferred modules",
            _business(facts, nodes),
        ),
        "RISK_REPORT.md": (
            "Risk report",
            "risk model, hotspots, import graph, commit history",
            _risk_report(facts, nodes, health_result),
        ),
    }
    return {
        name: "\n".join([*_header(title, facts, generated_at, source), *body]).rstrip() + "\n"
        for name, (title, source, body) in bodies.items()
    }
