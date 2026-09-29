"""Commit intent classification.

TF-IDF over identifier-aware word n-grams plus character n-grams, fed to a multinomial
logistic regression. Training data is the hand-labelled seed corpus plus weak labels
from the repository's own Conventional Commits prefixes (prefixes are stripped before
training so the model learns from wording). Evaluated with stratified 5-fold
cross-validation and, when the repository has enough prefixed commits, a
seed-only model scored on those commits as an out-of-domain test.
"""

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import FeatureUnion, Pipeline

from .data.commit_seed import COMMIT_SEED, CONVENTIONAL_TO_INTENT, INTENTS, SEED_VERSION
from .records import CommitRecord
from .text import conventional_prefix, tokenize

MODEL_VERSION = "commit-intent-tfidf-lr@1"
RANDOM_STATE = 7


def _word_ngrams(text: str) -> list[str]:
    tokens = tokenize(text, translate=False)
    return tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]


def build_pipeline() -> Pipeline:
    features = FeatureUnion(
        [
            ("words", TfidfVectorizer(analyzer=_word_ngrams, sublinear_tf=True, min_df=1)),
            (
                "chars",
                TfidfVectorizer(
                    analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1
                ),
            ),
        ]
    )
    classifier = LogisticRegression(C=6.0, max_iter=3000, class_weight="balanced")
    return Pipeline([("features", features), ("classifier", classifier)])


@dataclass
class IntentPrediction:
    sha: str
    intent: str
    probability: float
    source: str  # "conventional" when the author labelled it, otherwise "model"


@dataclass
class IntentResult:
    model_version: str
    status: str
    dataset: dict[str, object]
    metrics: dict[str, object]
    predictions: list[IntentPrediction] = field(default_factory=list)
    top_terms: dict[str, list[str]] = field(default_factory=dict)


def _weak_labels(commits: list[CommitRecord]) -> list[tuple[str, str, str]]:
    labelled = []
    for commit in commits:
        prefix, body = conventional_prefix(commit.message)
        intent = CONVENTIONAL_TO_INTENT.get(prefix or "")
        if intent and len(body.strip()) >= 3:
            labelled.append((commit.sha, body, intent))
    return labelled


def train_intent_classifier(commits: list[CommitRecord]) -> IntentResult:
    weak = _weak_labels(commits)
    texts = [text for text, _ in COMMIT_SEED] + [body for _, body, _ in weak]
    labels = [label for _, label in COMMIT_SEED] + [label for _, _, label in weak]
    counts = Counter(labels)
    folds = max(2, min(5, min(counts.values())))

    pipeline = build_pipeline()
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=RANDOM_STATE)
    predicted = cross_val_predict(pipeline, texts, labels, cv=splitter)
    majority = counts.most_common(1)[0][0]
    metrics: dict[str, object] = {
        "cv_folds": folds,
        "cv_accuracy": round(float(accuracy_score(labels, predicted)), 4),
        "cv_macro_f1": round(float(f1_score(labels, predicted, average="macro")), 4),
        "majority_baseline_macro_f1": round(
            float(f1_score(labels, [majority] * len(labels), average="macro")), 4
        ),
        "per_class_f1": {
            intent: round(float(score), 4)
            for intent, score in zip(
                INTENTS,
                f1_score(labels, predicted, labels=list(INTENTS), average=None, zero_division=0),
                strict=True,
            )
        },
        "confusion_matrix": {
            "labels": list(INTENTS),
            "matrix": confusion_matrix(labels, predicted, labels=list(INTENTS)).tolist(),
        },
    }

    # Out-of-domain check: a model trained only on the seed corpus, scored on the
    # repository's author-labelled commits.
    if len(weak) >= 10:
        seed_only = build_pipeline().fit(
            [text for text, _ in COMMIT_SEED], [label for _, label in COMMIT_SEED]
        )
        repo_predicted = seed_only.predict([body for _, body, _ in weak])
        repo_truth = [label for _, _, label in weak]
        metrics["repository_holdout"] = {
            "examples": len(weak),
            "accuracy": round(float(accuracy_score(repo_truth, repo_predicted)), 4),
            "macro_f1": round(float(f1_score(repo_truth, repo_predicted, average="macro")), 4),
        }

    pipeline.fit(texts, labels)
    classifier: LogisticRegression = pipeline.named_steps["classifier"]
    word_vectorizer: TfidfVectorizer = pipeline.named_steps["features"].transformer_list[0][1]
    vocabulary = np.array(word_vectorizer.get_feature_names_out())
    word_count = len(vocabulary)
    top_terms = {
        str(intent): [
            str(term)
            for term in vocabulary[np.argsort(classifier.coef_[index][:word_count])[::-1][:8]]
        ]
        for index, intent in enumerate(classifier.classes_)
    }

    authored = {sha: label for sha, _, label in weak}
    messages = [conventional_prefix(commit.message)[1] or commit.message for commit in commits]
    predictions: list[IntentPrediction] = []
    if commits:
        probabilities = pipeline.predict_proba(messages)
        classes = list(classifier.classes_)
        for commit, row in zip(commits, probabilities, strict=True):
            if commit.sha in authored:
                label = authored[commit.sha]
                predictions.append(IntentPrediction(commit.sha, label, 1.0, "conventional"))
            else:
                best = int(np.argmax(row))
                predictions.append(
                    IntentPrediction(
                        commit.sha, str(classes[best]), round(float(row[best]), 4), "model"
                    )
                )

    return IntentResult(
        model_version=MODEL_VERSION,
        status="trained",
        dataset={
            "seed_version": SEED_VERSION,
            "seed_examples": len(COMMIT_SEED),
            "weak_labels": len(weak),
            "class_counts": dict(counts),
            "predicted_commits": len(predictions),
        },
        metrics=metrics,
        predictions=predictions,
        top_terms=top_terms,
    )
