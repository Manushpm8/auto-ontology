"""Tests for VDB table discovery."""

from __future__ import annotations

from gsf.semantic.vdb_discovery import discover_tables_via_vdb
from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels


def test_discover_tables_via_vdb_uses_query_hits() -> None:
    class FakeRetriever:
        def query(self, query: str, top_k: int = 5) -> list[dict[str, object]]:
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
        entities=["Order"],
        retriever=FakeRetriever(),
    )
    assert found == ["orders"]


def test_metadata_from_hit_parses_json_string() -> None:
    from gsf.semantic.vdb_discovery import _metadata_from_hit

    hit = {
        "text": "orders table",
        "metadata": '{"label": "Table", "name": "orders"}',
    }
    meta = _metadata_from_hit(hit)
    assert meta["name"] == "orders"
