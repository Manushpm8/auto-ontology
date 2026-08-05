"""Semantic graph labels and relationship types."""

from __future__ import annotations

SEMANTIC_SOURCE = "semantic"

# Longest sample value that is worth carrying. Profiling stores nothing longer,
# and the prompt/embedding renderers drop anything longer on the way out. Raised
# from 30 because real categorical values exceed it — BIRD district names such as
# "Los Angeles Unified School District" are 35 characters — and a dropped value
# is worse than a long one now that a complete value list is presented to the
# model as a closed set ("one of: ...") rather than as examples.
MAX_SAMPLE_VALUE_LEN = 60

# Semantic node labels
LABEL_TERM = "Term"
LABEL_COLUMN_ATTRIBUTE = "ColumnAttribute"
LABEL_SQL_ATTRIBUTE = "SqlAttribute"
LABEL_TEXT_ATTRIBUTE = "TextAttribute"
LABEL_ANALYSIS = "Analysis"

# Cross-database Train few-shot Q→SQL demos for text-to-SQL ICL.
# Stored in the dedicated ``train_qa`` pgvector collection (not semantic_layer).
LABEL_FEW_SHOT_QA = "FewShotQA"
FEW_SHOT_DATABASE_NAME = "train_qa"

# PQL (predictive) custom analyses — the KumoRFM-prediction twin of CustomAnalysis.
# Stored under their own label so they never mix into the SQL text-to-SQL retrieval;
# retrieved only as few-shot examples for PQL generation.
LABEL_PQL_ANALYSIS = "PqlAnalysis"

# Semantic relationship types
REL_HAS_ATTRIBUTE = "HAS_ATTRIBUTE"
REL_PART_OF = "PART_OF"
REL_PROPERTY_OF = "PROPERTY_OF"
REL_IS_A = "IS_A"
REL_ROLE = "ROLE"
REL_REPRESENTS = "REPRESENTS"
REL_SEMANTIC_FK = "SEMANTIC_FK"
