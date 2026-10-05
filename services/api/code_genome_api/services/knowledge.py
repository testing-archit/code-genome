"""Deterministic project knowledge: cited text chunks of docs, manifests, and source.

Chunks are verbatim excerpts with a path and line range at a pinned commit. They let Q&A and
the voice agent answer "what does this project do?" from the repository's own README and
docs, and "how does X work?" from the code itself. Nothing here is model-generated.
"""

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

KNOWLEDGE_VERSION = "knowledge@0.1.0"
ChunkKind = Literal["readme", "doc", "manifest", "source"]

MAX_FILE_BYTES = 400_000
MAX_TOTAL_BYTES = 20_000_000
MAX_FILES = 3_000
MAX_CHUNKS = 8_000
CHUNK_CHARS = 1_100
SOURCE_WINDOW_LINES = 60

DOC_SUFFIXES = {".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc"}
MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "gemfile",
    "composer.json",
    "dockerfile",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "hardhat.config.js",
    "hardhat.config.ts",
    "foundry.toml",
    "vite.config.ts",
    "next.config.js",
    "next.config.ts",
    "tsconfig.json",
}
SOURCE_SUFFIXES = {
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".py", ".sol", ".go",
    ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".swift", ".c", ".h", ".cpp", ".hpp",
    ".sh", ".sql", ".graphql", ".proto", ".vue", ".svelte", ".css", ".scss", ".html",
    ".yml", ".yaml", ".toml",
}  # fmt: skip
SKIPPED_DIRECTORIES = {
    "node_modules", "dist", "build", "out", "coverage", "vendor", ".next", ".git",
    "__pycache__", ".venv", "venv", "target", "artifacts", "cache", "typechain-types",
}  # fmt: skip
SKIPPED_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
    "cargo.lock", "composer.lock", "gemfile.lock", "bun.lockb",
}  # fmt: skip
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|private[_-]?key|mnemonic)"
    r"(\s*[:=]\s*)(['\"]?)[^\s'\"]{8,}\3"
)


@dataclass(frozen=True)
class KnowledgeChunk:
    path: str
    kind: ChunkKind
    ordinal: int
    heading: str
    start_line: int
    end_line: int
    text: str


def classify(path: str) -> ChunkKind | None:
    """Return the chunk kind for a repository path, or None if it is not worth reading."""
    pure = PurePosixPath(path)
    lowered = [part.lower() for part in pure.parts]
    name = lowered[-1]
    if any(part in SKIPPED_DIRECTORIES for part in lowered[:-1]) or name in SKIPPED_NAMES:
        return None
    if name.endswith((".min.js", ".min.css", ".map", ".snap")):
        return None
    if name.startswith("readme"):
        return "readme"
    if name in MANIFEST_NAMES:
        return "manifest"
    suffix = pure.suffix.lower()
    if suffix in DOC_SUFFIXES or name in {"license", "contributing", "changelog"}:
        return "doc"
    if suffix in SOURCE_SUFFIXES:
        return "source"
    return None


def priority(path: str, kind: ChunkKind) -> tuple[int, int, str]:
    """Read root READMEs and manifests first so budgets never drop them."""
    order = {"readme": 0, "manifest": 1, "doc": 2, "source": 3}[kind]
    return (order, path.count("/"), path)


def redact(text: str) -> str:
    """Mask obvious credential assignments before text is stored or sent to a model."""
    return _SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)


def decode(content: bytes) -> str | None:
    if b"\0" in content[:8_000]:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _windows(lines: list[str], start: int, heading: str) -> list[tuple[str, int, int, str]]:
    """Split a block into pieces of at most CHUNK_CHARS, keeping line numbers."""
    pieces: list[tuple[str, int, int, str]] = []
    buffer: list[str] = []
    first = start
    size = 0
    for offset, line in enumerate(lines):
        number = start + offset
        if buffer and size + len(line) + 1 > CHUNK_CHARS:
            pieces.append((heading, first, number - 1, "\n".join(buffer)))
            buffer, size, first = [], 0, number
        buffer.append(line[:CHUNK_CHARS])
        size += len(line) + 1
    if buffer and "".join(buffer).strip():
        pieces.append((heading, first, start + len(lines) - 1, "\n".join(buffer)))
    return pieces


def _markdown(text: str) -> list[tuple[str, int, int, str]]:
    lines = text.splitlines()
    sections: list[tuple[str, int, list[str]]] = []
    heading, start = "", 1
    current: list[str] = []
    in_fence = False
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            if current:
                sections.append((heading, start, current))
            heading, start, current = match.group(2)[:200], number, [line]
        else:
            current.append(line)
    if current:
        sections.append((heading, start, current))
    pieces: list[tuple[str, int, int, str]] = []
    for title, first, block in sections:
        pieces.extend(_windows(block, first, title))
    return pieces


def _package_json(text: str, path: str) -> list[tuple[str, int, int, str]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _windows(text.splitlines(), 1, path)
    if not isinstance(data, dict):
        return []
    parts = []
    for key in ("name", "description", "version", "license", "type", "main"):
        if isinstance(data.get(key), str):
            parts.append(f"{key}: {data[key]}")
    if isinstance(data.get("keywords"), list):
        parts.append("keywords: " + ", ".join(str(item) for item in data["keywords"][:20]))
    for key in ("scripts", "dependencies", "devDependencies", "peerDependencies"):
        value = data.get(key)
        if isinstance(value, dict) and value:
            names = list(value)[:40]
            if key == "scripts":
                rendered = "; ".join(f"{name} = {str(value[name])[:120]}" for name in names)
            else:
                rendered = ", ".join(names)
            parts.append(f"{key}: {rendered}")
    summary = f"Package manifest {path}. " + ". ".join(parts)
    line_count = max(1, len(text.splitlines()))
    return [("package.json", 1, line_count, summary[: CHUNK_CHARS * 2])]


def chunk_file(path: str, kind: ChunkKind, content: bytes) -> list[KnowledgeChunk]:
    text = decode(content)
    if text is None or not text.strip():
        return []
    text = redact(text)
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if kind in {"readme", "doc"} and pure.suffix.lower() in {".md", ".mdx", ".markdown"}:
        pieces = _markdown(text)
    elif name == "package.json" or name == "composer.json":
        pieces = _package_json(text, path)
    else:
        lines = text.splitlines()
        pieces = []
        for start in range(0, len(lines), SOURCE_WINDOW_LINES):
            pieces.extend(_windows(lines[start : start + SOURCE_WINDOW_LINES], start + 1, ""))
    return [
        KnowledgeChunk(path, kind, ordinal, heading, first, last, body.strip())
        for ordinal, (heading, first, last, body) in enumerate(pieces)
        if body.strip()
    ]


def document_text(chunk_path: str, kind: str, heading: str, start: int, end: int, text: str) -> str:
    """How a chunk reads as retrieval evidence: where it is, then the verbatim excerpt."""
    label = {"readme": "README", "doc": "Documentation", "manifest": "Manifest"}.get(kind, "Code")
    section = f" section '{heading}'" if heading else ""
    return f"{label} {chunk_path}{section} (lines {start}-{end}): {text}"
