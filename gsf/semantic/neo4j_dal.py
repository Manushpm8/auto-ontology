"""Neo4j read/write for semantic entities — single source of truth, no in-memory graph."""

from __future__ import annotations

import json
import logging
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_TERM,
    REL_HAS_ATTRIBUTE,
    REL_IS_A,
    REL_PART_OF,
    REL_PROPERTY_OF,
    REL_REPRESENTS,
    REL_ROLE,
    SEMANTIC_SOURCE,
)

logger = logging.getLogger(__name__)

REVIEWED_SOURCE = "semantic"


def mark_table_reviewed(table_id: str) -> None:
    get_neo4j_conn().query_write(
        f"MATCH (t:{Labels.TABLE} {{id: $table_id}}) "
        "SET t.reviewed = true, t.reviewed_source = $source",
        {"table_id": table_id, "source": REVIEWED_SOURCE},
    )


def clear_reviewed_flags() -> None:
    get_neo4j_conn().query_write(
        f"MATCH (t:{Labels.TABLE}) SET t.reviewed = false REMOVE t.reviewed_source",
    )


def discover_unreviewed_tables() -> list[dict[str, Any]]:
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (t:{Labels.TABLE})
        WHERE coalesce(t.reviewed, false) = false
        RETURN t.id AS id, t.name AS name, t.description AS description
        ORDER BY t.name
        """
    )
    return rows


def table_has_term(table_id: str) -> bool:
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{REL_REPRESENTS}]->
              (term:{LABEL_TERM} {{source: $source}})
        RETURN term.name AS name LIMIT 1
        """,
        {"table_id": table_id, "source": SEMANTIC_SOURCE},
    )
    return bool(rows)


def get_term_for_table(table_id: str) -> str | None:
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{REL_REPRESENTS}]->
              (term:{LABEL_TERM} {{source: $source}})
        RETURN term.name AS name LIMIT 1
        """,
        {"table_id": table_id, "source": SEMANTIC_SOURCE},
    )
    return rows[0]["name"] if rows else None


def get_table_for_term(term_name: str) -> dict[str, str] | None:
    """Physical table mapped to a semantic Term (for ROLE path resolution)."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (t:{Labels.TABLE})-[:{REL_REPRESENTS}]->
              (term:{LABEL_TERM} {{name: $term_name, source: $source}})
        RETURN t.id AS id, t.name AS name
        LIMIT 1
        """,
        {"term_name": term_name, "source": SEMANTIC_SOURCE},
    )
    if not rows:
        return None
    return {"id": rows[0]["id"], "name": rows[0]["name"]}


def merge_term(name: str, description: str, table_id: str) -> None:
    get_neo4j_conn().query_write(
        f"""
        MATCH (t:{Labels.TABLE} {{id: $table_id}})
        MERGE (term:{LABEL_TERM} {{name: $name, source: $source}})
        SET term.description = $description
        MERGE (t)-[:{REL_REPRESENTS}]->(term)
        """,
        {
            "table_id": table_id,
            "name": name,
            "description": description,
            "source": SEMANTIC_SOURCE,
        },
    )


def merge_column_attribute(
    *,
    term_name: str,
    table_id: str,
    source_column: str,
    attr_name: str,
    datatype: str,
    description: str | None,
) -> None:
    get_neo4j_conn().query_write(
        f"""
        MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->
              (col:{Labels.COLUMN} {{name: $source_column}})
        MATCH (term:{LABEL_TERM} {{name: $term_name, source: $source}})
        MERGE (attr:{LABEL_COLUMN_ATTRIBUTE} {{
            name: $attr_name,
            source_column: $source_column,
            term_name: $term_name,
            source: $source
        }})
        SET attr.datatype = $datatype,
            attr.description = coalesce($description, attr.description)
        MERGE (col)-[:{REL_HAS_ATTRIBUTE}]->(attr)
        MERGE (attr)-[:{REL_PROPERTY_OF}]->(term)
        """,
        {
            "table_id": table_id,
            "source_column": source_column,
            "term_name": term_name,
            "attr_name": attr_name,
            "datatype": datatype,
            "description": description,
            "source": SEMANTIC_SOURCE,
        },
    )


def merge_is_a(child_term: str, parent_term: str) -> None:
    if child_term == parent_term:
        return
    get_neo4j_conn().query_write(
        f"""
        MATCH (child:{LABEL_TERM} {{name: $child, source: $source}})
        MATCH (parent:{LABEL_TERM} {{name: $parent, source: $source}})
        MERGE (child)-[:{REL_IS_A}]->(parent)
        """,
        {"child": child_term, "parent": parent_term, "source": SEMANTIC_SOURCE},
    )


def merge_part_of(child_term: str, parent_term: str) -> None:
    if child_term == parent_term:
        return
    get_neo4j_conn().query_write(
        f"""
        MATCH (child:{LABEL_TERM} {{name: $child, source: $source}})
        MATCH (parent:{LABEL_TERM} {{name: $parent, source: $source}})
        MERGE (child)-[:{REL_PART_OF}]->(parent)
        """,
        {"child": child_term, "parent": parent_term, "source": SEMANTIC_SOURCE},
    )


def merge_role_edge(
    *,
    source_term: str,
    target_term: str,
    role_name: str,
    join_path: list[dict[str, Any]] | None,
    source_table: str,
    target_table: str,
) -> None:
    get_neo4j_conn().query_write(
        f"""
        MATCH (src:{LABEL_TERM} {{name: $source_term, source: $source}})
        MATCH (tgt:{LABEL_TERM} {{name: $target_term, source: $source}})
        MERGE (src)-[r:{REL_ROLE} {{name: $role_name}}]->(tgt)
        SET r.source_table = $source_table,
            r.target_table = $target_table,
            r.join_path = $join_path
        """,
        {
            "source_term": source_term,
            "target_term": target_term,
            "role_name": role_name,
            "source_table": source_table,
            "target_table": target_table,
            "join_path": json.dumps(join_path or []),
            "source": SEMANTIC_SOURCE,
        },
    )


def list_orphan_tables() -> list[dict[str, Any]]:
    """Tables with no REPRESENTS link to a Term."""
    return get_neo4j_conn().query_read(
        f"""
        MATCH (t:{Labels.TABLE})
        WHERE NOT (t)-[:{REL_REPRESENTS}]->(:{LABEL_TERM} {{source: $source}})
        RETURN t.id AS id, t.name AS name
        ORDER BY t.name
        """,
        {"source": SEMANTIC_SOURCE},
    )


def fetch_neighbor_terms(table_id: str, limit: int = 10) -> list[str]:
    """Term names for FK-adjacent mapped tables (LLM context)."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->
              (src:{Labels.COLUMN})-[:{Edges.FOREIGN_KEY}]->
              (:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-
              (other:{Labels.TABLE})-[:{REL_REPRESENTS}]->
              (term:{LABEL_TERM} {{source: $source}})
        RETURN DISTINCT term.name AS name
        LIMIT $limit
        """,
        {"table_id": table_id, "source": SEMANTIC_SOURCE, "limit": limit},
    )
    return [r["name"] for r in rows]


def fetch_fk_role_pairs(table_id: str) -> list[dict[str, Any]]:
    """FK relationships for ROLE synthesis at finalize."""
    return get_neo4j_conn().query_read(
        f"""
        MATCH (src_table:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->
              (src_col:{Labels.COLUMN})-[:{Edges.FOREIGN_KEY}]->
              (tgt_col:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-
              (tgt_table:{Labels.TABLE})
        MATCH (src_table)-[:{REL_REPRESENTS}]->(src_term:{LABEL_TERM} {{source: $source}})
        MATCH (tgt_table)-[:{REL_REPRESENTS}]->(tgt_term:{LABEL_TERM} {{source: $source}})
        RETURN src_table.id AS source_table_id,
               tgt_table.id AS target_table_id,
               src_table.name AS source_table,
               tgt_table.name AS target_table,
               src_term.name AS source_term,
               tgt_term.name AS target_term,
               src_col.name AS source_column
        """,
        {"table_id": table_id, "source": SEMANTIC_SOURCE},
    )


def fetch_all_terms_and_attributes() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]]
]:
    """Scan all semantic nodes in Neo4j for embedding."""
    conn = get_neo4j_conn()
    params = {"source": SEMANTIC_SOURCE}
    terms = conn.query_read(
        f"""
        MATCH (t:{Labels.TABLE})-[:{REL_REPRESENTS}]->
              (term:{LABEL_TERM} {{source: $source}})
        RETURN DISTINCT term.name AS name, term.description AS description
        """,
        params,
    )
    attrs = conn.query_read(
        f"""
        MATCH (t:{Labels.TABLE})-[:{Edges.CONTAINS}]->
              (col:{Labels.COLUMN})-[:{REL_HAS_ATTRIBUTE}]->
              (attr:{LABEL_COLUMN_ATTRIBUTE} {{source: $source}})
        RETURN attr.name AS name,
               attr.description AS description,
               attr.term_name AS term_name,
               attr.source_column AS source_column,
               col.name AS column_name
        """,
        params,
    )
    return terms, attrs
