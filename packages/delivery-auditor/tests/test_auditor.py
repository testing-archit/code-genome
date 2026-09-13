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
