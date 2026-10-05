"""Deterministic Python facts from Tree-sitter (``tree-sitter-python@0.1.0``).

Same fact types as the JS/TS analyzer, so the genome builder treats both alike:

* imports: ``import a.b``, ``from x import y``, and relative ``from ..pkg import y`` (the module
  keeps its leading dots, which the builder resolves against the importing package);
* symbols: classes, functions, and methods; a name is "exported" when it does not start with
  ``_`` (Python's convention), and public top-level definitions are listed as exports;
* calls: ``f()`` and ``obj.f()``, with literal URL hosts for common HTTP clients;
* metrics: code lines, a cyclomatic estimate (if/elif, loops, except, conditional expressions,
  ``and``/``or``, match cases, comprehension filters), and function count (incl. lambdas).

Source is parsed, never executed.
"""

import hashlib
import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Literal

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from .types import (
    CallFact,
    Diagnostic,
    ExportFact,
    FileAnalysis,
    FileMetrics,
    ImportFact,
    SourceSpan,
    SymbolFact,
)

PYTHON_ANALYZER_VERSION = "tree-sitter-python@0.1.0"
PYTHON_SUFFIXES = {".py", ".pyi"}
_LANGUAGE = Language(tree_sitter_python.language())
DECISION_NODES = {
    "if_statement",
    "elif_clause",
    "for_statement",
    "while_statement",
    "except_clause",
    "conditional_expression",
    "boolean_operator",
    "case_clause",
    "if_clause",
}
FUNCTION_NODES = {"function_definition", "lambda"}
HTTP_CLIENTS = {"requests", "httpx", "aiohttp", "urllib3", "session", "client"}
MAX_CALLS_PER_FILE = 2_000
_URL_HOST = re.compile(r"^https?://([A-Za-z0-9.-]{1,253})(?::\d{1,5})?(?:[/?#]|$)")


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _span(node: Node) -> SourceSpan:
    return SourceSpan(
        node.start_point.row + 1,
        node.start_point.column + 1,
        node.end_point.row + 1,
        node.end_point.column + 1,
    )


def _imported_names(node: Node, source: bytes) -> tuple[str, ...]:
    names: list[str] = []
    for child in node.children_by_field_name("name"):
        target = child.child_by_field_name("name") if child.type == "aliased_import" else child
        if target is not None:
            names.append(_text(target, source))
    if any(child.type == "wildcard_import" for child in node.children):
        names.append("*")
    return tuple(names)


def _extract_imports(root: Node, source: bytes) -> tuple[ImportFact, ...]:
    imports: list[ImportFact] = []
    for node in _walk(root):
        if node.type == "import_statement":
            for name in _imported_names(node, source):
                imports.append(
                    ImportFact(module=name, names=(name,), kind="python", span=_span(node))
                )
        elif node.type == "import_from_statement":
            module = node.child_by_field_name("module_name")
            if module is None:
                continue
            imports.append(
                ImportFact(
                    module=_text(module, source).replace(" ", ""),
                    names=_imported_names(node, source),
                    kind="python",
                    span=_span(node),
                )
            )
    return tuple(sorted(imports, key=lambda item: (item.span, item.module)))


def _definitions(root: Node) -> Iterator[tuple[Node, bool]]:
    """(class/function node, defined directly inside a class)."""
    stack: list[tuple[Node, bool]] = [(child, False) for child in reversed(root.children)]
    while stack:
        node, in_class = stack.pop()
        if node.type == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is not None:
                stack.append((inner, in_class))
            continue
        if node.type in {"function_definition", "class_definition"}:
            yield node, in_class
            body = node.child_by_field_name("body")
            if body is not None:
                is_class = node.type == "class_definition"
                stack.extend((child, is_class) for child in reversed(body.children))
            continue
        if node.type in {"if_statement", "try_statement", "block", "else_clause", "with_statement"}:
            stack.extend((child, in_class) for child in reversed(node.children))


def _extract_symbols(root: Node, source: bytes) -> tuple[SymbolFact, ...]:
    symbols: list[SymbolFact] = []
    for node, in_class in _definitions(root):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        kind: Literal["function", "class", "method"] = (
            "class" if node.type == "class_definition" else "method" if in_class else "function"
        )
        symbols.append(
            SymbolFact(name=name, kind=kind, exported=not name.startswith("_"), span=_span(node))
        )
    return tuple(sorted(symbols, key=lambda item: (item.span, item.name)))


def _extract_exports(root: Node, source: bytes) -> tuple[ExportFact, ...]:
    exports: list[ExportFact] = []
    for child in root.children:
        node = (
            child.child_by_field_name("definition")
            if child.type == "decorated_definition"
            else child
        )
        if node is None or node.type not in {"function_definition", "class_definition"}:
            continue
        name_node = node.child_by_field_name("name")
        if name_node is not None and not _text(name_node, source).startswith("_"):
            name = _text(name_node, source)
            exports.append(
                ExportFact(name=name, local_name=name, is_default=False, span=_span(node))
            )
    return tuple(sorted(exports, key=lambda item: (item.span, item.name)))


def _extract_calls(root: Node, source: bytes) -> tuple[CallFact, ...]:
    calls: list[CallFact] = []
    for node in _walk(root):
        if len(calls) >= MAX_CALLS_PER_FILE:
            break
        if node.type != "call":
            continue
        target = node.child_by_field_name("function")
        if target is None:
            continue
        receiver: str | None = None
        if target.type == "identifier":
            callee = _text(target, source)
        elif target.type == "attribute":
            obj = target.child_by_field_name("object")
            attribute = target.child_by_field_name("attribute")
            if obj is None or attribute is None:
                continue
            callee = _text(attribute, source)
            receiver_text = _text(obj, source)
            receiver = (
                receiver_text
                if obj.type in {"identifier", "attribute"} and len(receiver_text) <= 120
                else "<expression>"
            )
        else:
            continue
        host = None
        if receiver is not None and receiver.split(".")[-1].lower() in HTTP_CLIENTS:
            arguments = node.child_by_field_name("arguments")
            first = arguments.named_children[0] if arguments and arguments.named_children else None
            if first is not None and first.type == "string":
                match = _URL_HOST.match(_text(first, source).strip("\"'rbfu"))
                host = match.group(1).lower() if match else None
        calls.append(CallFact(callee=callee, receiver=receiver, span=_span(node), url_host=host))
    return tuple(sorted(calls, key=lambda item: (item.span, item.callee)))


def _extract_diagnostics(root: Node) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    for node in _walk(root):
        if node.is_error:
            diagnostics.append(
                Diagnostic(
                    "PARSE_ERROR", "Tree-sitter could not parse this source range.", _span(node)
                )
            )
        elif node.is_missing:
            diagnostics.append(
                Diagnostic(
                    "MISSING_SYNTAX", f"Tree-sitter expected a {node.type} token.", _span(node)
                )
            )
    return tuple(sorted(diagnostics, key=lambda item: (item.span, item.code)))


def _compute_metrics(root: Node) -> FileMetrics:
    code_lines: set[int] = set()
    functions = 0
    decisions = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "comment":
            continue
        if node.child_count == 0:
            if node.end_byte > node.start_byte and not node.is_missing:
                code_lines.update(range(node.start_point.row, node.end_point.row + 1))
            continue
        if node.type in FUNCTION_NODES:
            functions += 1
        elif node.type in DECISION_NODES:
            decisions += 1
        stack.extend(node.children)
    return FileMetrics(loc=len(code_lines), complexity=1 + decisions, functions=functions)


def analyze_python(path: str, source: bytes | str) -> FileAnalysis:
    """Extract deterministic Python facts without executing repository source."""
    normalized_path = str(PurePosixPath(path))
    if normalized_path.startswith("../") or normalized_path.startswith("/"):
        raise ValueError("Source path must stay within the repository")
    if PurePosixPath(normalized_path).suffix.lower() not in PYTHON_SUFFIXES:
        raise ValueError("Not a Python source file")
    source_bytes = source.encode() if isinstance(source, str) else source
    root = Parser(_LANGUAGE).parse(source_bytes).root_node
    return FileAnalysis(
        path=normalized_path,
        language="python",
        content_sha256=hashlib.sha256(source_bytes).hexdigest(),
        analyzer_version=PYTHON_ANALYZER_VERSION,
        imports=_extract_imports(root, source_bytes),
        exports=_extract_exports(root, source_bytes),
        symbols=_extract_symbols(root, source_bytes),
        diagnostics=_extract_diagnostics(root),
        calls=_extract_calls(root, source_bytes),
        metrics=_compute_metrics(root),
    )
