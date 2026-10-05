"""Repository machine-learning models for CODE GENOME.

Models rank, classify, and explain. They never establish repository facts on their own;
every prediction carries the evidence it was computed from.
"""

from .components import group_components
from .impact import ImpactPrediction, predict_impact
from .impact_ranking import WEIGHTED_VERSION as IMPACT_WEIGHTED_VERSION
from .impact_ranking import (
    ImpactSignalContext,
    WeightedImpact,
    keyword_fix_shas,
    rank_weighted_impact,
    weighted_impact_score,
)
from .instability import MODEL_VERSION as INSTABILITY_VERSION
from .pipeline import TASKS, TrainingInputs, file_document, train_all
from .records import ChangeRecord, CommitRecord, FileRecord, ImportRecord, SearchDocument
from .retrieval import MODEL_VERSION as RETRIEVAL_VERSION
from .retrieval import HybridRetriever, SearchHit
from .text import tokenize

__all__ = [
    "IMPACT_WEIGHTED_VERSION",
    "ImpactSignalContext",
    "WeightedImpact",
    "keyword_fix_shas",
    "rank_weighted_impact",
    "weighted_impact_score",
    "INSTABILITY_VERSION",
    "RETRIEVAL_VERSION",
    "TASKS",
    "ChangeRecord",
    "CommitRecord",
    "FileRecord",
    "HybridRetriever",
    "ImpactPrediction",
    "ImportRecord",
    "SearchDocument",
    "SearchHit",
    "TrainingInputs",
    "file_document",
    "group_components",
    "predict_impact",
    "tokenize",
    "train_all",
]
