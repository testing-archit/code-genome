import json
from pathlib import Path

from code_genome_intelligence import (
    ImpactRelation,
    RetrievalDocument,
    RiskInput,
    answer_question,
    evaluate_ranking,
    rank_impact,
    score_risks,
)


def test_risk_is_explainable_and_beats_constant_baseline() -> None:
    results = score_risks(
        (
            RiskInput("billing.py", 0.95, 10, 500, ("commit:a",)),
            RiskInput("types.py", 0.1, 1, 10, ("commit:b",)),
        )
    )
    evaluation = evaluate_ranking(tuple(item.score for item in results), (1, 0))
    assert results[0].path == "billing.py"
    assert results[0].features["relative_churn"] == 1
    assert results[0].evidence_ids
    assert evaluation.useful


def test_impact_combines_cited_relationships() -> None:
    results = rank_impact(
        "api.py",
        (
            ImpactRelation("api.py", "billing.py", "imports", 0.6, ("source:1",)),
            ImpactRelation("api.py", "billing.py", "co_change", 0.3, ("commit:a",)),
        ),
    )
    assert results[0].path == "billing.py"
    assert results[0].score == 0.9
    assert results[0].evidence_ids == ("source:1", "commit:a")


def test_grounded_answer_refuses_when_no_evidence_matches() -> None:
    documents = (RetrievalDocument("commit:a", "commit", "invoice export endpoint"),)
    grounded = answer_question("Where is invoice export?", documents)
    refused = answer_question("How does quantum scheduling work?", documents)
    assert grounded.evidence_ids == ("commit:a",)
    assert "invoice export" in grounded.answer
    assert refused.evidence_ids == ()
    assert "Insufficient" in refused.answer


def test_grounded_answer_uses_newest_commits_for_recent_change_questions() -> None:
    documents = (
        RetrievalDocument("module:billing", "module", "Billing and invoice workflows."),
        RetrievalDocument("commit:newest", "commit", "harden repository credentials"),
        RetrievalDocument("commit:next", "commit", "add delivery report downloads"),
        RetrievalDocument("commit:older", "commit", "build architecture projection"),
    )

    grounded = answer_question("What were the latest changes about?", documents, limit=2)

    assert grounded.evidence_ids == ("commit:newest", "commit:next")
    assert "harden repository credentials" in grounded.answer
    assert "build architecture projection" not in grounded.answer


def test_reviewed_pilot_labels_beat_the_trivial_constant_baseline() -> None:
    fixture = Path(__file__).parent / "fixtures" / "risk_labels.json"
    rows = json.loads(fixture.read_text(encoding="utf-8"))["rows"]
    result = evaluate_ranking(
        tuple(row["score"] for row in rows), tuple(row["label"] for row in rows)
    )

    assert result.useful
    assert result.model_brier < result.constant_brier
