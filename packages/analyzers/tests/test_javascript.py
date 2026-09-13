from pathlib import Path

import pytest
from code_genome_analyzers import analyze_source

FIXTURES = Path(__file__).parent / "fixtures"


def test_extracts_typescript_structure_with_source_ranges() -> None:
    source = (FIXTURES / "component.tsx").read_bytes()
    result = analyze_source("src/component.tsx", source)

    assert result.language == "tsx"
    assert [(item.module, item.names, item.kind) for item in result.imports] == [
        ("react", ("React", "useMemo as memo"), "esm"),
        ("./format", ("formatName",), "esm"),
    ]
    assert {(item.name, item.local_name, item.is_default) for item in result.exports} == {
        ("Profile", "Profile", False),
        ("ProfileCard", "ProfileCard", False),
        ("buildProfile", "buildProfile", False),
        ("default", "ProfilePage", True),
    }
    assert {(item.name, item.kind, item.exported) for item in result.symbols} >= {
        ("Profile", "interface", True),
        ("ProfileCard", "class", True),
        ("render", "method", False),
        ("buildProfile", "function", True),
        ("ProfilePage", "function", True),
    }
    assert result.diagnostics == ()
    assert result.symbols[0].span.start_line > 0


def test_extracts_commonjs_and_dynamic_imports() -> None:
    result = analyze_source(
        "src/load.js",
        'const local = require("./local");\n'
        'export async function load() { return import("./lazy.js"); }',
    )
    assert [(item.module, item.kind) for item in result.imports] == [
        ("./local", "commonjs"),
        ("./lazy.js", "dynamic"),
    ]


def test_malformed_file_preserves_valid_facts_and_reports_diagnostics() -> None:
    result = analyze_source("src/malformed.ts", (FIXTURES / "malformed.ts").read_bytes())
    assert result.imports[0].module == "./value"
    assert result.diagnostics
    assert all(item.code in {"PARSE_ERROR", "MISSING_SYNTAX"} for item in result.diagnostics)


def test_analysis_is_deterministic_and_rejects_unsafe_paths() -> None:
    source = "export const execute = () => true;"
    first = analyze_source("src/stable.ts", source)
    second = analyze_source("src/stable.ts", source)
    assert first == second

    with pytest.raises(ValueError, match="within the repository"):
        analyze_source("../outside.ts", source)
