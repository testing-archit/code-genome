# Cross-project defect prediction benchmark

Generated 2026-10-05 by `defect-cross-project@1`. Each repository is held out in turn; scores are on its final (test) period. Labels: a commit matching the keyword fix rule touched the file. Average precision (AP) is the main metric.

## Mean over held-out repositories

| Approach | Mean AP | Mean ROC-AUC | Best on |
|---|---|---|---|
| Heuristic (frequency + churn) | 0.533 | 0.638 | 0 |
| Within-repo logistic regression | 0.535 | 0.706 | 1 |
| Within-repo random forest | 0.542 | 0.649 | 0 |
| Within-repo gradient boosting | 0.434 | 0.620 | 0 |
| Cross-repo logistic regression | 0.661 | 0.746 | 4 |
| Cross-repo random forest | 0.552 | 0.686 | 0 |
| Cross-repo gradient boosting | 0.560 | 0.685 | 1 |

## Per repository (AP)

| Repository | Files | Positives | Heuristic | Within LR | Within RF | Cross LR | Cross RF |
|---|---|---|---|---|---|---|---|
| chalk/chalk | 20 | 9 | 0.591 | 0.567 | 0.560 | 0.657 | 0.672 |
| pmndrs/zustand | 26 | 5 | 0.594 | 0.469 | 0.694 | 0.927 | 0.768 |
| sindresorhus/execa | 492 | 31 | 0.146 | 0.131 | 0.146 | 0.356 | 0.251 |
| sindresorhus/ky | 68 | 23 | 0.789 | 0.759 | 0.766 | 0.807 | 0.465 |
| sindresorhus/p-queue | 14 | 10 | 0.809 | 0.875 | 0.796 | 0.973 | 0.874 |
| unjs/ofetch | 19 | 4 | 0.267 | 0.410 | 0.289 | 0.248 | 0.280 |

Mean over held-out repositories. Labels are fix-touch labels from the keyword fix rule; small repositories make per-repository scores noisy.

## Not included

- tj/commander.js: no published snapshot
