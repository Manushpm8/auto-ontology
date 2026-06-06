"""Tests for semantic compilation BFS traversal and models."""

from __future__ import annotations

import heapq

from gsf.ontology.rigor.models import (
    Attribute,
    BusinessTerm,
    CoreOntology,
    DeltaOntology,
    ObjectProperty,
    ProposedBusinessTerm,
    Provenance,
)
from gsf.ontology.rigor.traversal import (
    QueueEntry,
    QueuePriority,
    TablesQueue,
    discover_tables_via_vdb,
)
from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels


def test_queue_priority_ordering() -> None:
    """Join telemetry outranks FK, which outranks VDB discovery."""
    entries = [
        QueueEntry(
            priority=int(QueuePriority.VDB), hop=1, table_id="3", table_name="c"
        ),
        QueueEntry(
            priority=int(QueuePriority.JOIN), hop=1, table_id="1", table_name="a"
        ),
        QueueEntry(priority=int(QueuePriority.FK), hop=1, table_id="2", table_name="b"),
    ]
    heapq.heapify(entries)
    order = [heapq.heappop(entries).table_name for _ in range(3)]
    assert order == ["a", "b", "c"]


def test_tables_queue_deduplicates() -> None:
    tables_by_name = {
        "orders": {"id": "t1", "name": "orders"},
        "customers": {"id": "t2", "name": "customers"},
    }
    queue = TablesQueue("db", "public", tables_by_name, join_edges=[])
    queue.push("orders", QueuePriority.FK, hop=0)
    queue.push("orders", QueuePriority.JOIN, hop=0)
    first = queue.pop()
    second = queue.pop()
    assert first is not None
    assert first.table_name == "orders"
    assert second is None


def test_core_ontology_deduplicates_attributes() -> None:
    ontology = CoreOntology()
    prov = Provenance(
        source_table="orders", source_column="amount", derivation="deterministic"
    )
    ontology.attributes.append(
        Attribute(
            name="amount",
            attribute_type="column",
            datatype="numeric",
            term_name="Order",
            source_column="amount",
            provenance=prov,
        )
    )
    assert ontology.has_attribute("Order", "amount")


def test_merge_reuses_existing_term() -> None:
    ontology = CoreOntology()
    ontology.business_terms.append(
        BusinessTerm(
            name="Customer",
            description="Existing customer term.",
            provenance=[],
        )
    )
    delta = DeltaOntology(
        business_terms=[
            ProposedBusinessTerm(
                name="Customer",
                description="Duplicate proposal.",
            )
        ]
    )
    ontology.merge(delta, "customers")
    assert len(ontology.business_terms) == 1


def test_role_edge_carries_path_metadata() -> None:
    op = ObjectProperty(
        name="hasCustomer",
        source_term="Order",
        target_term="Customer",
        relation_kind="role",
        provenance=Provenance(
            source_table="orders",
            target_table="customers",
            derivation="declared_fk",
        ),
        path_data_layer=[
            {"node": "orders", "type": "Table"},
            {"node": "customers", "type": "Table"},
        ],
    )
    assert op.path_data_layer is not None
    assert op.path_data_layer[0]["node"] == "orders"


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
        database_name="dor_prod",
        retriever=FakeRetriever(),
        known_table_names={"observers"},
    )
    assert found == ["orders"]
