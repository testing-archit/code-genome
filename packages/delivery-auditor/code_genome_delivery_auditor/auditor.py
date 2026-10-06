import re

from .types import ClaimAssessment, ClaimDraft, EvidenceCandidate

CLAIM_PATTERN = re.compile(r"[^\n.!?]+(?:[.!?]+|$)")
WORD_PATTERN = re.compile(r"[a-zA-Z0-9_-]{3,}")
STOP_WORDS = {"and", "the", "for", "with", "from", "that", "this", "into", "was", "were"}


def _claim_type(text: str) -> str:
    words = {word.lower() for word in WORD_PATTERN.findall(text)}
    if words & {"deploy", "deployed", "deployment", "production", "released", "live"}:
        return "deployment"
    if words & {"test", "tests", "tested", "ci", "coverage", "passing"}:
        return "test"
    return "repository_change"


def parse_claims(text: str, *, max_claims: int = 100) -> tuple[ClaimDraft, ...]:
    if not text.strip() or len(text) > 100_000:
        raise ValueError("Delivery report must contain between 1 and 100000 characters")
    claims: list[ClaimDraft] = []
    for match in CLAIM_PATTERN.finditer(text):
        raw = match.group()
        leading = len(raw) - len(raw.lstrip(" \t-*•"))
        cleaned = raw.strip().lstrip("-*•").strip()
        if not cleaned:
            continue
        start = match.start() + leading
        end = start + len(cleaned)
        claims.append(ClaimDraft(len(claims), cleaned, start, end, _claim_type(cleaned)))
        if len(claims) >= max_claims:
            break
    if not claims:
        raise ValueError("Delivery report did not contain a reviewable claim")
    return tuple(claims)


def _terms(text: str) -> set[str]:
    return {word.lower() for word in WORD_PATTERN.findall(text) if word.lower() not in STOP_WORDS}


ENVIRONMENT_WORDS = {
    "production": {"production", "prod", "live"},
    "staging": {"staging", "stage"},
    "preview": {"preview"},
    "development": {"development", "dev"},
}


def _named_environments(text: str) -> set[str]:
    words = _terms(text)
    return {name for name, aliases in ENVIRONMENT_WORDS.items() if words & aliases}


def _assess_deployment(
    claim: ClaimDraft, candidates: tuple[EvidenceCandidate, ...]
) -> ClaimAssessment:
    deployments = [item for item in candidates if item.kind == "deployment"]
    if not deployments:
        return ClaimAssessment(
            "EXTERNAL_EVIDENCE_REQUIRED",
            1.0,
            (),
            "Repository evidence cannot establish deployment state.",
            ("No deployment provider evidence was found for the scoped commits.",),
        )
    succeeded = [item for item in deployments if item.outcome == "success"]
    if not succeeded:
        return ClaimAssessment(
            "NO_SUPPORTING_EVIDENCE",
            0.0,
            tuple(item.id for item in deployments[:5]),
            "Deployments were recorded for the scoped commits, but none succeeded.",
            ("Only GitHub Deployments for the scoped commits were checked.",),
        )
    wanted = _named_environments(claim.text)
    matching = [
        item
        for item in succeeded
        if not wanted or _named_environments(item.environment or "") & wanted
    ]
    if matching:
        return ClaimAssessment(
            "VERIFIED",
            1.0,
            tuple(item.id for item in matching[:5]),
            "A successful deployment of a scoped commit was recorded"
            + (f" to {', '.join(sorted(wanted))}." if wanted else "."),
            ("Deployment state comes from GitHub Deployments, not from the code.",),
        )
    return ClaimAssessment(
        "PARTIALLY_VERIFIED",
        0.5,
        tuple(item.id for item in succeeded[:5]),
        f"Scoped commits were deployed, but not to {', '.join(sorted(wanted))}.",
        ("Environment names are matched by keyword.",),
    )


TEST_CHECK = re.compile(
    r"test|spec|pytest|jest|vitest|mocha|playwright|cypress|e2e|unit|integration|check suite",
    re.IGNORECASE,
)


def _assess_tests(
    candidates: tuple[EvidenceCandidate, ...], code_evidence: tuple[str, ...]
) -> ClaimAssessment:
    all_runs = [item for item in candidates if item.kind == "ci_run"]
    # A lint or deploy check passing says nothing about tests; judge test-named checks when
    # there are any, otherwise all checks with a lower ceiling.
    test_runs = [item for item in all_runs if item.name and TEST_CHECK.search(item.name)]
    assessment = _judge_runs(test_runs or all_runs, code_evidence)
    if all_runs and not test_runs and assessment.status == "VERIFIED":
        return ClaimAssessment(
            "PARTIALLY_VERIFIED",
            0.5,
            assessment.evidence_ids,
            "CI checks passed, but none is named like a test suite.",
            (*assessment.limitations, "Check names were matched against common test runners."),
        )
    return assessment


def _judge_runs(runs: list[EvidenceCandidate], code_evidence: tuple[str, ...]) -> ClaimAssessment:
    if not runs:
        return ClaimAssessment(
            "EXTERNAL_EVIDENCE_REQUIRED",
            1.0,
            code_evidence,
            "Repository changes do not prove that tests passed.",
            ("No CI runs were found for the scoped commits.",),
        )
    decisive = [item for item in runs if item.outcome not in {"neutral", "skipped"}]
    passed = [item for item in decisive if item.outcome == "success"]
    failed = [item for item in decisive if item.outcome != "success"]
    if passed and not failed:
        return ClaimAssessment(
            "VERIFIED",
            1.0,
            tuple(item.id for item in passed[:5]),
            f"All {len(passed)} completed CI check(s) on the scoped commits passed.",
            ("CI evidence shows checks passed; it does not prove functional correctness.",),
        )
    if passed:
        return ClaimAssessment(
            "PARTIALLY_VERIFIED",
            round(len(passed) / len(decisive), 4),
            tuple(item.id for item in [*failed[:3], *passed[:2]]),
            f"{len(passed)} CI check(s) passed and {len(failed)} did not.",
            ("Failed checks are cited first.",),
        )
    return ClaimAssessment(
        "NO_SUPPORTING_EVIDENCE",
        0.0,
        tuple(item.id for item in (failed or runs)[:5]),
        "CI ran on the scoped commits, but no check passed.",
        (),
    )


def assess_claim(claim: ClaimDraft, candidates: tuple[EvidenceCandidate, ...]) -> ClaimAssessment:
    if claim.claim_type == "deployment":
        return _assess_deployment(claim, candidates)
    claim_terms = _terms(claim.text)
    ranked: list[tuple[float, EvidenceCandidate]] = []
    for candidate in candidates:
        if candidate.kind in {"ci_run", "deployment"}:
            continue
        overlap = claim_terms & _terms(candidate.text)
        score = len(overlap) / max(1, len(claim_terms))
        if score:
            ranked.append((score, candidate))
    ranked.sort(key=lambda item: (-item[0], item[1].id))
    best = ranked[0][0] if ranked else 0.0
    evidence = tuple(item[1].id for item in ranked[:5])
    if claim.claim_type == "test":
        return _assess_tests(candidates, evidence)
    if best >= 0.6:
        status = "VERIFIED"
        rationale = "Repository evidence strongly overlaps the claim terms."
    elif best >= 0.25:
        status = "PARTIALLY_VERIFIED"
        rationale = "Repository evidence supports only part of the claim."
    else:
        status = "NO_SUPPORTING_EVIDENCE"
        rationale = "No repository evidence matched the material claim terms."
    return ClaimAssessment(
        status,
        round(best, 4),
        evidence,
        rationale,
        () if evidence else ("The selected commit and file-change scope was searched.",),
    )
