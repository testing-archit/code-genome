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
