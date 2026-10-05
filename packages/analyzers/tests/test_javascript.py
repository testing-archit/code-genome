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


def test_metrics_count_code_lines_functions_and_decisions() -> None:
    result = analyze_source(
        "src/m.js",
        "/* block\n   comment */\n// line\n\nconst f = (x) => x ? 1 : 2;\n"
        "function g(items) {\n  for (const i of items) {\n    switch (i) { case 1: break; }\n"
        "  }\n  try { h(); } catch (e) { return e || null; }\n}\n",
    )
    assert result.metrics.loc == 7
    assert result.metrics.functions == 2
    # 1 + ternary + for-of + case + catch + ||
    assert result.metrics.complexity == 6


def test_calls_record_receivers_and_literal_hosts() -> None:
    result = analyze_source(
        "src/c.ts",
        'import x from "./x";\nconst r = require("./r");\n'
        'fetch("https://api.example.com/v1");\naxios.get(`https://hooks.slack.com/x`);\n'
        "db.user.findMany();\nthis.go();\nmake()();\n",
    )
    facts = {(item.callee, item.receiver, item.url_host) for item in result.calls}
    assert ("fetch", None, "api.example.com") in facts
    assert ("get", "axios", "hooks.slack.com") in facts
    assert ("findMany", "db.user", None) in facts
    assert ("go", "this", None) in facts
    assert all(item.callee not in {"require", "import"} for item in result.calls)
