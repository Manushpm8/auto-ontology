"""Semantic graph labels and relationship types."""

from __future__ import annotations

SEMANTIC_SOURCE = "semantic"

# Semantic node labels
LABEL_SEMANTIC_IMPORT_LOCK = "SemanticImportLock"
LABEL_SEMANTIC_MODEL = "SemanticModel"
LABEL_TERM = "Term"
LABEL_COLUMN_ATTRIBUTE = "ColumnAttribute"
LABEL_SQL_ATTRIBUTE = "SqlAttribute"
LABEL_TEXT_ATTRIBUTE = "TextAttribute"
LABEL_ANALYSIS = "Analysis"

# PQL (predictive) custom analyses — the KumoRFM-prediction twin of CustomAnalysis.
# Stored under their own label so they never mix into the SQL text-to-SQL retrieval;
# retrieved only as few-shot examples for PQL generation.
LABEL_PQL_ANALYSIS = "PqlAnalysis"

# Semantic relationship types
REL_HAS_ATTRIBUTE = "HAS_ATTRIBUTE"
REL_HAS_DATASET = "HAS_DATASET"
REL_PART_OF = "PART_OF"
REL_PROPERTY_OF = "PROPERTY_OF"
REL_IS_A = "IS_A"
REL_ROLE = "ROLE"
REL_REPRESENTS = "REPRESENTS"
REL_SEMANTIC_FK = "SEMANTIC_FK"
