# Delivery Auditor

## Purpose

The Delivery Auditor compares a vendor or internal team’s natural-language progress report with repository evidence. It assists oversight; it does not determine intent, contractual compliance, or whether a person was truthful.

## Workflow

```text
Report → atomic claims → scoped evidence retrieval → facet matching
       → status + confidence + citations → human review / export
Repository activity → materiality clustering → unreported changes
```

## Inputs and scope

Required: repository, report text, start/end time, tracked branch/ref. Optional: PRs, CI, deployments, tickets, design/CMS/infrastructure connectors.

The report viewer always shows the exact scope. Audit across feature branches and PR ancestry in addition to `prod`; squash merges otherwise hide crucial evidence.

## Atomic claims

Split compound prose into independently assessable claims/facets. Example:

`Updated Fixed Deposit and User Profile flows with redesigned cards, tables, filters, carousels and navigation.`

becomes claims/facets for the two flows and each stated change. Preserve the original text span and allow a human to merge/split/correct it before assessment.

## Evidence hierarchy

1. Direct diff/source changes, linked to changed symbols/files.
2. PR merge/base-head ancestry and review metadata.
3. CI result explicitly tied to the SHA.
4. Deployment/release evidence explicitly tied to SHA/artifact/environment.
5. Commit/PR message and semantic similarity (supporting only).

No direct source evidence is required for external-only work, but it must be labelled as requiring an appropriate connected source.

## Facet/status algorithm

For each claim, extract verbs, subject/features, and qualifiers. Retrieve evidence under the declared scope; score source reliability, textual relevance, code/diff linkage, branch reachability, time alignment, and corroboration. Apply transparent thresholds and persist component scores.

| Status | Meaning |
|---|---|
| `VERIFIED` | sufficient evidence covers the material facets |
| `PARTIALLY_VERIFIED` | some material facets supported; list each missing facet |
| `NO_SUPPORTING_EVIDENCE` | no adequate support found in searched scope |
| `EXTERNAL_EVIDENCE_REQUIRED` | repository cannot reasonably evidence the claim |

Separately show lifecycle state: implemented, merged, tested, deployed. A verified implementation is not automatically verified deployment.

## Unreported changes

Cluster changes into human-readable change sets. Flag only items exceeding configured materiality (e.g., authentication, schema/API changes, high churn/risk, protected paths). Show why it was flagged and allow dismissal with an auditable reason. Do not label routine formatting/version bumps as suspicious.

## Output language

Use: “No supporting repository evidence was identified in the selected scope.” Never use: “they lied,” “fake,” “fraud,” or an unwarranted “completed.”

## Example acceptance test

Given commits change `fd-card.tsx` and `fd-carousel.tsx`, but no route/navigation files, a report claim “FD cards and carousel navigation updated” is partially verified: cards/carousel are evidenced; navigation is not. The response includes SHAs, paths, date/branch scope, and a limitation if CI/deployment evidence is unavailable.
