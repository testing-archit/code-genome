"""Code Genome's own documentation as citable knowledge for the workspace assistant.

The assistant answers "what is a bus factor?", "how does SZZ work?" or "how do I check a pull
request?" from the project's Markdown docs, split into sections. Every answer cites the sections
it used (``doc:<file>#<section>``). Repository facts never come from here; those go through each
repository's own evidence pipeline.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

from code_genome_intelligence import RetrievalDocument
from code_genome_ml import HybridRetriever, SearchDocument

from ..config import get_settings

PRODUCT_KNOWLEDGE_VERSION = "product-docs@1"
DOC_FILES = (
    "README.md",
    "PRD.md",
    "ARCHITECTURE.md",
    "ML_SPEC.md",
    "API_SPEC.md",
    "DATA_MODEL.md",
    "DELIVERY_AUDITOR.md",
    "SECURITY.md",
    "TESTING_STRATEGY.md",
    "PILOT.md",
    "docs/benchmarks/defect-cross-project.md",
)
MAX_SECTION_CHARS = 1_800
_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class DocSection:
    id: str
    path: str
    title: str
    text: str


def default_root() -> Path:
    """Where the docs live: CODE_GENOME_PRODUCT_DOCS_DIR, else the working directory (the
    repository root locally, /app in the container), else the source checkout's root."""
    configured = get_settings().product_docs_dir
    candidates = [Path(configured)] if configured else []
    candidates += [Path.cwd(), Path(__file__).resolve().parents[4]]
    for candidate in candidates:
        if (candidate / "README.md").is_file():
            return candidate
    return candidates[0]


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "section"


def split_sections(path: str, markdown: str) -> list[DocSection]:
    """Heading-delimited sections; long ones are cut into parts so excerpts stay focused."""
    sections: list[DocSection] = []
    matches = list(_HEADING.finditer(markdown))
    starts = [(0, PurePosixPath(path).stem)] + [
        (match.start(), match.group(2)) for match in matches
    ]
    seen: dict[str, int] = {}
    for index, (start, title) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(markdown)
        body = markdown[start:end].strip()
        if not body or (index == 0 and matches and matches[0].start() == 0):
            continue
        slug = _slug(title)
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        for part, offset in enumerate(range(0, len(body), MAX_SECTION_CHARS)):
            suffix = f"-{part + 1}" if len(body) > MAX_SECTION_CHARS else ""
            sections.append(
                DocSection(
                    id=f"doc:{path}#{slug}{suffix}",
                    path=path,
                    title=title,
                    text=body[offset : offset + MAX_SECTION_CHARS],
                )
            )
    return sections


@lru_cache(maxsize=4)
def load_sections(root: str | None = None) -> tuple[DocSection, ...]:
    base = Path(root) if root else default_root()
    sections: list[DocSection] = []
    for name in DOC_FILES:
        file = base / name
        if file.is_file() and file.stat().st_size <= 2_000_000:
            sections.extend(
                split_sections(name, file.read_text(encoding="utf-8", errors="replace"))
            )
    return tuple(sections)


def documents(sections: tuple[DocSection, ...]) -> tuple[RetrievalDocument, ...]:
    return tuple(
        RetrievalDocument(section.id, "doc", f"{section.path} / {section.title}: {section.text}")
        for section in sections
    )


@lru_cache(maxsize=4)
def _retriever(root: str | None = None) -> HybridRetriever | None:
    sections = load_sections(root)
    if not sections:
        return None
    return HybridRetriever(
        [
            SearchDocument(id=item.id, kind="doc", text=item.text, title=item.title, path=item.path)
            for item in sections
        ]
    )


def search(question: str, *, limit: int = 4, root: str | None = None) -> list[DocSection]:
    """The most relevant documentation sections for a question (hybrid BM25 + LSA)."""
    retriever = _retriever(root)
    if retriever is None:
        return []
    by_id = {section.id: section for section in load_sections(root)}
    hits = retriever.search(question, limit=limit)
    return [by_id[hit.document.id] for hit in hits if hit.score > 0 and hit.document.id in by_id]
