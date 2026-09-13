import hashlib
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Literal

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from .types import Diagnostic, ExportFact, FileAnalysis, ImportFact, SourceSpan, SymbolFact

ANALYZER_VERSION = "tree-sitter-js-ts@0.1.0"

LanguageName = Literal["javascript", "typescript", "tsx"]
SYMBOL_TYPES: dict[str, Literal["function", "class", "method", "interface", "type", "enum"]] = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "method_definition": "method",
    "method_signature": "method",
    "abstract_method_signature": "method",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}
FUNCTION_VALUES = {"arrow_function", "function_expression", "generator_function"}


def _language_for_path(path: str) -> tuple[LanguageName, Language]:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".js", ".jsx", ".mjs", ".cjs"}:
        return "javascript", Language(tree_sitter_javascript.language())
    if suffix == ".tsx":
        return "tsx", Language(tree_sitter_typescript.language_tsx())
    if suffix in {".ts", ".mts", ".cts"}:
        return "typescript", Language(tree_sitter_typescript.language_typescript())
    raise ValueError(f"Unsupported JavaScript/TypeScript file extension: {suffix or '<none>'}")


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _unquote(node: Node, source: bytes) -> str:
    value = _text(node, source)
    if len(value) >= 2 and value[0] in {'"', "'", "`"} and value[-1] == value[0]:
        return value[1:-1]
    return value


def _span(node: Node) -> SourceSpan:
    return SourceSpan(
        start_line=node.start_point.row + 1,
        start_column=node.start_point.column,
        end_line=node.end_point.row + 1,
        end_column=node.end_point.column,
    )


def _identifier(node: Node, source: bytes) -> str | None:
    name = node.child_by_field_name("name")
    if name is not None:
        return _text(name, source)
    return None


def _is_exported(node: Node) -> bool:
    parent = node.parent
    while parent is not None and parent.type in {
        "lexical_declaration",
        "variable_declaration",
        "variable_declarator",
    }:
        parent = parent.parent
    return parent is not None and parent.type == "export_statement"


def _import_names(node: Node, source: bytes) -> tuple[str, ...]:
    names: list[str] = []
    clause = next((child for child in node.named_children if child.type == "import_clause"), None)
    if clause is None:
        return ()
    for child in _walk(clause):
        if child.type == "identifier" and child.parent and child.parent.type == "import_clause":
            names.append(_text(child, source))
        elif child.type == "namespace_import":
            identifier = next(
                (item for item in child.named_children if item.type == "identifier"), None
            )
            if identifier:
                names.append(f"* as {_text(identifier, source)}")
        elif child.type == "import_specifier":
            original = child.child_by_field_name("name")
            alias = child.child_by_field_name("alias")
            if original:
                imported = _text(original, source)
                names.append(f"{imported} as {_text(alias, source)}" if alias else imported)
    return tuple(dict.fromkeys(names))


def _extract_imports(root: Node, source: bytes) -> tuple[ImportFact, ...]:
    imports: list[ImportFact] = []
    for node in _walk(root):
        if node.type == "import_statement":
            module_node = node.child_by_field_name("source")
            if module_node is not None:
                imports.append(
                    ImportFact(
                        module=_unquote(module_node, source),
                        names=_import_names(node, source),
                        kind="esm",
                        span=_span(node),
                    )
                )
        elif node.type == "call_expression":
            function = node.child_by_field_name("function")
            arguments = node.child_by_field_name("arguments")
            if function is None or arguments is None:
                continue
            function_name = _text(function, source)
            if function_name not in {"require", "import"}:
                continue
            module_node = next(
                (
                    child
                    for child in arguments.named_children
                    if child.type in {"string", "template_string"}
                ),
                None,
            )
            if module_node is not None:
                imports.append(
                    ImportFact(
                        module=_unquote(module_node, source),
                        names=(),
                        kind="commonjs" if function_name == "require" else "dynamic",
                        span=_span(node),
                    )
                )
    return tuple(sorted(imports, key=lambda item: item.span))


def _declaration_names(node: Node, source: bytes) -> list[str]:
    direct_name = _identifier(node, source)
    if direct_name:
        return [direct_name]
    if node.type in {"lexical_declaration", "variable_declaration"}:
        return [
            _text(name, source)
            for declarator in node.named_children
            if declarator.type == "variable_declarator"
            if (name := declarator.child_by_field_name("name")) is not None
            and name.type in {"identifier", "type_identifier"}
        ]
    return []


def _extract_exports(root: Node, source: bytes) -> tuple[ExportFact, ...]:
    exports: list[ExportFact] = []
    for node in root.named_children:
        if node.type != "export_statement":
            continue
        declaration = node.child_by_field_name("declaration")
        is_default = _text(node, source).lstrip().startswith("export default")
        if declaration is not None:
            names = _declaration_names(declaration, source)
            if names:
                exports.extend(
                    ExportFact(
                        name="default" if is_default else name,
                        local_name=name,
                        is_default=is_default,
                        span=_span(node),
                    )
                    for name in names
                )
            elif is_default:
                exports.append(
                    ExportFact(name="default", local_name=None, is_default=True, span=_span(node))
                )
        for child in _walk(node):
            if child.type != "export_specifier":
                continue
            local = child.child_by_field_name("name")
            alias = child.child_by_field_name("alias")
            if local is not None:
                local_name = _text(local, source)
                exports.append(
                    ExportFact(
                        name=_text(alias, source) if alias else local_name,
                        local_name=local_name,
                        is_default=False,
                        span=_span(child),
                    )
                )
    unique = {(item.name, item.local_name, item.span): item for item in exports}
    return tuple(sorted(unique.values(), key=lambda item: (item.span, item.name)))


def _extract_symbols(root: Node, source: bytes) -> tuple[SymbolFact, ...]:
    symbols: list[SymbolFact] = []
    for node in _walk(root):
        kind = SYMBOL_TYPES.get(node.type)
        name = _identifier(node, source) if kind else None
        if kind and name:
            symbols.append(
                SymbolFact(name=name, kind=kind, exported=_is_exported(node), span=_span(node))
            )
            continue
        if node.type != "variable_declarator":
            continue
        value = node.child_by_field_name("value")
        name_node = node.child_by_field_name("name")
        if value is None or name_node is None or value.type not in FUNCTION_VALUES:
            continue
        if name_node.type == "identifier":
            symbols.append(
                SymbolFact(
                    name=_text(name_node, source),
                    kind="function",
                    exported=_is_exported(node),
                    span=_span(node),
                )
            )
    return tuple(sorted(symbols, key=lambda item: (item.span, item.name)))


def _extract_diagnostics(root: Node) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    for node in _walk(root):
        if node.is_error:
            diagnostics.append(
                Diagnostic(
                    code="PARSE_ERROR",
                    message="Tree-sitter could not parse this source range.",
                    span=_span(node),
                )
            )
        elif node.is_missing:
            diagnostics.append(
                Diagnostic(
                    code="MISSING_SYNTAX",
                    message=f"Tree-sitter expected a {node.type} token.",
                    span=_span(node),
                )
            )
    return tuple(sorted(diagnostics, key=lambda item: (item.span, item.code)))


def analyze_source(path: str, source: bytes | str) -> FileAnalysis:
    """Extract deterministic JS/TS facts without executing repository source."""
    normalized_path = str(PurePosixPath(path))
    if normalized_path.startswith("../") or normalized_path.startswith("/"):
        raise ValueError("Source path must stay within the repository")
    source_bytes = source.encode() if isinstance(source, str) else source
    language_name, language = _language_for_path(normalized_path)
    tree = Parser(language).parse(source_bytes)
    root = tree.root_node
    return FileAnalysis(
        path=normalized_path,
        language=language_name,
        content_sha256=hashlib.sha256(source_bytes).hexdigest(),
        analyzer_version=ANALYZER_VERSION,
        imports=_extract_imports(root, source_bytes),
        exports=_extract_exports(root, source_bytes),
        symbols=_extract_symbols(root, source_bytes),
        diagnostics=_extract_diagnostics(root),
    )
