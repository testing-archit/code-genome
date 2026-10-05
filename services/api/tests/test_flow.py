from pathlib import Path

from code_genome_api.services.flow import trace_flow
from code_genome_api.services.genome import GenomeGraphBuilder, Node
from code_genome_api.services.question_routing import flow_target, is_flow_question
from fastapi.testclient import TestClient
from test_project_knowledge import _ask, analysed  # noqa: F401


def _graph() -> GenomeGraphBuilder:
    graph = GenomeGraphBuilder()
    for path in ("src/api.ts", "src/service.ts", "src/repo.ts", "src/app.ts"):
        graph.node(
            Node(f"file:{path}", "file", path, {"path": path}, [f"evidence:ev_{path[4:-3]}"])
        )
    graph.node(Node("component:src", "component", "src"))
    graph.node(Node("datastore:PostgreSQL", "datastore", "PostgreSQL", inferred=True))
    graph.edge("CALLS", "file:src/api.ts", "file:src/service.ts", evidence_ids=["evidence:c1"])
    graph.edge("IMPORTS", "file:src/service.ts", "file:src/repo.ts", evidence_ids=["evidence:i1"])
    graph.edge("IMPORTS", "file:src/app.ts", "file:src/api.ts", evidence_ids=["evidence:i2"])
    graph.edge("BELONGS_TO_MODULE", "file:src/repo.ts", "component:src", inferred=True)
    graph.edge(
        "READS_FROM",
        "component:src",
        "datastore:PostgreSQL",
        inferred=True,
        evidence_ids=["evidence:db"],
    )
    return graph


def test_trace_follows_calls_and_imports_to_a_data_store() -> None:
    flow = trace_flow(_graph(), "src/api.ts")
    assert len(flow.chains) == 1
    chain = flow.chains[0]
    assert [(step.source, step.kind, step.target) for step in chain] == [
        ("file:src/api.ts", "CALLS", "file:src/service.ts"),
        ("file:src/service.ts", "IMPORTS", "file:src/repo.ts"),
        ("component:src", "READS_FROM", "datastore:PostgreSQL"),
    ]
    assert chain[-1].inferred and chain[-1].evidence_ids == ("evidence:db",)
    assert [(step.source, step.kind) for step in flow.callers] == [("file:src/app.ts", "IMPORTS")]


def test_trace_of_an_unknown_or_isolated_file_is_empty() -> None:
    assert trace_flow(_graph(), "src/missing.ts").empty
    graph = GenomeGraphBuilder()
    graph.node(Node("file:a.ts", "file", "a.ts", {"path": "a.ts"}))
    assert trace_flow(graph, "a.ts").empty


def test_flow_questions_in_english_hinglish_and_hindi() -> None:
    for question in (
        "How does retry work?",
        "how is authentication handled in this app",
        "What happens when a payment fails?",
        "walk me through the checkout",
        "retry kaise kaam karta hai?",
        "रिट्राई कैसे काम करता है?",
    ):
        assert is_flow_question(question), question
    for question in ("What does this project do?", "If I change app.tsx what breaks?"):
        assert not is_flow_question(question), question


def test_flow_target_prefers_named_files_and_skips_tests_unless_asked() -> None:
    paths = ["src/retry.ts", "src/core.ts", "test/fetch.ts", "src/http/client.ts"]
    symbols = {
        "retry": "src/core.ts",
        "fetchwrapper": "test/fetch.ts",
        "sendrequest": "src/core.ts",
    }
    components = {"src/http": ["src/http/client.ts"]}
    assert flow_target("How does src/core.ts work?", paths, symbols, components) == "src/core.ts"
    # A file named after the word beats a symbol with the same name.
    assert flow_target("How does retry work?", paths, symbols, components) == "src/retry.ts"
    assert flow_target("How does sendRequest work?", paths, symbols, components) == "src/core.ts"
    assert flow_target("How does fetchWrapper work?", paths, symbols, components) is None
    assert (
        flow_target("How does the fetchWrapper test work?", paths, symbols, components)
        == "test/fetch.ts"
    )
    assert flow_target("How does src/http work?", paths, symbols, components) == (
        "src/http/client.ts"
    )
    assert flow_target("How does billing work?", paths, symbols, components) is None


def test_flow_question_is_answered_from_the_graph_with_citations(
    analysed: Path,  # noqa: F811
    client: TestClient,
) -> None:
    answer = _ask(client, "How does src/index.ts work?")
    assert "src/format.ts" in answer["answer"]
    assert answer["evidence_ids"]
    assert all(item.startswith("evidence:") for item in answer["evidence_ids"])
    assert any("code-flow@1" in item for item in answer["limitations"])


def test_flow_question_without_a_target_falls_back_to_retrieval(
    analysed: Path,  # noqa: F811
    client: TestClient,
) -> None:
    answer = _ask(client, "How does the billing pipeline work?")
    assert not any("code-flow@1" in item for item in answer["limitations"])
