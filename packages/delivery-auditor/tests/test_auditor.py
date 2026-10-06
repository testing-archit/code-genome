from code_genome_delivery_auditor import EvidenceCandidate, assess_claim, parse_claims


def test_parser_preserves_atomic_source_spans() -> None:
    report = "Updated account cards.\n- Deployed navigation to production."
    claims = parse_claims(report)
    assert [report[item.start_offset : item.end_offset] for item in claims] == [
        "Updated account cards.",
        "Deployed navigation to production.",
    ]
    assert [item.claim_type for item in claims] == ["repository_change", "deployment"]


def test_assessment_requires_deployment_evidence_and_cites_repository_matches() -> None:
    change, deployment = parse_claims("Updated account cards. Deployed to production.")
    candidates = (
        EvidenceCandidate("change:abc:cards.tsx", "file_change", "account cards.tsx updated"),
    )
    matched = assess_claim(change, candidates)
    external = assess_claim(deployment, candidates)
    assert matched.status == "VERIFIED"
    assert matched.evidence_ids == ("change:abc:cards.tsx",)
    assert external.status == "EXTERNAL_EVIDENCE_REQUIRED"
    assert external.evidence_ids == ()
    assert external.limitations


def test_delivery_narrative_is_not_mistaken_for_live_deployment() -> None:
    claim = parse_claims("Added the delivery auditor API and claim ledger.")[0]

    assert claim.claim_type == "repository_change"


def _ci(identifier: str, outcome: str) -> EvidenceCandidate:
    return EvidenceCandidate(
        f"provider:{identifier}", "ci_run", "CI check tests", outcome=outcome, name="test"
    )


def _deploy(identifier: str, outcome: str, environment: str) -> EvidenceCandidate:
    return EvidenceCandidate(
        f"provider:{identifier}", "deployment", "deploy", outcome=outcome, environment=environment
    )


def test_test_claims_follow_ci_outcomes_not_code_changes() -> None:
    claim = parse_claims("All tests are passing.")[0]
    assert claim.claim_type == "test"
    assert assess_claim(claim, ()).status == "EXTERNAL_EVIDENCE_REQUIRED"
    passed = assess_claim(claim, (_ci("a", "success"), _ci("b", "skipped")))
    assert passed.status == "VERIFIED" and passed.evidence_ids == ("provider:a",)
    assert "functional correctness" in passed.limitations[0]
    mixed = assess_claim(claim, (_ci("a", "success"), _ci("b", "failure")))
    assert mixed.status == "PARTIALLY_VERIFIED" and mixed.evidence_ids[0] == "provider:b"
    failed = assess_claim(claim, (_ci("b", "failure"),))
    assert failed.status == "NO_SUPPORTING_EVIDENCE"


def test_deployment_claims_need_a_successful_deployment_to_the_named_environment() -> None:
    claim = parse_claims("Deployed the export to production.")[0]
    assert assess_claim(claim, ()).status == "EXTERNAL_EVIDENCE_REQUIRED"
    staging = assess_claim(claim, (_deploy("s", "success", "staging"),))
    assert staging.status == "PARTIALLY_VERIFIED"
    failed = assess_claim(claim, (_deploy("p", "failure", "production"),))
    assert failed.status == "NO_SUPPORTING_EVIDENCE"
    live = assess_claim(claim, (_deploy("p", "success", "Production"),))
    assert live.status == "VERIFIED" and live.evidence_ids == ("provider:p",)
    # A merge or code change alone never counts as a deployment.
    code_only = (EvidenceCandidate("change:abc:export.ts", "repository_change", "export deployed"),)
    assert assess_claim(claim, code_only).status == "EXTERNAL_EVIDENCE_REQUIRED"


def test_only_test_named_checks_can_fully_verify_a_test_claim() -> None:
    claim = parse_claims("All tests pass.")[0]
    lint = EvidenceCandidate(
        "provider:lint", "ci_run", "CI check lint", outcome="success", name="lint"
    )
    unit = EvidenceCandidate(
        "provider:unit", "ci_run", "CI check test", outcome="failure", name="test (node 22)"
    )
    only_lint = assess_claim(claim, (lint,))
    assert (
        only_lint.status == "PARTIALLY_VERIFIED"
        and "named like a test suite" in only_lint.rationale
    )
    # A passing lint check does not hide a failing test job.
    assert assess_claim(claim, (lint, unit)).status == "NO_SUPPORTING_EVIDENCE"
    passing = EvidenceCandidate(
        "provider:unit2", "ci_run", "CI check test", outcome="success", name="Unit tests"
    )
    assert assess_claim(claim, (lint, passing)).status == "VERIFIED"
