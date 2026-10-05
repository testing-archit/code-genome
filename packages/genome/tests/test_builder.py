from code_genome_analyzers import analyze_source
from code_genome_genome import build_structural_graph

SNAPSHOT_SHA = "a" * 40


def test_builds_stable_structural_graph_with_provenance() -> None:
    files = [
        analyze_source(
            "src/profile.ts",
            'import { format } from "./format";\n'
            'import React from "react";\n'
            "export const profile = () => format('profile');",
        ),
        analyze_source("src/format.ts", "export function format(value: string) { return value; }"),
    ]

    graph = build_structural_graph("repo_fixture", SNAPSHOT_SHA, files)
    repeated = build_structural_graph("repo_fixture", SNAPSHOT_SHA, list(reversed(files)))

    assert graph == repeated
    assert graph.repository_sha == SNAPSHOT_SHA
    assert {node.kind for node in graph.nodes} == {"FILE", "SYMBOL", "MODULE"}
    assert {edge.kind for edge in graph.edges} == {"DECLARES", "EXPORTS", "IMPORTS", "CALLS"}
    assert not graph.diagnostics
    assert all(edge.evidence_id in {item.id for item in graph.evidence} for edge in graph.edges)
    assert all(node.evidence_ids for node in graph.nodes)


def test_unresolved_relative_import_is_a_diagnostic_not_a_phantom_node() -> None:
    analysis = analyze_source("src/index.ts", 'import value from "../missing";\nexport { value };')
    graph = build_structural_graph("repo_fixture", SNAPSHOT_SHA, [analysis])

    assert [item.code for item in graph.diagnostics] == ["UNRESOLVED_IMPORT"]
    assert all(node.natural_key != "external:../missing" for node in graph.nodes)
    assert all(edge.kind != "IMPORTS" for edge in graph.edges)


def test_rejects_duplicate_paths_and_invalid_snapshot_ids() -> None:
    analysis = analyze_source("src/index.ts", "export const value = 1;")

    try:
        build_structural_graph("repo_fixture", "not-a-sha", [analysis])
    except ValueError as error:
        assert "hexadecimal" in str(error)
    else:
        raise AssertionError("Expected invalid SHA to be rejected")

    try:
        build_structural_graph("repo_fixture", SNAPSHOT_SHA, [analysis, analysis])
    except ValueError as error:
        assert "unique" in str(error)
    else:
        raise AssertionError("Expected duplicate paths to be rejected")


def test_resolves_safe_parent_imports_and_tracks_non_code_assets() -> None:
    files = [
        analyze_source(
            "app/page.tsx",
            'import { Button } from "../components/button";\nimport "./page.css";\n'
            "export const Page = () => <Button />;",
        ),
        analyze_source("components/button.tsx", "export const Button = () => <button />;"),
    ]
    graph = build_structural_graph("repo_fixture", SNAPSHOT_SHA, files)

    assert not graph.diagnostics
    assert any(node.natural_key == "asset:app/page.css" for node in graph.nodes)
    assert len([edge for edge in graph.edges if edge.kind == "IMPORTS"]) == 2


def test_file_nodes_carry_code_metrics() -> None:
    analysis = analyze_source(
        "src/metrics.ts",
        "// header comment\n\nexport function pick(a: number, b?: number) {\n"
        "  if (a > 1 && b) { return a; }\n  return b ?? 0;\n}\n",
    )
    graph = build_structural_graph("repo_fixture", SNAPSHOT_SHA, [analysis])
    file_node = next(node for node in graph.nodes if node.kind == "FILE")
    assert file_node.properties["loc"] == 4
    assert file_node.properties["functions"] == 1
    # 1 + if + && + ??
    assert file_node.properties["complexity"] == 4


def test_calls_resolve_through_imports_as_candidates_with_provenance() -> None:
    files = [
        analyze_source(
            "src/app.ts",
            'import { format as fmt } from "./format";\n'
            'import * as util from "./util";\n'
            'import { external } from "lodash";\n'
            "export function main() { return fmt(util.helper()) + external() + unknown(); }\n"
            "function local() { return main(); }\n",
        ),
        analyze_source("src/format.ts", "export function format(v: string) { return v; }"),
        analyze_source("src/util.ts", "export function helper() { return 'x'; }"),
    ]
    graph = build_structural_graph("repo_fixture", SNAPSHOT_SHA, files)
    keys = {node.id: node.natural_key for node in graph.nodes}
    calls = {
        (keys[edge.from_node].split("#")[1], keys[edge.to_node].split("#")[1]): edge
        for edge in graph.edges
        if edge.kind == "CALLS"
    }
    assert set(calls) == {
        ("function:main:4:7", "function:format:1:7"),
        ("function:main:4:7", "function:helper:1:7"),
        ("function:local:5:0", "function:main:4:7"),
    }
    assert all(0 < edge.confidence < 1 for edge in calls.values())
    local_call = calls[("function:local:5:0", "function:main:4:7")]
    assert local_call.confidence < calls[("function:main:4:7", "function:format:1:7")].confidence
    evidence = {item.id: item for item in graph.evidence}
    for edge in calls.values():
        assert evidence[edge.evidence_id].path == "src/app.ts"
        assert evidence[edge.evidence_id].span is not None


def test_python_imports_and_calls_resolve_across_packages() -> None:
    from code_genome_analyzers import analyze_source

    files = [
        analyze_source(
            "services/api/app/routes.py",
            "from .services import billing\nfrom app.models import Invoice\nimport fastapi\n\n"
            "def handler():\n    return billing.charge(Invoice())\n",
        ),
        analyze_source("services/api/app/services/__init__.py", ""),
        analyze_source(
            "services/api/app/services/billing.py", "def charge(invoice):\n    return invoice\n"
        ),
        analyze_source("services/api/app/models.py", "class Invoice:\n    pass\n"),
        analyze_source("services/api/app/broken.py", "from .missing import x\n"),
    ]
    graph = build_structural_graph("repo_py", SNAPSHOT_SHA, files)
    keys = {node.id: node.natural_key for node in graph.nodes}
    imports = {
        (keys[edge.from_node], keys[edge.to_node]) for edge in graph.edges if edge.kind == "IMPORTS"
    }
    assert ("services/api/app/routes.py", "services/api/app/services/billing.py") in imports
    assert ("services/api/app/routes.py", "services/api/app/models.py") in imports
    assert ("services/api/app/routes.py", "external:fastapi") in imports
    assert any(
        item.code == "UNRESOLVED_IMPORT" and item.path.endswith("broken.py")
        for item in graph.diagnostics
    )
    calls = {
        (keys[edge.from_node], keys[edge.to_node]) for edge in graph.edges if edge.kind == "CALLS"
    }
    targets = {target for _, target in calls}
    assert "services/api/app/models.py#class:Invoice:1:1" in targets
    assert "services/api/app/services/billing.py#function:charge:1:1" in targets
    assert graph.analysis_version.endswith("tree-sitter-js-ts-py@0.3.0")
