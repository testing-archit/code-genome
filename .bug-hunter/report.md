# Bug Hunter Report

- Findings reviewed: 1
- Confirmed: 1
- Dismissed: 0
- Manual review: 0

## Confirmed Bugs

- BUG-1 | Medium | packages/intelligence/code_genome_intelligence/engine.py | Valid recency questions are rejected unless a commit message literally contains the query terms.
  Confidence: 99 (high) | INDEPENDENTLY_VERIFIED
  Analysis: A normal recency question follows a reachable API path but returns a refusal because lexical overlap is the only retrieval strategy. Recent commit documents are already ordered newest-first and available, so the refusal is incorrect for this valid intent.

## Manual Review

- None

## Dismissed Findings

- None
