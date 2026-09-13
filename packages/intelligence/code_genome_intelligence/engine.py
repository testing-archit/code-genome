import re
from collections import defaultdict

from .types import (
    GroundedResult,
    ImpactRelation,
    ImpactResult,
    RankingEvaluation,
    RetrievalDocument,
    RiskInput,
    RiskResult,
)

TOKEN = re.compile(r"[a-zA-Z0-9_-]{3,}")
STOP = {"and", "the", "for", "from", "that", "this", "what", "where", "which", "with"}
RECENCY_TERMS = {"latest", "newest", "recent", "recently"}
CHANGE_TERMS = {"change", "changed", "changes", "commit", "commits", "update", "updates"}


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in TOKEN.findall(value) if token.lower() not in STOP}


def _asks_for_recent_changes(terms: set[str]) -> bool:
    return bool(terms & RECENCY_TERMS) and bool(terms & CHANGE_TERMS)


def score_risks(items: tuple[RiskInput, ...]) -> tuple[RiskResult, ...]:
    maximum_commits = max((item.commit_count for item in items), default=1)
    maximum_churn = max((item.churn for item in items), default=1)
    results = []
    for item in items:
        frequency = item.commit_count / maximum_commits
        churn = item.churn / maximum_churn
        score = round(0.55 * item.hotspot_score + 0.25 * frequency + 0.2 * churn, 4)
        results.append(
            RiskResult(
                path=item.path,
                score=score,
                features={
                    "hotspot": round(item.hotspot_score, 4),
                    "change_frequency": round(frequency, 4),
                    "relative_churn": round(churn, 4),
                },
                rationale=(
                    f"Relative risk combines hotspot ({item.hotspot_score:.2f}), "
                    f"change frequency ({frequency:.2f}), and churn ({churn:.2f})."
                ),
                evidence_ids=item.evidence_ids,
            )
        )
    return tuple(sorted(results, key=lambda result: (-result.score, result.path)))


def rank_impact(
    selected_path: str, relations: tuple[ImpactRelation, ...], *, limit: int = 20
) -> tuple[ImpactResult, ...]:
    ranked: dict[str, list[tuple[float, ImpactRelation]]] = defaultdict(list)
    for relation in relations:
        if relation.source == selected_path:
            ranked[relation.target].append((relation.weight, relation))
        elif relation.target == selected_path:
            ranked[relation.source].append((relation.weight * 0.9, relation))
    results = []
    for path, contributions in ranked.items():
        score = min(1.0, sum(weight for weight, _ in contributions))
        reasons = tuple(
            f"{relation.kind.replace('_', ' ')} ({weight:.2f})"
            for weight, relation in sorted(contributions, reverse=True, key=lambda item: item[0])
        )
        evidence = tuple(
            dict.fromkeys(
                evidence_id
                for _, relation in contributions
                for evidence_id in relation.evidence_ids
            )
        )
        results.append(ImpactResult(path, round(score, 4), reasons, evidence))
    return tuple(sorted(results, key=lambda result: (-result.score, result.path))[:limit])


def answer_question(
    question: str, documents: tuple[RetrievalDocument, ...], *, limit: int = 5
) -> GroundedResult:
    question_terms = _tokens(question)
    recent_changes = _asks_for_recent_changes(question_terms)
    if recent_changes:
        selected = [document for document in documents if document.kind == "commit"][:limit]
    else:
        selected = []
    ranked: list[tuple[float, RetrievalDocument]] = []
    if not recent_changes:
        for document in documents:
            terms = _tokens(document.text)
            overlap = question_terms & terms
            score = len(overlap) / max(1, len(question_terms))
            if score:
                ranked.append((score, document))
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        selected = [document for _, document in ranked[:limit]]
    if not selected:
        return GroundedResult(
            "Insufficient repository evidence to answer this question.",
            (),
            ("No indexed path, module, hotspot, or commit matched the question terms.",),
        )
    facts = "; ".join(document.text[:220].strip() for document in selected)
    return GroundedResult(
        f"Relevant repository evidence: {facts}",
        tuple(document.id for document in selected),
        ("This answer is extractive and limited to the latest published repository evidence.",),
    )


def evaluate_ranking(scores: tuple[float, ...], labels: tuple[int, ...]) -> RankingEvaluation:
    if not scores or len(scores) != len(labels):
        raise ValueError("scores and labels must be non-empty and have equal length")
    prevalence = sum(labels) / len(labels)
    model_brier = sum((score - label) ** 2 for score, label in zip(scores, labels, strict=True))
    model_brier /= len(labels)
    constant_brier = sum((prevalence - label) ** 2 for label in labels) / len(labels)
    return RankingEvaluation(
        round(model_brier, 4), round(constant_brier, 4), model_brier < constant_brier
    )
