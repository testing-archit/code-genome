import re

from .types import ClaimAssessment, ClaimDraft, EvidenceCandidate

CLAIM_PATTERN = re.compile(r"[^\n.!?]+(?:[.!?]+|$)")
WORD_PATTERN = re.compile(r"[a-zA-Z0-9_-]{3,}")
STOP_WORDS = {"and", "the", "for", "with", "from", "that", "this", "into", "was", "were"}


def _claim_type(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("deploy", "production", "released", "live")):
        return "deployment"
    if any(word in lowered for word in ("test", "ci", "coverage", "passing")):
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


def assess_claim(claim: ClaimDraft, candidates: tuple[EvidenceCandidate, ...]) -> ClaimAssessment:
    if claim.claim_type == "deployment":
        return ClaimAssessment(
            "EXTERNAL_EVIDENCE_REQUIRED",
            1.0,
            (),
            "Repository evidence cannot establish deployment state.",
            ("No trusted deployment provider evidence was connected.",),
        )
    claim_terms = _terms(claim.text)
    ranked: list[tuple[float, EvidenceCandidate]] = []
    for candidate in candidates:
        overlap = claim_terms & _terms(candidate.text)
        score = len(overlap) / max(1, len(claim_terms))
        if score:
            ranked.append((score, candidate))
    ranked.sort(key=lambda item: (-item[0], item[1].id))
    best = ranked[0][0] if ranked else 0.0
    evidence = tuple(item[1].id for item in ranked[:5])
    if claim.claim_type == "test" and not any(
        candidate.kind == "ci_run" for _, candidate in ranked
    ):
        return ClaimAssessment(
            "EXTERNAL_EVIDENCE_REQUIRED",
            1.0,
            evidence,
            "Repository changes do not prove that tests passed.",
            ("No trusted CI evidence was connected.",),
        )
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
