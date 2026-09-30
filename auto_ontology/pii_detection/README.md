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

For uncertain columns, the ingestion integration uses the project's configured
LLM through `LlmPiiClassifier`:

```python
from auto_ontology.pii_detection import LlmPiiClassifier, PiiDetector

detector = PiiDetector(LlmPiiClassifier())
```

Do not send raw sample values to an external model unless the deployment's
privacy policy explicitly allows it. Column metadata is usually sufficient.
