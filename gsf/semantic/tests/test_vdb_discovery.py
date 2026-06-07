"""Tests for VDB table discovery."""

from __future__ import annotations

from gsf.semantic.models import BusinessQuestionItem
from gsf.semantic.vdb_discovery import (
    _build_table_discovery_where,
    discover_tables_via_vdb,
)
from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels


def test_build_table_discovery_where_uses_nemo_dict_format() -> None:
    class FakeVdb:
        metadata_filter_format = "dict"
        database_name = "dor_prod"

    class FakeRetriever:
        vdb_kwargs = {"vdb": FakeVdb()}

    assert _build_table_discovery_where(FakeRetriever()) == {
        "label": Labels.TABLE,
        "database_name": "dor_prod",
    }


def test_discover_tables_via_vdb_uses_query_hits() -> None:
    captured: dict[str, object] = {}

    class FakeVdb:
        metadata_filter_format = "dict"
        database_name = "dor_prod"

    class FakeRetriever:
        vdb_kwargs = {"vdb": FakeVdb()}

        def query(
            self,
            query: str,
            top_k: int = 5,
            **kwargs: object,
        ) -> list[dict[str, object]]:
            captured["query"] = query
            captured["vdb_kwargs"] = kwargs.get("vdb_kwargs")
            return [
                {
                    "text": "orders table",
                    "metadata": {
                        "content_metadata": {
                            "label": Labels.TABLE,
                            "name": "orders",
                        }
                    },
                }
            ]

    found = discover_tables_via_vdb(
        items=[
            BusinessQuestionItem(
                question="Who ordered?", entity="Customer", role="placedBy"
            )
        ],
        retriever=FakeRetriever(),
    )
    assert found == ["orders"]
    assert captured["query"] == "Customer"
    assert captured["vdb_kwargs"] == {
        "where": {"label": Labels.TABLE, "database_name": "dor_prod"},
    }


def test_metadata_from_hit_parses_json_string() -> None:
    from gsf.semantic.vdb_discovery import _metadata_from_hit

    hit = {
        "text": "orders table",
        "metadata": '{"label": "Table", "name": "orders"}',
    }
    meta = _metadata_from_hit(hit)
    assert meta["name"] == "orders"
