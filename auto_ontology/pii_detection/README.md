<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# PII Detection

Rules-first PII classification for database columns. During catalog ingestion,
high-confidence decisions are persisted through the project's existing `PII`
tag; detection remains independently testable from storage.

## Flow

1. High-confidence rules handle obvious PII and obvious non-PII.
2. Uncertain columns are sent to an optional LLM backend.
3. Results include status, category, confidence, reason, and source.
4. Only results above the chosen confidence threshold are auto-tagged.

## Usage

```python
from auto_ontology.pii_detection import ColumnInput, PiiDetector

detector = PiiDetector()
decision = detector.detect(
    ColumnInput(column_name="email_address", table_name="customers")
)

if decision.should_auto_tag(threshold=0.9):
    # The ingestion integration attaches the shared PII tag to this column.
    ...
```

## Propagation to attributes

A column tagged `PII` also makes the attributes built from it `PII`:

- **Column attributes** linked to the column through `column__has_attribute`
  (`column__semantic_fk` is intentionally ignored: the column only references
  the attribute, it is not an instance of it).
- **SQL attributes** whose SQL reads the column
  (`sql_attribute__sql` → `sql_query__column`).

`propagate_pii_to_attributes()` reads the labels already on the columns, so
hand-applied tags propagate too. It runs after PII detection in every ingest and
again after semantic compilation, because attributes are created by the latter.
Only attributes linked to a Term are tagged. It is additive: existing labels
keep their source. Taking `PII` off a column — from the tags API, when a
rule takes back a label it no longer matches, when that rule is deleted
(without keeping its labels), or when ingest drops the column from the
catalog — also takes it off that column's ColumnAttributes,
and off any SQL attribute whose query no longer reads a tagged column
(`pii_processed` is cleared so a later tag can still propagate). A SQL
attribute that still reads another PII column keeps both the tag and
`pii_processed`. A user-edited SQL expression follows the same rule.

Each attribute is handled once, tracked by `column_attribute.pii_processed` and
`sql_attribute.pii_processed` (like `catalog_column.pii_processed`). Once an
attribute has been handled, removing its `PII` tag by hand is permanent: it is
not re-added on the next pass. An attribute built from no PII column stays
unprocessed, so it is still tagged if one of its columns is classified later.

## LLM fallback

For uncertain columns, the ingestion integration uses the project's configured
LLM through `LlmPiiClassifier`:

```python
from auto_ontology.pii_detection import LlmPiiClassifier, PiiDetector

detector = PiiDetector(LlmPiiClassifier())
```

Do not send raw sample values to an external model unless the deployment's
privacy policy explicitly allows it. Column metadata is usually sufficient.
