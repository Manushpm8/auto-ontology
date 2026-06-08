"""Tests for term extraction sanitization."""

from __future__ import annotations

from gsf.semantic.models import (
    ColumnAttributeSpec,
    RawTableTermsResult,
    RawTermProposal,
    TermColumnRef,
)
from gsf.semantic.term_extractor import _sanitize_result


def test_sanitize_assigns_orphan_specs_to_primary_term() -> None:
    specs = [
        ColumnAttributeSpec(
            source_column="amount",
            name="amount",
            datatype="numeric",
        ),
        ColumnAttributeSpec(
            source_column="status",
            name="status",
            datatype="text",
        ),
    ]
    raw = RawTableTermsResult(
        terms=[
            RawTermProposal(
                name="PurchaseOrder",
                description="A purchase order",
                attributes=[TermColumnRef(source_column="amount")],
            )
        ]
    )

    result = _sanitize_result(raw, table={"name": "purchase_orders"}, specs=specs)

    assert len(result.terms) == 1
    assigned = {a.source_column: a.display_name for a in result.terms[0].attributes}
    assert assigned == {"amount": "amount", "status": "status"}
