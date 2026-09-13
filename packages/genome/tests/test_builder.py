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
    assert {edge.kind for edge in graph.edges} == {"DECLARES", "EXPORTS", "IMPORTS"}
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
